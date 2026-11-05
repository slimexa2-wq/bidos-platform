@echo off
REM ============================================================
REM  BidOS 进程管理（Windows 10/11）
REM
REM    run_bidos.cmd start | stop | restart | status | logs
REM
REM  编码约定（改文件前必读）：
REM    1. 本文件必须存为 UTF-8（无 BOM）+ CRLF 换行。
REM       - 无 BOM：cmd 会把 BOM 当命令读，报 "'xxx' 不是内部或外部命令"。
REM       - CRLF：纯 LF 换行会让 goto/label/括号块解析不稳定。
REM    2. 第 2 行 chcp 65001 让 cmd 按 UTF-8 解读后续中文，
REM       避免中文系统按 GBK 读 UTF-8 产生乱码或吞掉后续字符。
REM ============================================================
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

REM 用 pushd 而不是 cd /d：脚本放在网络共享(UNC)时 cd /d 会失败，
REM 失败后 %CD% 还是原目录，后面所有 PYTHONPATH 都会指错地方。
pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
    echo [错误] 无法进入脚本目录：%~dp0
    exit /b 1
)

set "ROOT=%CD%"
set "VENV=%ROOT%\.venv311"
set "PY=%VENV%\Scripts\python.exe"
set "WEB_DIR=%ROOT%\apps\web"
set "WEB_PORT=5173"
set "API_PORT=8000"
set "DB_PORT=55432"
set "LAN_HOST=0.0.0.0"
set "RUN_DIR=%ROOT%\data\run"
set "LOG_API=%TEMP%\bidos_api.log"
set "LOG_WEB=%TEMP%\bidos_web.log"
set "LOG_SYNC=%TEMP%\bidos_nas_sync.out"

REM ---- 允许用环境变量覆盖（install.ps1 会注入这几个变量）----
if not "%BIDOS_WEB_PORT%"==""       set "WEB_PORT=%BIDOS_WEB_PORT%"
if not "%BIDOS_API_PORT%"==""       set "API_PORT=%BIDOS_API_PORT%"
if not "%BIDOS_DB_PORT%"==""        set "DB_PORT=%BIDOS_DB_PORT%"
if not "%BIDOS_LAN_HOST%"==""       set "LAN_HOST=%BIDOS_LAN_HOST%"

REM ---- 目录 ----
if not exist "%RUN_DIR%" mkdir "%RUN_DIR%" >nul 2>&1
if not exist "%ROOT%\data" mkdir "%ROOT%\data" >nul 2>&1

REM ---- 读取 install.ps1 落盘的配置 ----
REM 开机自启走计划任务，拿不到 install.ps1 那个 PowerShell 进程里的
REM 环境变量（共享盘路径、端口都在这里）。所以配置必须写进文件。
if exist "%RUN_DIR%\bidos_env.cmd" call "%RUN_DIR%\bidos_env.cmd"

if not exist "%PY%" (
    echo [错误] 缺少 Python 环境：%PY%
    echo        请先运行 install.ps1 完成部署。
    exit /b 1
)

REM ---- 默认值（bidos_env.cmd / 环境变量已在上方覆盖过）----
if "%DATABASE_URL%"=="" set "DATABASE_URL=postgresql://bidos:bidos_dev_only@127.0.0.1:%DB_PORT%/bidos"
if "%WORKBOOK_DIR%"=="" set "WORKBOOK_DIR=%USERPROFILE%\Desktop\AI小程序收集汇总资料"
if "%TZ%"=="" set "TZ=Asia/Shanghai"

