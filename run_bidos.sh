#!/usr/bin/env bash
#
# BidOS 进程管理（不依赖 launchd也能跑）
#
#   ./run_bidos.sh start|stop|restart|status|logs
#
# 什么时候用这个：
#   正常情况用 launchd（开机自启）。但有些机器 launchd 会拒绝注册服务
#   （报 error 5，bootout 也失败），这时就用本脚本启动，功能完全一样，
#   区别只是关掉终端后需要重新 start。
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv311"
PY="$VENV/bin/python"
WEB_DIR="$ROOT/apps/web"
LOG_API="/tmp/bidos_api.log"
LOG_WEB="/tmp/bidos_web.log"
LOG_SYNC="/tmp/bidos_nas_sync.out"
PIDDIR="$ROOT/data/run"
mkdir -p "$PIDDIR"

API_PORT="${BIDOS_API_PORT:-8000}"
WEB_PORT="${BIDOS_WEB_PORT:-5173}"
LAN_HOST="${BIDOS_LAN_HOST:-0.0.0.0}"

# 从已生成的 plist 里读真实配置，避免两处设置打架
PLIST="$HOME/Library/LaunchAgents/com.bidos.api.plist"
if [ -f "$PLIST" ]; then
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:DATABASE_URL" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export DATABASE_URL="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:BIDOS_WORKBOOK_DIR" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export BIDOS_WORKBOOK_DIR="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:TZ" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export TZ="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:BIDOS_DATA_MODE" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export BIDOS_DATA_MODE="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:BIDOS_ORGANIZATION_ID" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export BIDOS_ORGANIZATION_ID="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:BIDOS_SCAN_CRON" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export BIDOS_SCAN_CRON="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:BIDOS_SCHEDULER_ENABLED" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export BIDOS_SCHEDULER_ENABLED="$v"
  v="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:BIDOS_SCHEDULER_TZ" "$PLIST" 2>/dev/null)" && [ -n "$v" ] && export BIDOS_SCHEDULER_TZ="$v"
fi
export PYTHONPATH="$ROOT/apps/api"
export PYTHONUNBUFFERED=1

[ -x "$PY" ] || { echo "缺少 Python 环境，请先运行 ./install.sh"; exit 1; }

NODE=""
for c in node /opt/homebrew/bin/node /usr/local/bin/node; do
  p="$(command -v "$c" 2>/dev/null)" || [ -x "$c" ] || continue
  NODE="${p:-$c}"; break
done

alive() { [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }

# 只杀本脚本自己启动过的进程树。
# 曾经用 `lsof -ti tcp:$PORT | xargs kill -9`兜底，结果把同端口上的其他进程
# （包括调用方自己的 shell）一起杀掉 —— 这是真实踩过的坑，不能再用宽泛清理。
kill_tree() {
  local pid="$1" sig="${2:-TERM}"
  [ -n "$pid" ] || return 0
  # 先杀子进程，再杀自身
  pkill "-$sig" -P "$pid" 2>/dev/null
  kill "-$sig" "$pid" 2>/dev/null
}

# 在独立会话里启动一个进程，使其不受调用方进程组影响。
#
# 为什么必须这么做：直接在脚本里 `cmd &` 启动的进程会继承脚本的进程组，
# 调用方（终端标签页、AI 工具、no ssh 会话）结束时内核会向整个进程组发
# SIGTERM，服务就跟着消失了 —— 表现为 install/run 脚本跑完返回 exit 137、
# 页面突然 502。macOS 没有 setsid 命令，但 Python 标准库的 os.setsid 可以，
# 而 Python 本身就是本项目的依赖，所以用它最省事也最可靠。
spawn_detached() {
  # $1=日志路径  $2=工作目录  其余=要执行的命令
  local log="$1" cwd="$2"; shift 2
  BIDOS_SPAWN_LOG="$log" BIDOS_SPAWN_CWD="$cwd" "$PY" - "$@" <<'PYEOF'
import os, sys
log_path = os.environ['BIDOS_SPAWN_LOG']
cwd = os.environ['BIDOS_SPAWN_CWD']
pid = os.fork()
if pid > 0:
    print(pid); sys.exit(0)
os.setsid()                       # 新会话，脱离原进程组
if os.fork() > 0:
    os._exit(0)                   # 孙进程交给 init 收养
# 孙进程不继承父进程 cwd，必须显式切换：脚本大量依赖相对路径与工作目录
os.chdir(cwd)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
log = open(log_path, 'ab', buffering=0)
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
os.execvp(sys.argv[1], sys.argv[1:])
PYEOF
}

start_one() {
  local name="$1" log="$2" port="$3" workdir="$4"; shift 4
  local pidf="$PIDDIR/$name.pid"
  if alive "$pidf"; then
    echo "  $name 已在运行 (pid $(cat "$pidf"))"
    return 0
  fi
  local pid
  pid="$(spawn_detached "$log" "$workdir" "$@")"
  # spawn_detached 报的是中间 fork 进程，真正的服务是它的孙进程，
  # pid 对不上（曾导致 status 说「启动失败」而服务其实活着，
  # stop 也杀不掉）。所以启动成功后按端口回读**真实监听者** pid 覆盖记录。
  local waited=0 realpid=""
  while [ "$waited" -lt 25 ]; do
    realpid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)"
    [ -n "$realpid" ] && break
    sleep 1; waited=$((waited+1))
  done
  if [ -n "$realpid" ]; then
    echo "$realpid" > "$pidf"
    echo "  $name 已启动 (pid $realpid, 端口 $port)"
  else
    echo "  $name 启动失败，日志末尾："
    tail -5 "$log" 2>/dev/null | sed 's/^/      /'
    rm -f "$pidf"
    kill_tree "$pid" KILL 2>/dev/null
    return 1
  fi
}

