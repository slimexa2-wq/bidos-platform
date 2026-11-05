#!/usr/bin/env bash
#
# BidOS 一键部署脚本（同事电脑用）
#
# 用法：
#   chmod +x install.sh && ./install.sh
#   ./install.sh --lan          # 允许局域网内其他电脑访问（默认开启）
#   ./install.sh --no-lan       # 只允许本机访问
#   ./install.sh --uninstall    # 卸载常驻服务（不删数据）
#
# 这个脚本会做什么：
#   1. 检查运行环境（Python / Node / Docker）
#   2. 建 Python 虚拟环境并装依赖
#   3. 装前端依赖
#   4. 启动 PostgreSQL 并恢复数据
#   5. 注册开机常驻服务（launchd）
#   6. 逐项验证，不通就明确报原因
#
# 幂等：重复执行安全。已经有环境会跳过重建，不会丢数据。
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ------------------------------------------------------------------ 输出样式
if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else
  R=""; G=""; Y=""; B=""; N=""
fi
step() { echo "${B}▶ $*${N}"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✗ $*${N}"; exit 1; }

LAN_HOST="0.0.0.0"
WEB_PORT="5173"
API_PORT="8000"
DB_NAME="bidos"; DB_USER="bidos"; DB_PASS="bidos_dev_only"; DB_PORT="55432"
VENV="$ROOT/.venv311"
LAN_IP=""

for arg in "$@"; do
  case "$arg" in
    --no-lan)    LAN_HOST="127.0.0.1" ;;
    --lan)       LAN_HOST="0.0.0.0" ;;
    --port=*)WEB_PORT="${arg#*=}" ;;
    --uninstall) MODE="uninstall" ;;
    *) ;;
  esac
done
MODE="${MODE:-install}"

echo "${B}════════════════════════════════════════════════════════════${N}"
echo "${B}  BidOS 招投标智能工作平台 · 部署${N}"
echo "${B}════════════════════════════════════════════════════════════${N}"
echo "  项目目录：$ROOT"
echo "  局域网访问：$([ "$LAN_HOST" = "0.0.0.0" ] && echo "开启（同事可用 http://<本机IP>:$WEB_PORT 访问）" || echo "关闭（仅本机）")"
echo

# ------------------------------------------------------------------ 0. 卸载
if [ "$MODE" = "uninstall" ]; then
  step "卸载常驻服务"
  for label in com.bidos.api com.bidos.web com.bidos.nassync; do
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "  已停止 $label" || true
    launchctl remove "$label" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
  done
  echo "  数据与代码都保留，重新执行 ./install.sh 即可恢复。"
  exit 0
fi

# ------------------------------------------------------------------ 1. 环境检查
step "1/6 检查运行环境"

# --- Python 3.11（必须；3.13 有兼容问题）
PY=""
for cand in python3.11 python3.12 python3; do
  p="$(command -v "$cand" 2>/dev/null)" || continue
  ver="$("$p" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)" || continue
  case "$ver" in
    3.11|3.12) PY="$p"; break ;;
  esac
done
[ -n "$PY" ] || die "没找到 Python 3.11 或 3.12。
安装：brew install python@3.11
装完后重新运行本脚本。"
ok "Python：$PY ($("$PY" -V 2>&1 | awk '{print $2}'))"

# --- Node 18+
NODE=""
for cand in node /opt/homebrew/bin/node /usr/local/bin/node; do
  p="$(command -v "$cand" 2>/dev/null)" || [ -x "$cand" ] || continue
  p="${p:-$cand}"
  maj="$("$p" -p 'process.versions.node.split(".")[0]' 2>/dev/null)" || continue
  [ -n "$maj" ] && [ "$maj" -ge 18 ] 2>/dev/null && NODE="$p" && break
done
[ -n "$NODE" ] || die "没找到 Node.js 18 或更高版本。
安装：brew install node
装完后重新运行本脚本。"
ok "Node：$NODE (v$("$NODE" -v))"