REM ---- 固定环境变量 ----
set "PYTHONPATH=%ROOT%\apps\api"
set "BIDOS_DATA_MODE=postgres"
set "BIDOS_ORGANIZATION_ID=org_xiangneng"
set "BIDOS_SCAN_CRON=*/30 * * * *"
set "BIDOS_SCHEDULER_ENABLED=1"
set "BIDOS_SCHEDULER_TZ=Asia/Shanghai"
set "PYTHONUNBUFFERED=1"
REM Windows 控制台默认是 GBK/cp437，Python 往日志写中文会抛
REM UnicodeEncodeError 直接把进程打挂。强制 UTF-8 输出。
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM ---- 找 node ----
set "NODE="
for %%N in (node.exe) do if not "%%~$PATH:N"=="" set "NODE=%%~$PATH:N"
if not "%NODE%"=="" goto :node_ready
if exist "%ProgramFiles%\nodejs\node.exe" set "NODE=%ProgramFiles%\nodejs\node.exe"
if not "%NODE%"=="" goto :node_ready
if exist "%ProgramFiles(x86)%\nodejs\node.exe" set "NODE=%ProgramFiles(x86)%\nodejs\node.exe"
:node_ready

set "CMD=%~1"
if "%CMD%"=="" set "CMD=status"

if /i "%CMD%"=="start"   goto DO_START
if /i "%CMD%"=="stop"    goto DO_STOP
if /i "%CMD%"=="restart" goto DO_RESTART
if /i "%CMD%"=="status"  goto DO_STATUS
if /i "%CMD%"=="logs"    goto DO_LOGS
echo 用法: run_bidos.cmd {start^|stop^|restart^|status^|logs}
exit /b 1

REM ============================================================
:DO_START
echo [启动] BidOS ...
type nul > "%LOG_API%"
type nul > "%LOG_WEB%"
type nul > "%LOG_SYNC%"

REM ---- 等数据库：开机自启时 Docker Desktop 可能还没起来 ----
call :WAIT_DB 120
if errorlevel 1 (
    echo [错误] 等了 %DB_PORT% 端口的数据库 %~1 秒还没起来。
    echo        请先打开 Docker Desktop，确认容器 bidos-pg 在跑：
    echo        docker ps -a    docker logs bidos-pg
    exit /b 1
)
echo [就绪] 数据库端口 %DB_PORT% 已响应

REM ---- 清掉本项目占用的旧端口进程 ----
call :KILL_PORT %WEB_PORT%
call :KILL_PORT %API_PORT%

REM ---- 后端：直接起 uvicorn ----
REM 不要调用 scripts\desktop_api.py：那个脚本是 macOS 专用的，
REM 内部写死了 .venv311\bin\python、/Applications/OrbStack.app、
REM os.execv，在 Windows 上必然退出码 1。
REM 只监听 127.0.0.1：前端 vite 已经把 /api 代理到本机 8000，
REM 局域网访问走 5173 即可，没必要把无鉴权的 API 暴露出去。
start "BidOS API" /min cmd /c ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT% --app-dir "%ROOT%\apps\api" >> "%LOG_API%" 2>&1"
echo [启动] api  端口 %API_PORT% (日志 %LOG_API%)

REM ---- 前端 ----
if "%NODE%"=="" (
    echo [跳过] web  找不到 node.exe，请先运行 install.ps1
) else if not exist "%WEB_DIR%\node_modules\vite\bin\vite.js" (
    echo [跳过] web  前端依赖缺失，请先运行 install.ps1
) else (
    start "BidOS Web" /min cmd /c ""%NODE%" "%WEB_DIR%\node_modules\vite\bin\vite.js" --port %WEB_PORT% --host %LAN_HOST% --strictPort >> "%LOG_WEB%" 2>&1"
    echo [启动] web  端口 %WEB_PORT% (日志 %LOG_WEB%)
)

