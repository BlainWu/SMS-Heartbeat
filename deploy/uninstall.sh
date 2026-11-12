#!/usr/bin/env bash
# Heartbeat Monitor 卸载脚本
# 用法：sudo bash deploy/uninstall.sh [--purge-data]
# 默认保留 venv/ hb.db .env logs/；加 --purge-data 才会删除运行数据
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-heartbeat-monitor}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

log() { printf '\n[hb] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "[hb][ERROR] 请用 sudo 运行" >&2; exit 1; }

if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
  log "服务已停止并取消开机自启"
fi

if [ -f "$UNIT_FILE" ]; then
  rm -f "$UNIT_FILE"
  systemctl daemon-reload
  log "已删除 $UNIT_FILE"
fi

if [ "${1:-}" = "--purge-data" ]; then
  rm -rf "$APP_DIR/venv" "$APP_DIR/logs"
  rm -f "$APP_DIR/hb.db" "$APP_DIR/.env" \
        "$APP_DIR/hb.db-journal" "$APP_DIR/hb.db-wal" "$APP_DIR/hb.db-shm"
  log "运行数据已清除（seed/hb.db 保留，重新运行 install.sh 即可初始化）"
else
  log "运行数据保留：venv/ hb.db .env logs/"
  log "如需彻底清除：sudo bash deploy/uninstall.sh --purge-data"
fi
