#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/heartbeat_monitor"
PID_FILE="$APP_DIR/run/uvicorn.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "[stop] pid file not found: $PID_FILE"
  echo "[stop] try to find process by command..."
  ps -ef | grep "/opt/heartbeat_monitor/venv/bin/uvicorn app.main:app" | grep -v grep || true
  exit 0
fi

PID="$(cat "$PID_FILE" || true)"
if [[ -z "${PID:-}" ]]; then
  echo "[stop] empty pid file, removing."
  rm -f "$PID_FILE"
  exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
  echo "[stop] process not running. pid=$PID"
  echo "[stop] removing stale pid file."
  rm -f "$PID_FILE"
  exit 0
fi

echo "[stop] stopping pid=$PID (SIGTERM)..."
kill "$PID"

# 等待最多 10 秒优雅退出
for i in {1..10}; do
  if kill -0 "$PID" 2>/dev/null; then
    sleep 1
  else
    echo "[stop] stopped."
    rm -f "$PID_FILE"
    exit 0
  fi
done

echo "[stop] still running after 10s, force kill (SIGKILL)..."
kill -9 "$PID" || true
rm -f "$PID_FILE"
echo "[stop] killed."