# --- Docker（跑 PostgreSQL）
DOCKER=""
for cand in docker /usr/local/bin/docker /opt/homebrew/bin/docker; do
  p="$(command -v "$cand" 2>/dev/null)" || [ -x "$cand" ] || continue
  p="${p:-$cand}"
  "$p" info >/dev/null 2>&1 && DOCKER="$p" && break
done
[ -n "$DOCKER" ] || die "Docker 没在运行。
安装：brew install --cask docker
装完启动 Docker Desktop（或 OrbStack）后重新运行本脚本。"
ok "Docker：$DOCKER"

# --- 网络工具（局域网 IP 探测用）
IPUTIL="$(command -v ipconfig || true)"
LAN_IP=""
if [ -n "$IPUTIL" ]; then
  for nic in en0 en1 en2; do
    ip="$($IPUTIL getifaddr "$nic" 2>/dev/null)" || continue
    [ -n "$ip" ] && { LAN_IP="$ip"; break; }
  done
fi
[ -n "$LAN_IP" ] || warn "没探测到局域网 IP，稍后用 ifconfig 手动查（局域网访问需要它）"

# --- 工作正本目录
WORKBOOK_DIR=""
for cand in "${BIDOS_WORKBOOK_DIR:-}" "/Volumes/市场共享文件/AI小程序收集汇总资料" "$HOME/Desktop/AI小程序收集汇总资料"; do
  [ -n "$cand" ] && [ -d "$cand" ] && { WORKBOOK_DIR="$cand"; break; }
done
if [ -z "$WORKBOOK_DIR" ]; then
  warn "没找到工作正本目录（共享盘未挂载 / 桌面也没有）"
  warn "  系统仍可启动，但同步会报错。请先挂载共享盘，或把表放到桌面同名目录："
  warn "    $HOME/Desktop/AI小程序收集汇总资料"
  WORKBOOK_DIR="$HOME/Desktop/AI小程序收集汇总资料"
else
  ok "工作正本：$WORKBOOK_DIR"
fi

# ------------------------------------------------------------------ 2. Python 环境
step "2/6 准备 Python 环境"
if [ -x "$VENV/bin/python" ]; then
  ok "已存在虚拟环境，跳过创建"
else
  "$PY" -m venv "$VENV" || die "创建虚拟环境失败"
  ok "已创建虚拟环境"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip 2>/dev/null || true
if "$VENV/bin/python" -c "import fastapi, psycopg, sqlalchemy, openpyxl" 2>/dev/null; then
  ok "依赖已就绪"
else
  [ -f requirements.lock.txt ] || die "缺少 requirements.lock.txt（依赖清单），请重新解压完整交付包"
  ok "安装依赖（首次约 1-3 分钟）…"
  "$VENV/bin/python" -m pip install --quiet -r requirements.lock.txt \
    || die "依赖安装失败。常见原因：网络不通。可换国内镜像重试：
    $VENV/bin/python -m pip install -r requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
  ok "依赖安装完成"
fi

# ------------------------------------------------------------------ 3. 前端依赖
step "3/6 准备前端依赖"
if [ -d "$ROOT/apps/web/node_modules/vite" ]; then
  ok "前端依赖已就绪"
else
  ok "安装前端依赖（首次约 1-3 分钟）…"
  ( cd "$ROOT/apps/web" && npm install --no-audit --no-fund ) \
    || die "前端依赖安装失败。可换镜像重试：
    cd apps/web && npm install --registry=https://registry.npmmirror.com"
  ok "前端依赖安装完成"
fi

# ------------------------------------------------------------------ 4. 数据库
step "4/6 启动数据库并恢复数据"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "bidos-pg"; then
  ok "PostgreSQL 容器已在运行"
