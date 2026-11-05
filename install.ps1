﻿<#
.SYNOPSIS
    BidOS 招投标智能工作平台 · Windows 一键部署

.DESCRIPTION
    双击本文件即可运行（首次运行需要「以管理员身份」）。
    做的事：检查环境 → 装 Python/Node 依赖 → 起数据库 → 恢复数据
            → 注册开机自启 → 配置局域网访问 → 验证。

.EXAMPLE
    右键本文件 →「以管理员身份运行」
    或在 PowerShell 里：  powershell -ExecutionPolicy Bypass -File install.ps1
#>

# 本文件必须保存为「UTF-8 带 BOM」+ CRLF 换行。
#   - 无 BOM：Windows PowerShell 5.1 会按 GBK 读本文件，所有中文变乱码，
#             且个别汉字的 GBK 码位可能破坏引号配对，直接语法报错。
#   - CRLF   ：纯 LF 换行在 5.1 下同样可能解析异常。
# 用 PowerShell 7 运行时不受 BOM 影响，但加上更保险。

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 输出样式
function Step ($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "  [X]  $m" -ForegroundColor Red; Write-Host ''; exit 1 }

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ApiPort = 8000
$WebPort = 5173
$DbPort  = 55432
$LanHost = '0.0.0.0'          # 允许局域网访问
$DbName  = 'bidos'; $DbUser = 'bidos'; $DbPass = 'bidos_dev_only'
$Venv    = Join-Path $Root '.venv311'
$RunDir  = Join-Path $Root 'data\run'

