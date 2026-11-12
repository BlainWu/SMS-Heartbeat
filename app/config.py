import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

# 项目根目录：app/config.py -> app/ -> 仓库根（不依赖启动 cwd）
APP_ROOT = Path(__file__).resolve().parent.parent

# 显式读取项目根目录 .env
load_dotenv(APP_ROOT / ".env")


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


APP_NAME = env("APP_NAME", "HeartbeatMonitor")
HOST = env("HOST", "0.0.0.0")
PORT = int(env("PORT", "8000"))

DB_URL = env("DB_URL", "sqlite:///./hb.db")

# SQLite 相对路径统一锚定到 APP_ROOT，避免 cwd 影响落库位置
if DB_URL.startswith("sqlite"):
    _url = make_url(DB_URL)
    _db = _url.database
    if _db and _db != ":memory:" and not Path(_db).is_absolute():
        _abs = (APP_ROOT / _db).resolve()
        DB_URL = _url.set(database=_abs.as_posix()).render_as_string(hide_password=False)

ADMIN_PASSWORD = env("ADMIN_PASSWORD", "admin")
SESSION_SECRET = env("SESSION_SECRET", "change_me")

HEARTBEAT_TOKEN = env("HEARTBEAT_TOKEN", "")  # 为空则不校验

SCAN_SECONDS = int(env("SCAN_SECONDS", "60"))
DEFAULT_INTERVAL_MIN = int(env("DEFAULT_INTERVAL_MIN", "15"))
DEFAULT_ALERT_COOLDOWN_MIN = int(env("DEFAULT_ALERT_COOLDOWN_MIN", "30"))

HISTORY_RETENTION_DAYS = int(env("HISTORY_RETENTION_DAYS", "30"))
DEFAULT_DING_KEYWORD = env("DEFAULT_DING_KEYWORD", "停止")
