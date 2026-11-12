
#!/usr/bin/env bash
set -euo pipefail

# ===== 可按需修改的配置 =====
APP_DIR="/opt/heartbeat_monitor"
VENV_BIN="$APP_DIR/venv/bin"
UVICORN="$VENV_BIN/uvicorn"

APP_MODULE="app.main:app"
HOST="0.0.0.0"
PORT="8000"

PID_FILE="$APP_DIR/run/uvicorn.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/uvicorn.log"
# ===========================

mkdir -p "$(dirname "$PID_FILE")" "$LOG_DIR"

echo "[start] checking existing pid..."
if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "[start] already running. pid=$PID"
    exit 0
  else
    echo "[start] stale pid file found, removing."
    rm -f "$PID_FILE"
  fi
fi

# 防止端口已被占用（可选但建议）
if command -v ss >/dev/null 2>&1; then
  if ss -lntp 2>/dev/null | grep -q ":$PORT"; then
    echo "[start] port $PORT seems in use:"
    ss -lntp | grep ":$PORT" || true
    echo "[start] abort."
    exit 1
  fi
fi

if [[ ! -x "$UVICORN" ]]; then
  echo "[start] uvicorn not found or not executable: $UVICORN"
  exit 1
fi

echo "[start] starting uvicorn in background..."
cd "$APP_DIR"

# 用 nohup 后台运行，stdout/stderr 都进日志
nohup "$UVICORN" "$APP_MODULE" --host "$HOST" --port "$PORT" \
  >> "$LOG_FILE" 2>&1 &

NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
echo "[start] started. pid=$NEW_PID"
echo "[start] log: $LOG_FILE"
