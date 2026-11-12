#!/usr/bin/env bash
# Heartbeat Monitor 运维脚本
# 用法：sudo bash deploy/manage.sh <status|start|stop|restart|logs|follow|health> [日志行数]
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-heartbeat-monitor}"
PORT="$(grep -E '^PORT=' "$APP_DIR/.env" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
PORT="${PORT:-8000}"
ACTION="${1:-status}"

usage() {
  cat <<'EOF'
用法: sudo bash deploy/manage.sh <命令>
  status              查看服务状态 + 健康检查
  start | stop | restart
  logs   [N]          查看最近 N 行日志（默认 100）
  follow              实时跟踪日志（Ctrl+C 退出）
  health              仅健康检查
EOF
}

health() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health"
  else
    HB_PORT="$PORT" "$APP_DIR/venv/bin/python" -c \
      'import os,urllib.request; print(urllib.request.urlopen("http://127.0.0.1:%s/health" % os.environ["HB_PORT"], timeout=3).read().decode())'
  fi
}

case "$ACTION" in
  start|stop|restart)
    systemctl "$ACTION" "$SERVICE_NAME"
    ;;
  status)
    systemctl status "$SERVICE_NAME" --no-pager || true
    printf '\n[健康检查] '
    health || printf 'FAILED (服务可能未启动或端口 %s 被占用)\n' "$PORT"
    ;;
  logs)
    journalctl -u "$SERVICE_NAME" -n "${2:-100}" --no-pager
    ;;
  follow)
    journalctl -u "$SERVICE_NAME" -f
    ;;
  health)
    health
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