else
  warn "启动 PostgreSQL 容器…"
  docker rm -f bidos-pg >/dev/null 2>&1 || true
  docker run -d --name bidos-pg \
    -e POSTGRES_DB="$DB_NAME" -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASS" \
    -p "$DB_PORT:5432" -v bidos_pg_data:/var/lib/postgresql/data \
    pgvector/pgvector:pg16 >/dev/null 2>&1 || die "PostgreSQL 容器启动失败"
  ok "容器已创建"
fi

ok "等待数据库就绪…"
for i in $(seq 1 40); do
  docker exec bidos-pg pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1 && break
  sleep 1
done
docker exec bidos-pg pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1 \
  || die "数据库 40 秒内没就绪。查看日志：docker logs bidos-pg"
ok "数据库就绪"

# 数据恢复：只在projects 表为空时恢复，避免覆盖已有数据
EXIST="$("$VENV/bin/python" - <<PY 2>/dev/null || echo 0
import psycopg
with psycopg.connect("postgresql://$DB_USER:$DB_PASS@127.0.0.1:$DB_PORT/$DB_NAME", connect_timeout=5) as c:
    with c.cursor() as cur:
        cur.execute("SELECT count(*) FROM projects")
        print(cur.fetchone()[0])
PY
)"
if [ "${EXIST:-0}" -gt 0 ]; then
  ok "数据库已有 $EXIST 条项目，跳过恢复（不覆盖现有数据）"
else
  [ -f "$ROOT/data/bidos.dump" ] || warn "没找到 data/bidos.dump，将只建空库（系统能启动，但没有历史数据）"
  if [ -f "$ROOT/data/bidos.dump" ]; then
    warn "恢复数据（约 30 秒）…"
    docker cp "$ROOT/data/bidos.dump" bidos-pg:/tmp/bidos.dump >/dev/null
    if docker exec bidos-pg pg_restore -U "$DB_USER" -d "$DB_NAME" \
         --clean --if-exists --no-owner --no-privileges /tmp/bidos.dump >/tmp/bidos_restore.log 2>&1; then
      ok "数据恢复完成"
    else
      warn "恢复过程有告警（不一定致命），检查日志：/tmp/bidos_restore.log"
      grep -qi "error" /tmp/bidos_restore.log && warn "存在错误，请查看 /tmp/bidos_restore.log"
    fi
  fi
fi

DB_URL="postgresql://$DB_USER:$DB_PASS@127.0.0.1:$DB_PORT/$DB_NAME"

# ------------------------------------------------------------------ 5. 常驻服务
step "5/6 注册开机常驻服务"