REM ---- 同步看护 ----
REM nas_sync.py 在模块级 import fcntl，Windows 没有这个模块，
REM 直接跑必然 ImportError。先探测一次，不通就跳过并留下明确日志，
REM 免得每 60 秒刷一次看不懂的崩溃。
"%PY%" -c "import fcntl" >nul 2>&1
if errorlevel 1 (
    echo [跳过] sync  scripts\nas_sync.py 依赖 Unix 模块 fcntl，Windows 上无法运行
    > "%LOG_SYNC%" echo [bidos] sync 已跳过：nas_sync.py 需要 Unix 的 fcntl 模块，Windows 暂不支持。
) else (
    if exist "%RUN_DIR%\sync_loop.cmd" del /q "%RUN_DIR%\sync_loop.cmd" >nul 2>&1
    > "%RUN_DIR%\sync_loop.cmd" echo @echo off
    >>"%RUN_DIR%\sync_loop.cmd" echo :loop
    >>"%RUN_DIR%\sync_loop.cmd" echo "%PY%" "%ROOT%\scripts\nas_sync.py" ^>^> "%LOG_SYNC%" 2^>^&1
    >>"%RUN_DIR%\sync_loop.cmd" echo ping -n 61 127.0.0.1 ^>nul
    >>"%RUN_DIR%\sync_loop.cmd" echo goto loop
    REM 注意：cmd /c 后面不能直接跟括号，cmd /c ("x.cmd") 会被当成
    REM 字面量报"系统找不到指定的路径"。必须用 call。
    start "BidOS Sync" /min cmd /c call "%RUN_DIR%\sync_loop.cmd"
    echo [启动] sync 每 60 秒同步一次 (日志 %LOG_SYNC%)
)

echo [等待] 服务就绪，首轮同步大约 40 秒 ...
call :WAIT_PORT %API_PORT% 90
if errorlevel 1 (
    echo [警告] api 90 秒内没监听 %API_PORT%，请看 %LOG_API%
) else (
    call :SAVE_PID api %API_PORT%
)
call :WAIT_PORT %WEB_PORT% 60
if errorlevel 1 (
    echo [警告] web 60 秒内没监听 %WEB_PORT%，请看 %LOG_WEB%
) else (
    call :SAVE_PID web %WEB_PORT%
)
echo [完成]
goto DO_STATUS

REM ============================================================
:DO_RESTART
call :DO_STOP
goto DO_START

REM ============================================================
:DO_STOP
echo [停止] BidOS ...
REM 端口进程由 :KILL_PORT 负责；这里只补杀同步看护（它不占端口）。
taskkill /FI "WINDOWTITLE eq BidOS Sync*" /T /F >nul 2>&1
if exist "%RUN_DIR%\sync_loop.cmd" del /q "%RUN_DIR%\sync_loop.cmd" >nul 2>&1
call :KILL_PORT %WEB_PORT%
call :KILL_PORT %API_PORT%
if exist "%RUN_DIR%\api.pid" del /q "%RUN_DIR%\api.pid" >nul 2>&1
if exist "%RUN_DIR%\web.pid" del /q "%RUN_DIR%\web.pid" >nul 2>&1
echo [停止] 完成
exit /b 0

REM ============================================================
:DO_STATUS
echo BidOS 运行状态
call :SHOW_PID api
call :SHOW_PID web
call :CHECK_URL api "http://127.0.0.1:%API_PORT%/api/blind/workspace"
call :CHECK_URL web "http://127.0.0.1:%WEB_PORT%/"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-NetIPConfiguration -ErrorAction SilentlyContinue ^| Where-Object { $_.IPv4DefaultGateway -ne $null } ^| Select-Object -First 1 ^| ForEach-Object { $_.IPv4Address.IPAddress }" 2^>nul`) do set "LANIP=%%I"
if defined LANIP (
    if "%LAN_HOST%"=="0.0.0.0" call :CHECK_URL lan "http://%LANIP%:%WEB_PORT%/"
)
if exist "%ROOT%\data\blind_workspace_state.json" (
    echo   同步  状态文件 %ROOT%\data\blind_workspace_state.json
) else (
    echo   同步  尚未同步过
)
if exist "%LOG_SYNC%" (
    for /f "tokens=1,2 delims==" %%A in ('findstr /b /c:"up_to_date" /c:"imported_n" /c:"skip_locked" /c:"已跳过" "%LOG_SYNC%" 2^>nul') do echo   同步  %%B
)
exit /b 0

REM ============================================================
:DO_LOGS
echo 后端日志 %LOG_API%（Ctrl+C 退出）
powershell -NoProfile -Command "Get-Content -Path '%LOG_API%' -Wait -Tail 30"
exit /b 0