# 参数：--no-lan / --port=xxxx
foreach ($a in $args) {
  if ($a -eq '--no-lan') { $LanHost = '127.0.0.1' }
  if ($a -like '--port=*') { $WebPort = [int]($a -replace '--port=', '') }
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host '  BidOS 招投标智能工作平台 . Windows 部署' -ForegroundColor DarkCyan
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host "  项目目录：$Root"
if ($LanHost -eq '0.0.0.0') {
  Write-Host ("  局域网访问：开启（同事可用 http://<本机IP>:{0} 访问）" -f $WebPort)
} else {
  Write-Host '  局域网访问：关闭（仅本机）'
}
Write-Host ''

# ---------------------------------------------------------------- 1. Python
Step '1/7 检查运行环境'

# 用「命令 + 前置参数」两个字段表示候选解释器。
# 不能把 'py -3.11' 存成一个字符串再用 & 调用：& 后面跟一个带空格的
# 字符串时，PowerShell 会把它当成「一个叫 "py -3.11" 的可执行文件」，
# 报「无法将...项识别为 cmdlet」。必须拆成 (& 'py' '-3.11')。
$PyCmd = $null
$PyPre = @()

function Test-Interpreter ([string]$exe, [string[]]$pre) {
  try {
    $v = & $exe @pre -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    if ("$v".Trim() -in @('3.11', '3.12')) { return $true }
  } catch {}
  return $false
}

$cands = @()
$pyCmd = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
if ($pyCmd) { $cands += ,@($pyCmd.Source, @()) }
$cands += ,@($null, @('-3.11'))
$pyAny = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
if ($pyAny) { $cands += ,@($pyAny.Source, @()) }

foreach ($c in $cands) {
  $exe = $c[0]; $pre = $c[1]
  if (-not $exe) {
    # 没有 py.exe 就没有 'py -3.11' 这个 launcher 形式，跳过
    if ($pre.Count -eq 0) { continue }
    if (-not (Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue)) { continue }
    $exe = (Get-Command py.exe -CommandType Application).Source
  }
  if (Test-Interpreter $exe $pre) { $PyCmd = $exe; $PyPre = $pre; break }
}
if (-not $PyCmd) {
  Die "没找到 Python 3.11 或 3.12。`n请先安装：https://www.python.org/downloads/windows/`n安装时务必勾选「Add python.exe to PATH」，然后重新运行本脚本。"
}
Ok ("Python：{0} {1}" -f $PyCmd, ($PyPre -join ' '))

# ---------------------------------------------------------------- Node
$Node = $null
$c = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
if (-not $c) { $c = Get-Command node -CommandType Application -ErrorAction SilentlyContinue }
if ($c) {
  try {
    $maj = [int](& $c.Source -p "process.versions.node.split('.')[0]" 2>$null)
    if ($maj -ge 18) { $Node = $c.Source }
  } catch {}
}
if (-not $Node) {
  Die "没找到 Node.js 18 或更高版本。`n请先安装：https://nodejs.org/ （选 LTS 版本，一路下一步即可）"
}
Ok ("Node：{0} (v{1})" -f $Node, (& $c.Source -v))

# ---------------------------------------------------------------- Docker
$c = Get-Command docker.exe -CommandType Application -ErrorAction SilentlyContinue
if (-not $c) { $c = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue }
if (-not $c) {
  Die "没找到 Docker。`n请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/`n安装后需启动 Docker Desktop，等左下角图标变绿再运行本脚本。"
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) { Die 'Docker 没有在运行。请打开 Docker Desktop，等它启动完成后再运行本脚本。' }
Ok 'Docker：已就绪'

# ---------------------------------------------------------------- 局域网IP
# 注意：不能用 Sort-Object InterfaceMetric —— Get-NetIPAddress 没有
# InterfaceMetric 这个属性（那是 Get-NetIPConfiguration 的），
# 那样排不了序，会随机取到 VPN / 虚拟网卡地址。
$LanIP = $null
try {
  $LanIP = (Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
    Sort-Object { $_.NetAdapter.InterfaceMetric } |
    Select-Object -First 1).IPv4Address.IPAddress
} catch {}
if ($LanIP) { Ok "本机局域网IP：$LanIP" } else { Warn '没探测到局域网 IP，稍后可用 ipconfig 查看' }

# ---------------------------------------------------------------- 共享盘
$WorkbookDir = $null
$shareCandidates = @(
  '\\192.168.1.228\市场共享文件\AI小程序收集汇总资料',
  'Z:\AI小程序收集汇总资料',
  'Y:\AI小程序收集汇总资料',
  "$env:USERPROFILE\Desktop\AI小程序收集汇总资料",
  "$env:USERPROFILE\OneDrive\Desktop\AI小程序收集汇总资料"
)
foreach ($p in $shareCandidates) {
  if (Test-Path -LiteralPath $p) { $WorkbookDir = $p; break }
}
if (-not $WorkbookDir) {
  Warn '没找到工作正本目录（共享盘未映射 / 桌面也没有）'
  Warn "  系统仍可启动，但同步会报错。请先映射共享盘到某个盘符，或把表放到：$env:USERPROFILE\Desktop\AI小程序收集汇总资料"
  $WorkbookDir = "$env:USERPROFILE\Desktop\AI小程序收集汇总资料"
} else {
  Ok "工作正本：$WorkbookDir"
}

# ---------------------------------------------------------------- Python 环境
Step '2/7 准备 Python 环境'

# 仓库里的 .venv311 是 macOS 版本（只有 bin/，没有 Scripts/）。
# 必须检测 Windows 布局，不存在才重建；否则会在 mac 的 venv 上叠加安装。
if (Test-Path (Join-Path $Venv 'Scripts\python.exe')) {
  Ok '已存在 Windows 虚拟环境，跳过创建'
} else {
  if (Test-Path $Venv) {
    Warn "发现非 Windows 结构的虚拟环境（macOS 的 .venv311），正在重建…"
    Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
  }
  & $PyCmd @PyPre -m venv $Venv
  if ($LASTEXITCODE -ne 0) { Die '创建虚拟环境失败' }
  Ok '已创建虚拟环境'
}
$Vpy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Vpy)) { Die "虚拟环境里找不到 $Vpy，虚拟环境已损坏，请删掉 .venv311 重跑本脚本。" }

& $Vpy -m pip install --disable-pip-version-check --quiet --upgrade pip 2>$null