cmd_start() {
  echo "启动 BidOS…"
  start_one api "$LOG_API" "$API_PORT" "$ROOT" "$PY" "$ROOT/scripts/desktop_api.py"
  if [ -n "$NODE" ] && [ -f "$WEB_DIR/node_modules/vite/bin/vite.js" ]; then
    start_one web "$LOG_WEB" "$WEB_PORT" "$WEB_DIR" "$NODE" "$WEB_DIR/node_modules/vite/bin/vite.js" --port "$WEB_PORT" --host "$LAN_HOST" --strictPort
  else
    echo "  web 未启动：缺 node 或前端依赖，请先运行 ./install.sh"
  fi
  # 同步看护：每 60 秒一轮，Excel ↔ 系统联动靠它
  local syncf="$PIDDIR/sync.pid"
  if alive "$syncf"; then
    echo "  sync 已在运行 (pid $(cat "$syncf"))"
  else
    # 生成独立循环脚本再交给 spawn_detached：孙进程不继承本脚本的变量，
    # 所以必须把路径写死进文件里（写成占位符再用 sed 替换）。
    printf '#!/usr/bin/env bash\nwhile true; do\n  "%s" "%s/scripts/nas_sync.py" >>"%s" 2>&1\n  sleep 60\ndone\n' \
      "$PY" "$ROOT" "$LOG_SYNC" > "$PIDDIR/sync_loop.sh"
    chmod +x "$PIDDIR/sync_loop.sh"
    local spid
    spid="$(spawn_detached "$LOG_SYNC" "$ROOT" /bin/bash "$PIDDIR/sync_loop.sh")"
    echo "$spid" > "$syncf"
    # 判定方式：轮询日志等第一次同步结果，**不能只看进程是否存活** ——
    # spawn_detached 的中间 fork 会正常退出，循环进程是它的孙进程，
    # kill -0 判不到（曾因此误报「启动失败」）。
    # 一轮完整同步要十几秒（要读 1000+ 行 Excel），所以给到 90 秒。
    local waited=0
    while [ "$waited" -lt 90 ]; do
      grep -q 'lastSync' "$LOG_SYNC" 2>/dev/null && break
      sleep 3; waited=$((waited+3))
    done
    if grep -q 'lastSync' "$LOG_SYNC" 2>/dev/null; then
      echo "  sync 已启动并完成首轮同步 (pid $spid)"
    else
      echo "  sync 未确认启动，看日志：tail -5 $LOG_SYNC"
    fi
  fi
}

cmd_stop() {
  echo "停止 BidOS…"
  for n in api web sync; do
    f="$PIDDIR/$n.pid"
    if alive "$f"; then
      kill_tree "$(cat "$f")" TERM
      sleep 1
      kill_tree "$(cat "$f" 2>/dev/null)" KILL
      echo "  $n 已停止"
    fi
    rm -f "$f"
  done
  # 等待端口真正释放，否则紧接着 start 会撞端口
  local w=0
  while [ "$w" -lt 10 ]; do
    if ! lsof -nP -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1 \
       && ! lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then break; fi
    sleep 1; w=$((w+1))
  done
  return 0
}

cmd_status() {
  echo "BidOS 运行状态"
  printf "  %-6s " "api";  curl -s -o /dev/null -w "HTTP %{http_code}  http://127.0.0.1:$API_PORT\n" --max-time 8 "http://127.0.0.1:$API_PORT/api/blind/workspace" || echo "未响应"
  printf "  %-6s " "web";  curl -s -o /dev/null -w "HTTP %{http_code}  http://127.0.0.1:$WEB_PORT\n" --max-time 8 "http://127.0.0.1:$WEB_PORT/blind" || echo "未响应"
  ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
  if [ -n "$ip" ] && [ "$LAN_HOST" = "0.0.0.0" ]; then
    printf "  %-6s " "局域网"; curl -s -o /dev/null -w "HTTP %{http_code}  http://$ip:$WEB_PORT\n" --max-time 8 "http://$ip:$WEB_PORT/blind" || echo "未响应"
  fi
  printf "  %-6s " "同步"
  if [ -f "$ROOT/data/blind_workspace_state.json" ]; then
    "$PY" -c "
import json,sys
try:
    d=json.load(open('$ROOT/data/blind_workspace_state.json'))
    print(f\"{d.get('status')}  最近同步 {d.get('lastSync','-')[:19]}  {d.get('rows',0)} 行\")
except Exception as e:
    print('状态文件读取失败:', e)
" 2>/dev/null || echo "状态文件读取失败"
  else
    echo "尚未同步过"
  fi
  for n in api web sync; do
    f="$PIDDIR/$n.pid"
    alive "$f" && echo "  进程 $n: pid $(cat "$f")"
  done
}

case "${1:-status}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; sleep 2; cmd_start ;;
  status)  cmd_status ;;
  logs)    tail -f "$LOG_API" ;;
  *)       echo "用法: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac