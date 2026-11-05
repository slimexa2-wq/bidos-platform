#!/bin/zsh
# BidOS 双击启动器（给不熟悉命令行的同事）
#
# 首次使用请先在终端执行一次：./install.sh
# 之后每次开机双击本文件即可启动系统。
#
# 找不到 PostgreSQL 容器时会尝试自动启动（需已安装 Docker Desktop 或 OrbStack）。
cd "$(dirname "$0")" || exit 1

if [ ! -x "./run_bidos.sh" ]; then
  echo "首次使用：请先在终端执行 ./install.sh 完成部署。"
  read -r "?按回车键关闭…"
  exit 1
fi

echo ""
echo "  BidOS 招投标智能工作平台"
echo "  ────────────────────────────────"

# 同步器依赖数据库，先确保容器在跑
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "bidos-pg"; then
  if ! docker info >/dev/null 2>&1; then
    echo "  ✗ Docker 没在运行。请先打开 Docker Desktop 或 OrbStack，再双击本文件。"
    read -r "?按回车键关闭…"
    exit 1
  fi
  echo "  正在启动数据库…"
  docker start bidos-pg >/dev/null 2>&1 || docker run -d --name bidos-pg \
    -e POSTGRES_DB=bidos -e POSTGRES_USER=bidos -e POSTGRES_PASSWORD=bidos_dev_only \
    -p 55432:5432 -v bidos_pg_data:/var/lib/postgresql/data \
    pgvector/pgvector:pg16 >/dev/null 2>&1
  sleep 5
fi

./run_bidos.sh start
echo ""
./run_bidos.sh status
echo ""
echo "  浏览器打开："
echo "    本机    http://127.0.0.1:${BIDOS_WEB_PORT:-5173}"
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null)
[ -z "$LAN_IP" ] && LAN_IP=$(ipconfig getifaddr en1 2>/dev/null)
[ -n "$LAN_IP" ] && echo "    局域网  http://$LAN_IP:${BIDOS_WEB_PORT:-5173}   ← 发给同事"
echo ""
echo "  关闭此窗口不会停止系统。要停止请在终端执行 ./run_bidos.sh stop"
echo ""
read -r "?按回车键关闭…"