# requirements.lock.txt 是在 macOS 上 pip freeze 出来的，里面有
# pyobjc-core / pyobjc-framework-Cocoa / uvloop / screeninfo / camoufox，
# 这些在 Windows 上没有 wheel，pip 会直接报「No matching distribution」
# 并中断整批安装。必须在装之前过滤掉。
$LockFile = Join-Path $Root 'requirements.lock.txt'
$WinReq   = Join-Path $RunDir 'requirements.windows.txt'
$BlockList = @(
  'pyobjc-core','pyobjc-framework-cocoa','uvloop','screeninfo','camoufox',
  'inquirer','readchar','blessed','runs','rich','rich-click',
  'apify_fingerprint_datapoints','browserforge'
)
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
if (Test-Path $LockFile) {
  $kept = @()
  foreach ($line in (Get-Content -LiteralPath $LockFile -Encoding UTF8)) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#')) { continue }
    $name = ($t -split '[=<>!~\[]')[0].Trim().ToLower()
    if ($BlockList -contains $name) { continue }
    $kept += $t
  }
  # tzdata 必须显式装：Windows 没有系统时区库，
  # 缺它的话 ZoneInfo('Asia/Shanghai') 会抛 ZoneInfoNotFoundError，
  # 调度器会静默降级（事件扫描再也不跑）。
  $kept += 'tzdata'
  $kept | Set-Content -LiteralPath $WinReq -Encoding UTF8
  Ok ("已生成 Windows 依赖清单：过滤掉 {0} 个 macOS 专有包，并补入 tzdata" -f $BlockList.Count)
}

& $Vpy -c "import fastapi, psycopg, sqlalchemy, openpyxl, apscheduler, tzdata" 2>$null
if ($LASTEXITCODE -ne 0) {
  Ok '安装 Python 依赖（首次约 2-5 分钟，请耐心等待）…'
  $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', $WinReq)
  & $Vpy @pipArgs
  $rc = $LASTEXITCODE
  # 只有「网络类失败」才退到国内镜像重试。macOS 专有包已经在上面
  # 过滤掉了，所以这里的失败基本就是网络问题，用镜像兜底是合理的。
  if ($rc -ne 0) {
    Warn '默认源安装失败，改用清华镜像重试…'
    & $Vpy @pipArgs '-i' 'https://pypi.tuna.tsinghua.edu.cn/simple'
    if ($LASTEXITCODE -ne 0) {
      Die '依赖安装失败。请检查网络；如仍失败，可手动执行：' + "`n  `"$Vpy`" -m pip install -r `"$WinReq`" -i https://pypi.tuna.tsinghua.edu.cn/simple"
    }
  }
  # 装完必须复检：pip 返回 0 也可能没装全（某些包静默跳过）
  & $Vpy -c "import fastapi, psycopg, sqlalchemy, openpyxl, apscheduler, tzdata" 2>$null
  if ($LASTEXITCODE -ne 0) { Die '依赖装完仍无法 import，请把上面的报错发出来。' }
  Ok '依赖安装完成'
} else { Ok '依赖已就绪' }

# ---------------------------------------------------------------- 前端依赖
Step '3/7 准备前端依赖'
$WebDir = Join-Path $Root 'apps\web'
# 仓库里的 apps\web\node_modules 是 macOS 装的，里面是
# @esbuild/darwin-arm64、@rollup/rollup-darwin-arm64。
# 拷到 Windows 后 vite 会因为找不到 win32 二进制而启动失败，
# 所以不能只看 node_modules\vite 存不存在，要看平台包。
$needNpm = $true
if (Test-Path (Join-Path $WebDir 'node_modules\vite')) {
  if (Test-Path (Join-Path $WebDir 'node_modules\@esbuild\win32-x64')) {
    $needNpm = $false
  } else {
    Warn '检测到非 Windows 的前端依赖（macOS 的 node_modules），将重新安装…'
    Remove-Item -LiteralPath (Join-Path $WebDir 'node_modules') -Recurse -Force -ErrorAction SilentlyContinue
  }
}
if (-not $needNpm) {
  Ok '前端依赖已就绪'
} else {
  Ok '安装前端依赖（首次约 2-5 分钟）…'
  Push-Location $WebDir
  # 不要用 node ..\..\node_modules\npm\bin\npm-cli.js：
  # 仓库根目录根本没有 node_modules（已确认），这条命令必然失败。
  $npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
  if (-not $npm) { $npm = Get-Command npm -CommandType Application -ErrorAction SilentlyContinue }
  if (-not $npm) {
    Pop-Location
    Die '找不到 npm。Node.js 应自带 npm，请重新安装 Node.js。'
  }
  & $npm.Source install --no-audit --no-fund
  $npmRc = $LASTEXITCODE
  Pop-Location
  if ($npmRc -ne 0) { Die "npm install 失败（退出码 $npmRc）。请在项目目录手动执行 npm install 看报错。" }
  if (-not (Test-Path (Join-Path $WebDir 'node_modules\vite\bin\vite.js'))) {
    Die '前端依赖安装完成但找不到 vite，请检查 npm 报错。'
  }
  if (-not (Test-Path (Join-Path $WebDir 'node_modules\@esbuild\win32-x64'))) {
    Die '前端依赖装好了但缺少 Windows 版 esbuild（node_modules\@esbuild\win32-x64）。请删除 node_modules 后重装。'
  }
  Ok '前端依赖安装完成'
}

