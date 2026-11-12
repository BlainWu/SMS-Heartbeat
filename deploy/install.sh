#!/usr/bin/env bash
# Heartbeat Monitor - Linux 一键部署（systemd）
# 用法：sudo bash deploy/install.sh [-p PORT] [-u SERVICE_USER] [--admin-password XXX]
# 可重复执行：git pull 后再跑一次即为升级（不会覆盖 .env / hb.db）
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$APP_DIR/venv"
SERVICE_NAME="${SERVICE_NAME:-heartbeat-monitor}"
SERVICE_USER="${SERVICE_USER:-root}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PORT_ARG=""
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

log()  { printf '\n[hb] %s\n' "$*"; }
warn() { printf '\n[hb][WARN] %s\n' "$*" >&2; }
die()  { printf '\n[hb][ERROR] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: sudo bash deploy/install.sh [选项]
  -p, --port PORT            监听端口（默认 8000；已有 .env 时优先读 .env）
  -u, --user USER            服务运行用户（默认 root）
      --admin-password PWD   首次生成 .env 时设置后台密码
  -h, --help                 显示帮助

可用环境变量覆盖: SERVICE_NAME / PYTHON_BIN / PIP_INDEX_URL / ADMIN_PASSWORD
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -p|--port)            PORT_ARG="${2:-}"; shift 2 ;;
    -u|--user)            SERVICE_USER="${2:-}"; shift 2 ;;
    --admin-password)     ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    -h|--help)            usage; exit 0 ;;
    *)                    die "未知参数: $1（用 --help 查看用法）" ;;
  esac
done

# ---------- 前置检查 ----------
[ -f "$APP_DIR/app/main.py" ] || die "未找到 app/main.py，请在仓库内运行本脚本"
[ "$(id -u)" -eq 0 ] || die "需要 root 权限，请用 sudo bash deploy/install.sh 运行"
command -v systemctl >/dev/null 2>&1 || die "本脚本需要 systemd（该服务器似乎不是 systemd 环境）"
case "$APP_DIR" in *[[:space:]]*) die "安装路径不能包含空格: $APP_DIR" ;; esac
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "找不到 $PYTHON_BIN，请先安装 Python 3"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' \
  || die "Python 版本过低（需 >= 3.8），当前: $("$PYTHON_BIN" -V 2>&1)"

PY_MINOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[1])')"
if [ "$PY_MINOR" -ge 13 ]; then
  warn "检测到 Python 3.$PY_MINOR，锁定版本依赖可能没有对应 wheel；失败时会自动改用不锁版本安装。"
fi

# 端口优先级：命令行 -p > 已有 .env > 8000
ENV_PORT=""
if [ -f "$APP_DIR/.env" ]; then
  ENV_PORT="$(grep -E '^PORT=' "$APP_DIR/.env" | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
fi
PORT="${PORT_ARG:-${ENV_PORT:-8000}}"
case "$PORT" in ''|*[!0-9]*) die "端口不合法: $PORT" ;; esac

log "安装目录: $APP_DIR"
log "服务: $SERVICE_NAME | 端口: $PORT | 运行用户: $SERVICE_USER"

# 非 root 运行用户：需存在，并让仓库目录可写（SQLite 要写 journal/wal 文件）
if [ "$SERVICE_USER" != "root" ]; then
  id "$SERVICE_USER" >/dev/null 2>&1 \
    || die "服务用户不存在: $SERVICE_USER（先执行 useradd -r -s /sbin/nologin $SERVICE_USER）"
  chown -R "$SERVICE_USER" "$APP_DIR"
  log "已将 $APP_DIR 归属改为 $SERVICE_USER"
fi

# ---------- 1. 虚拟环境 ----------
if [ ! -x "$VENV/bin/python" ]; then
  log "创建虚拟环境 ..."
  if ! "$PYTHON_BIN" -m venv "$VENV" 2>/dev/null; then
    warn "python3 -m venv 失败，尝试安装 python3-venv ..."
    rm -rf "$VENV"
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq || true
      apt-get install -y -qq python3-venv python3-pip || true
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y python3-pip || true
    elif command -v yum >/dev/null 2>&1; then
      yum install -y python3-pip || true
    fi
    "$PYTHON_BIN" -m venv "$VENV" || die "无法创建虚拟环境，请手动安装 python3-venv 后重试"
  fi
else
  log "虚拟环境已存在，复用"
fi
pip_run() { "$VENV/bin/python" -m pip "$@"; }
pip_run install --upgrade pip -q -i "$PIP_INDEX_URL" >/dev/null 2>&1 || true