PLIST_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$PLIST_DIR"
for f in "$ROOT"/infra/launchd/*.plist; do
  [ -f "$f" ] || continue
  base="$(basename "$f")"
  sed -e "s|__PROJECT_ROOT__|$ROOT|g" \
      -e "s|__PYTHON__|$VENV/bin/python|g" \
      -e "s|__NODE__|$NODE|g" \
      -e "s|__DATABASE_URL__|$DB_URL|g" \
      -e "s|__WORKBOOK_DIR__|$WORKBOOK_DIR|g" \
      -e "s|__LAN_HOST__|$LAN_HOST|g" \
      -e "s|__WEB_PORT__|$WEB_PORT|g" \
      "$f" > "$PLIST_DIR/$base"
  plutil -lint "$PLIST_DIR/$base" >/dev/null 2>&1 || die "生成的 plist 有语法错误：$PLIST_DIR/$base"
  ok "已生成 $base"
done

# launchd 在部分环境（受管终端 / 已存在残留 override 记录）会拒绝 bootstrap，
# 报 error 5 且 bootout 也失败 —— 这是真实遇到过的情况，不是配置错误。
# 所以：先试 launchd，失败则退回「后台守护进程」模式，保证系统一定能用。
LAUNCHD_OK=1
for label in com.bidos.api com.bidos.web com.bidos.nassync; do
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
done
sleep 1
for label in com.bidos.api com.bidos.web com.bidos.nassync; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST_DIR/$label.plist" >/dev/null 2>&1; then
    ok "$label 已注册"
  else
    LAUNCHD_OK=0
    warn "$label 注册失败"
  fi
done

if [ "$LAUNCHD_OK" = "1" ]; then
  ok "三个常驻服务全部注册成功（开机自启）"
else
  warn "部分服务无法注册到 launchd（这台机器的 launchd 状态异常，报错通常是 error 5）"
  warn "改用「后台守护进程」模式启动，系统功能完全一样，只是关掉终端后需要重新启动"
  warn "脚本见 $ROOT/run_bidos.sh"
fi

# 兜底：不管 launchd 成没成功，都确保进程真的起来了
"$ROOT/run_bidos.sh" start >/dev/null 2>&1 || true

# ------------------------------------------------- 5.5 开机自动启动（双保险）
step "5.5/6 配置开机自动启动"

# 开机/登录自动启动用的登录项兜底脚本
cat > "$ROOT/autostart.sh" <<'AUTOSTART_EOF'
#!/usr/bin/env bash
# BidOS 开机自动启动（由 install.sh 生成，请勿手工编辑）
#
# 双重保障机制：
#   1) 主路径：launchd 服务（install.sh 会注册，登录即启动）；
#   2) 兜底：macOS「登录项」执行本脚本 —— 某些机器 launchd 会拒绝注册
#      并报 error 5（实测遇到过），此时靠这条兜底保证系统照样自启。
#
# 本脚本会自己判断服务是否已由 launchd 托管，已托管就什么都不做，
# 避免和 launchd 抢端口。
set -u
ROOT="__PROJECT_ROOT__"
LOG=/tmp/bidos_autostart.log

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG"; }

# 三个服务都被 launchd 正常托管 → 交给 launchd，不用插手
managed=0
for label in com.bidos.api com.bidos.web com.bidos.nassync; do
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    managed=$((managed+1))
  fi
done
if [ "$managed" -eq 3 ]; then
  log "三个服务均由 launchd 托管，跳过（不重复启动）"
  exit 0
fi

log "launchd 托管数=$managed，改用兜底启动"
cd "$ROOT" || { log "目录不存在：$ROOT"; exit 1; }
exec ./run_bidos.sh start >>"$LOG" 2>&1
AUTOSTART_EOF
# 替换脚本里的路径占位符
python3 - "$ROOT/autostart.sh" "$ROOT" <<'PYEOF'
import sys
p, root = sys.argv[1], sys.argv[2]
s = open(p, encoding='utf-8').read().replace('__PROJECT_ROOT__', root)
open(p, 'w', encoding='utf-8').write(s)
PYEOF
chmod +x "$ROOT/autostart.sh"
ok "已生成开机自启兜底脚本"

# 注册 macOS 登录项（launchd 失败时的保险）
LOGIN_DIR="$HOME/Library/LaunchAgents"
LOGIN_PLIST="$LOGIN_DIR/com.bidos.autostart.plist"
cat > "$LOGIN_PLIST" <<AUTOPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.bidos.autostart</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/autostart.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/bidos_autostart.log</string>
  <key>StandardErrorPath</key><string>/tmp/bidos_autostart.log</string>
</dict>
</plist>
AUTOPLIST
plutil -lint "$LOGIN_PLIST" >/dev/null 2>&1 || die "登录项 plist 生成失败"
launchctl bootout "gui/$(id -u)/com.bidos.autostart" >/dev/null 2>&1 || true
sleep 1
if launchctl bootstrap "gui/$(id -u)" "$LOGIN_PLIST" >/dev/null 2>&1; then
  ok "登录项已注册（自启兜底）"
else
  # 再退一步：写入系统偏好设置里的登录项列表（老式但依然生效）
  if osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$ROOT/autostart.sh\", hidden:true}" >/dev/null 2>&1; then
    ok "已加入「系统设置 → 通用 → 登录项」（自启兜底）"
  else
    warn "自动注册登录项失败，请手动添加：系统设置 → 通用 → 登录项 → + → 选$ROOT/autostart.sh"
  fi
fi

# 立即验证兜底脚本本身能跑通（不真启动，只验语法与依赖）
if bash -n "$ROOT/autostart.sh" 2>/dev/null; then
  ok "自启脚本语法正确"
fi

echo
echo "${B}  开机自动启动已配置${N}"
echo "    方式：launchd 服务 + 登录项兜底（双重保障）"
echo "    日志：/tmp/bidos_autostart.log"
echo "    验证：重启电脑后浏览器打开 http://127.0.0.1:$WEB_PORT 即可"

# ------------------------------------------------------------------ 6. 验证
step "6/6 验证服务"
sleep 6
FAILED=0

check() {
  local name="$1" url="$2"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" 2>/dev/null)"
  if [ "$code" = "200" ]; then ok "$name 已就绪 ($url)"
  else warn "$name 未就绪（HTTP $code）← $url"; FAILED=1; fi
}

check "后端接口" "http://127.0.0.1:$API_PORT/api/blind/workspace"
check "前端页面" "http://127.0.0.1:$WEB_PORT/blind"

if [ "$LAN_HOST" = "0.0.0.0" ] && [ -n "$LAN_IP" ]; then
  lan_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$LAN_IP:$WEB_PORT/blind" 2>/dev/null)"
  if [ "$lan_code" = "200" ]; then ok "局域网访问正常（http://$LAN_IP:$WEB_PORT）"
  else warn "局域网访问不通（HTTP $lan_code）← 检查 macOS 防火墙是否放行 node"; fi
fi

# ------------------------------------------------------------------ 结果
echo
echo "${B}════════════════════════════════════════════════════════════${N}"
if [ "$FAILED" = "0" ]; then
  echo "${G}${B}  部署完成${N}"
else
  echo "${Y}${B}  部署完成，但有项目未就绪${N}"
fi
echo "${B}════════════════════════════════════════════════════════════${N}"
echo
echo "  ${B}怎么用${N}"
echo "    本机访问：     http://127.0.0.1:$WEB_PORT"
if [ "$LAN_HOST" = "0.0.0.0" ] && [ -n "$LAN_IP" ]; then
echo "    ${B}局域网访问：   http://$LAN_IP:$WEB_PORT${N}  ← 把这个地址发给同事"
fi
echo
echo "  ${B}常用命令${N}"
echo "    启动/停止：    ./run_bidos.sh start | stop | restart | status"
echo "    立即同步一次： curl -X POST http://127.0.0.1:$API_PORT/api/blind/sync"
echo "    查看日志：     tail -f /tmp/bidos_api.log"
echo
echo
echo
echo "  ${B}开机自动启动${N}"
if launchctl print "gui/$(id -u)/com.bidos.autostart" >/dev/null 2>&1; then
  echo "    已配置（launchd 服务 + 登录项双重保障）"
else
  echo "    已生成自启脚本：$ROOT/autostart.sh"
  echo "    如未生效，手动添加：系统设置 → 通用 → 登录项 → + → 选autostart.sh"
fi
echo "    日志：/tmp/bidos_autostart.log"
echo "    验证：重启电脑后直接浏览器打开 http://127.0.0.1:$WEB_PORT"
echo
cat <<'TIP'
    · 前端打不开     → tail -30 /tmp/bidos_web.log
    · 接口报错       → tail -50 /tmp/bidos_api.log
    · 改表后系统没变 → tail -6 /tmp/bidos_nas_sync.out（正常最多等 60 秒）
    · 表改了没生效   → 改表前必须先关掉 Excel/WPS，文件被占用时同步会暂停
    · 开机没自启     → 查/tmp/bidos_autostart.log；再不行执行 ./run_bidos.sh start
TIP
echo
[ "$FAILED" = "0" ] || echo "  有问题把上面的日志发给接手的同事或 AI 助手。"
echo