# ---------------------------------------------------------------- 数据库
Step '4/7 启动数据库并恢复数据'
$existing = & docker ps --format '{{.Names}}' 2>$null
if ($existing -contains 'bidos-pg') {
  Ok 'PostgreSQL 容器已在运行'
} else {
  Warn '启动 PostgreSQL 容器…'
  & docker rm -f bidos-pg *> $null
  & docker run -d --name bidos-pg `
    -e POSTGRES_DB=$DbName -e POSTGRES_USER=$DbUser -e POSTGRES_PASSWORD=$DbPass `
    -p "${DbPort}:5432" -v bidos_pg_data:/var/lib/postgresql/data `
    pgvector/pgvector:pg16 *> $null
  if ($LASTEXITCODE -ne 0) { Die 'PostgreSQL 容器启动失败。执行 docker logs bidos-pg 查看原因。' }
  Ok '容器已创建'
}
Ok '等待数据库就绪…'
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
  & docker exec bidos-pg pg_isready -U $DbUser -d $DbName *> $null
  if ($LASTEXITCODE -eq 0) { $ready = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $ready) { Die '数据库 40 秒内没就绪。执行 docker logs bidos-pg 查看原因。' }
Ok '数据库就绪'

$DbUrl = "postgresql://${DbUser}:${DbPass}@127.0.0.1:${DbPort}/${DbName}"

# 用单行 -c 而不是 here-string：here-string 里含 $DbUrl 会被展开，
# 这点没错，但一旦 python 报错，PowerShell 会把整段 stderr 当异常抛出
# （$ErrorActionPreference='Stop'），脚本直接崩在这里看不出原因。
# 显式 try/catch 才能给出可读提示。
$pyProbe = @'
import sys
try:
    import psycopg
    with psycopg.connect(sys.argv[1], connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FROM projects")
            print(cur.fetchone()[0])
except Exception as e:
    print("ERR:" + str(e)); sys.exit(2)
'@
$rowCount = & $Vpy -c $pyProbe $DbUrl 2>&1
$rc = $LASTEXITCODE
if ($rc -eq 0 -and "$rowCount" -match '^\d+$') {
  if ([int]$rowCount -gt 0) {
    Ok "数据库已有 $rowCount 条项目，跳过恢复（不覆盖现有数据）"
  } else {
    # 表在但是空的 -> 仍然需要灌数据
    $needRestore = $true
  }
} elseif ("$rowCount" -match 'does not exist.*projects|no such table') {
  # 空库（首次部署，pg_restore 还没跑）——这正是需要恢复的情况
  $needRestore = $true
} else {
  Warn "探测 projects 表失败：$rowCount"
  $needRestore = $true
}

if ($needRestore) {
  $dump = Join-Path $Root 'data\bidos.dump'
  if (Test-Path $dump) {
    Warn '恢复数据（约 30 秒）…'
    & docker cp $dump 'bidos-pg:/tmp/bidos.dump' *> $null
    & docker exec bidos-pg pg_restore -U $DbUser -d $DbName --clean --if-exists --no-owner --no-privileges /tmp/bidos.dump *> $null
    if ($LASTEXITCODE -eq 0) { Ok '数据恢复完成' }
    else { Warn '恢复有告警，执行 docker exec bidos-pg pg_restore -l /tmp/bidos.dump 检查；多数情况可忽略' }
  } else {
    Warn "没找到 data\bidos.dump，将只建空库（系统能用，但没有历史数据）"
  }
}

# 校验租户：BIDOS_ORGANIZATION_ID 指向的租户不存在时，
# API 不会报错，但读侧按租户过滤后所有页面都是空的（静默失败）。
$orgCheck = & $Vpy -c @'
import sys
try:
    import psycopg
    with psycopg.connect(sys.argv[1], connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FROM organizations WHERE id = %s", (sys.argv[2],))
            print(cur.fetchone()[0])
except Exception as e:
    print("ERR:" + str(e)); sys.exit(2)
'@ $DbUrl 'org_xiangneng' 2>&1
if ("$orgCheck" -match '^\d+$' -and [int]$orgCheck -gt 0) {
  Ok '租户 org_xiangneng 存在'
} else {
  Warn "租户 org_xiangneng 不存在（探测结果：$orgCheck）。页面可能全是空数据，请检查 data\bidos.dump 是否正确。"
}

# ---------------------------------------------------------------- 落盘配置
Step '5/7 写入运行配置'
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\backups') | Out-Null

$envFile = Join-Path $RunDir 'bidos_env.cmd'
$envBody = @(
  '@echo off',
  'REM 由 install.ps1 生成，开机自启时由 run_bidos.cmd 读取。请勿手工编辑。',
  ('set "BIDOS_WEB_PORT={0}"' -f $WebPort),
  ('set "BIDOS_API_PORT={0}"' -f $ApiPort),
  ('set "BIDOS_DB_PORT={0}"' -f $DbPort),
  ('set "BIDOS_LAN_HOST={0}"' -f $LanHost),
  ('set "WORKBOOK_DIR={0}"' -f $WorkbookDir),
  ('set "DATABASE_URL=postgresql://{1}:{2}@127.0.0.1:{3}/{4}"' -f $DbName, $DbUser, $DbPass, $DbPort, $DbName)
) -join "`r`n"
# 必须写 UTF-8 带 BOM，否则 run_bidos.cmd 按 GBK 读会乱码。
[System.IO.File]::WriteAllText($envFile, $envBody, (New-Object System.Text.UTF8Encoding $true))
Ok "已写入 $envFile"

# ---------------------------------------------------------------- 启动服务
Step '6/7 启动服务'
# 直接调用 run_bidos.cmd，不要另起 powershell -NoExit：
#   - NoExit 会留一个窗口不关，用户以为卡住；
#   - 另起进程拿不到这里的 $env，配置也传不下去；
#   - run_bidos.cmd 内部已经带等待端口的就绪逻辑，直接跑能拿到真实退出码。
$env:BIDOS_WEB_PORT = "$WebPort"
$env:BIDOS_API_PORT = "$ApiPort"
$env:BIDOS_DB_PORT  = "$DbPort"
$env:BIDOS_LAN_HOST = $LanHost
$env:WORKBOOK_DIR   = $WorkbookDir
$env:DATABASE_URL   = $DbUrl

Push-Location $Root
& cmd.exe /c "`"$Root\run_bidos.cmd`" start"
$startRc = $LASTEXITCODE
Pop-Location
if ($startRc -ne 0) { Warn "run_bidos.cmd start 返回 $startRc，请查看上面的输出定位问题。" }
else { Ok '服务已启动' }

# ---------------------------------------------------------------- 开机自启
Step '6.5/7 配置开机自动启动'
$taskName = 'BidOS_AutoStart'
try {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
  $action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"`"$Root\run_bidos.cmd`" start`"" `
    -WorkingDirectory $Root
  # 延迟 1 分钟再启动：计划任务触发时 Docker Desktop 通常还没起来，
  # run_bidos.cmd 会等数据库，但给它一点启动时间更稳。
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $trigger.Delay = 'PT1M'
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
  # 用当前用户身份跑最高权限：docker 命令需要，普通权限会被拒。
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'BidOS 开机自动启动' -Force | Out-Null
  Ok '已注册开机计划任务（登录后 1 分钟自动启动）'
} catch {
  Warn "自动注册计划任务失败：$($_.Exception.Message)"
  Warn '请手动添加：任务计划程序 -> 创建基本任务 -> 触发器「登录时」-> 操作「启动程序」'
  Warn "  程序：$Root\run_bidos.cmd    参数：start"
}

# ---------------------------------------------------------------- 防火墙
Step '6.8/7 配置防火墙（局域网访问需要）'
if ($LanHost -eq '0.0.0.0' -and $LanIP) {
  foreach ($rule in @('BidOS-Web-In')) {
    Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue |
      Remove-NetFirewallRule -ErrorAction SilentlyContinue
  }
  try {
    # 只放行 5173：API(8000) 只监听 127.0.0.1，vite 会代理 /api，
    # 不需要也不应该把无鉴权的 API 直接暴露到局域网。
    New-NetFirewallRule -DisplayName 'BidOS-Web-In' -Direction Inbound `
      -Protocol TCP -LocalPort $WebPort -Action Allow -Profile Any | Out-Null
    Ok "已放行防火墙端口 $WebPort"
  } catch {
    Warn "自动配置防火墙失败：$($_.Exception.Message)"
    Warn "请手动放行：控制面板 -> Windows Defender 防火墙 -> 高级设置 -> 入站规则 -> 新建 -> TCP $WebPort"
  }
}