# ---------- 2. 安装依赖（多镜像降级 + 锁版本失败时自动放宽） ----------
install_reqs() {
  local req="$1" idx
  for idx in "$PIP_INDEX_URL" "https://pypi.tuna.tsinghua.edu.cn/simple" "https://pypi.org/simple"; do
    log "安装依赖（源: $idx）"
    if pip_run install --no-cache-dir -r "$req" -i "$idx"; then
      return 0
    fi
    warn "该源安装失败，尝试下一个源 ..."
  done
  return 1
}

if ! install_reqs "$APP_DIR/requirements.txt"; then
  warn "锁定版本依赖安装失败，改用不锁版本重试（常见于 Python 3.13+）"
  RELAXED="$(mktemp)"
  sed 's/==.*//' "$APP_DIR/requirements.txt" > "$RELAXED"
  install_reqs "$RELAXED" || die "依赖安装失败，请检查网络后重试或手动执行 venv/bin/pip install -r requirements.txt"
  rm -f "$RELAXED"
fi

# ---------- 3. 生成 .env（仅首次，升级不覆盖） ----------
APP_DIR="$APP_DIR" PORT="$PORT" ADMIN_PASSWORD="$ADMIN_PASSWORD" "$VENV/bin/python" - <<'PY'
import os
import secrets
from pathlib import Path

root = Path(os.environ["APP_DIR"])
env_file = root / ".env"

def set_key(text, key, value):
    out, done = [], False
    for line in text.splitlines():
        if line.startswith(key + "="):
            out.append("%s=%s" % (key, value))
            done = True
        else:
            out.append(line)
    if not done:
        out.append("%s=%s" % (key, value))
    return "\n".join(out) + "\n"

if env_file.exists():
    print("  .env 已存在，保留现有配置")
else:
    text = (root / ".env.example").read_text(encoding="utf-8")
    text = set_key(text, "PORT", os.environ["PORT"])
    text = set_key(text, "SESSION_SECRET", secrets.token_hex(16))
    pwd = os.environ.get("ADMIN_PASSWORD", "")
    if pwd:
        text = set_key(text, "ADMIN_PASSWORD", pwd)
    env_file.write_text(text, encoding="utf-8")  # UTF-8 无 BOM
    print("  .env 已生成（SESSION_SECRET 随机）")
PY

# ---------- 4. 初始化数据库（仅首次，升级不覆盖） ----------
if [ -f "$APP_DIR/hb.db" ]; then
  log "hb.db 已存在，保留线上数据"
else
  cp "$APP_DIR/seed/hb.db" "$APP_DIR/hb.db"
  log "已从 seed/hb.db 初始化数据库（含现有节点与历史）"
fi
mkdir -p "$APP_DIR/logs"
if [ "$SERVICE_USER" != "root" ]; then
  chown "$SERVICE_USER" "$APP_DIR/hb.db" "$APP_DIR/.env" "$APP_DIR/logs" || true
fi

# ---------- 5. 生成并注册 systemd 单元 ----------
log "写入 systemd 单元: $UNIT_FILE"
cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Heartbeat Monitor (FastAPI + APScheduler)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-$APP_DIR/.env
ExecStart=$VENV/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

# ---------- 6. 防火墙（尽力而为，云安全组仍需手动） ----------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
  ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
  log "ufw 已放行 TCP/$PORT"
fi
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
  log "firewalld 已放行 TCP/$PORT"
fi

# ---------- 7. 健康检查 ----------
health_ok() {
  HB_PORT="$PORT" "$VENV/bin/python" - <<'PY' 2>/dev/null
import os, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/health" % os.environ["HB_PORT"], timeout=2) as r:
        body = r.read()
    sys.exit(0 if b'"ok"' in body else 1)
except Exception:
    sys.exit(1)
PY
}

log "等待服务就绪 ..."
OK=0
for _i in $(seq 1 30); do
  if health_ok; then OK=1; break; fi
  sleep 1
done
if [ "$OK" -ne 1 ]; then
  warn "健康检查失败，最近的服务日志："
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
  die "服务未能就绪，请按上方日志排查（常见：端口被占用 / 依赖缺失 / .env 配置错误）"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')" || IP=""
printf '\n========== 部署完成 ==========\n'
printf ' 服务状态:   systemctl status %s\n' "$SERVICE_NAME"
printf ' 管理页面:   http://%s:%s\n' "${IP:-<服务器IP>}" "$PORT"
printf ' 健康检查:   http://127.0.0.1:%s/health\n' "$PORT"
printf ' 实时日志:   journalctl -u %s -f\n' "$SERVICE_NAME"
printf ' 运维脚本:   bash %s/deploy/manage.sh status|logs|restart\n' "$APP_DIR"
printf '\n [必做 1] 编辑 %s/.env，修改 ADMIN_PASSWORD 与 SESSION_SECRET，然后执行：\n' "$APP_DIR"
printf '           bash deploy/manage.sh restart\n'
printf ' [必做 2] 在云厂商控制台（阿里云安全组）放行 TCP %s\n' "$PORT"
printf '==============================\n'