REM ============================================================
REM  子程序
REM ============================================================

REM :WAIT_DB <秒>  等数据库端口可连接
:WAIT_DB
set /a _d=0
:WAIT_DB_LOOP
powershell -NoProfile -Command "if (New-Object Net.Sockets.TcpClient('127.0.0.1', %DB_PORT%)) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _d+=1
if %_d% ggeq %~1 exit /b 1
ping -n 2 127.0.0.1 >nul 2>&1
goto WAIT_DB_LOOP

REM :WAIT_PORT <端口> <秒>  等端口进入 LISTEN
REM 用 Get-NetTCPConnection 而不是 netstat|findstr "LISTENING"：
REM 中文 Windows 上 netstat 的状态列是「侦听」不是 LISTENING，
REM 用英文关键字过滤会永远匹配不到。
:WAIT_PORT
set /a _n=0
:WAIT_LOOP
powershell -NoProfile -Command "if (Get-NetTCPConnection -State Listen -LocalPort %~1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _n+=1
if %_n% ggeq %~2 exit /b 1
REM 用 ping 不用 timeout：timeout 在 stdin 被重定向/计划任务
REM 非交互会话下会报「输入重定向不受支持」并立刻返回。
ping -n 2 127.0.0.1 >nul 2>&1
goto WAIT_LOOP

REM :KILL_PORT <端口>
REM 只杀「监听该端口 且 命令行里包含本项目路径」的进程，
REM 避免误杀同事电脑上恰好也用 5173/8000 的其它程序。
:KILL_PORT
powershell -NoProfile -Command "$port=[int]'%~1'; $root=[Environment]::CurrentDirectory; Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | ForEach-Object { $target=$_.OwningProcess; $proc=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $target) -ErrorAction SilentlyContinue; if ($proc -and $proc.CommandLine -and $proc.CommandLine.Contains($root)) { Write-Host ('  kill port ' + $port + ' pid=' + $target); Stop-Process -Id $target -Force -ErrorAction SilentlyContinue } else { Write-Host ('  skip  port ' + $port + ' pid=' + $target + ' (非本项目进程，未杀)') } }" >nul 2>&1
goto :eof

REM :SAVE_PID <名字> <端口>  记录 PID，供 status 判断存活
:SAVE_PID
set "PIDFILE=%RUN_DIR%\%~1.pid"
powershell -NoProfile -Command "$c=Get-NetTCPConnection -State Listen -LocalPort %~2 -ErrorAction SilentlyContinue; if ($c) { Write-Output $c[0].OwningProcess }" > "%PIDFILE%" 2>nul
for %%A in ("%PIDFILE%") do if %%~zA LEQ 2 del /q "%PIDFILE%" >nul 2>&1
goto :eof

REM :SHOW_PID <名字>  按 PID 文件判断进程是否还活着
:SHOW_PID
set "PIDFILE=%RUN_DIR%\%~1.pid"
if not exist "%PIDFILE%" (
    echo   %~1  未运行
    goto :eof
)
set "PIDVAL="
for /f "usebackq delims=" %%P in ("%PIDFILE%") do set "PIDVAL=%%P"
if not defined PIDVAL (
    echo   %~1  未运行
    goto :eof
)
tasklist /FI "PID eq %PIDVAL%" /NH 2>nul | findstr /b /c:"%PIDVAL%" >nul 2>&1
if errorlevel 1 (
    echo   %~1  未运行 ^(PID %PIDVAL% 已不存在^)
) else (
    echo   %~1  运行中 ^(PID %PIDVAL%^)
)
goto :eof

REM :CHECK_URL <名字> <URL>
:CHECK_URL
set "CODE="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri '%~2' -TimeoutSec 10 -UseBasicParsing; Write-Output $r.StatusCode } catch { Write-Output 0 }" 2^>nul`) do set "CODE=%%A"
if "%CODE%"=="200" (
    echo   %~1  就绪  %~2
) else (
    echo   %~1  未就绪  %~2
)
goto :eof

popd
endlocal
exit /b 0