# ---------------------------------------------------------------- 验证
Step '7/7 验证服务'
$failed = $false
function Check ($name, $url) {
  try {
    $r = Invoke-WebRequest -Uri $url -TimeoutSec 15 -UseBasicParsing
    if ($r.StatusCode -eq 200) { Ok "$name 已就绪 ($url)" }
    else { Warn "$name 返回 HTTP $($r.StatusCode) <- $url"; $script:failed = $true }
  } catch {
    Warn "$name 未就绪 <- $url（$($_.Exception.Message)）"; $script:failed = $true
  }
}
Check '后端接口' "http://127.0.0.1:$ApiPort/api/blind/workspace"
Check '前端页面' "http://127.0.0.1:$WebPort/"
if ($LanHost -eq '0.0.0.0' -and $LanIP) {
  Check '局域网访问' "http://${LanIP}:$WebPort/"
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
if ($failed) { Write-Host '  部署完成，但有项目未就绪' -ForegroundColor Yellow }
else        { Write-Host '  部署完成' -ForegroundColor Green }
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host ''
Write-Host '  怎么用' -ForegroundColor Cyan
Write-Host "    本机访问：http://127.0.0.1:$WebPort"
if ($LanHost -eq '0.0.0.0' -and $LanIP) {
  Write-Host "    局域网访问：http://${LanIP}:$WebPort   <- 把这个地址发给同事" -ForegroundColor Cyan
}
Write-Host ''
Write-Host '  常用命令（在项目目录里）' -ForegroundColor Cyan
Write-Host '    run_bidos.cmd status    查看运行状态'
Write-Host '    run_bidos.cmd restart   重启'
Write-Host '    run_bidos.cmd stop      停止'
Write-Host ''
Write-Host '  出问题时看这里' -ForegroundColor Cyan
Write-Host "    前端打不开     -> type %TEMP%\bidos_web.log"
Write-Host "    接口报错       -> type %TEMP%\bidos_api.log"
Write-Host '    同步日志       -> type %TEMP%\bidos_nas_sync.out'
Write-Host '    改了配置       -> 改 data\run\bidos_env.cmd 后 run_bidos.cmd restart'
Write-Host ''
Write-Host '  注意：数据同步依赖 scripts\nas_sync.py，该脚本目前只能在 macOS 上运行'
Write-Host '  （它在模块级 import fcntl，Windows 没有这个模块）。Windows 上系统能正常'
Write-Host '  访问，但自动同步暂不可用，需要人工把 Excel 放好后重启服务。'
Write-Host ''
Write-Host '  数据库只监听 127.0.0.1，外部访问不到。'
Write-Host '  系统页面没有登录验证，只建议在可信的公司内网使用，不要映射到公网。'
Write-Host ''
Read-Host '按回车键退出'