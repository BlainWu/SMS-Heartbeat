import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from .config import (
    APP_ROOT,
    SESSION_SECRET,
    ADMIN_PASSWORD,
    SCAN_SECONDS,
    DEFAULT_DING_KEYWORD,
    DEFAULT_INTERVAL_MIN,
    DEFAULT_ALERT_COOLDOWN_MIN,
)
from .db import engine, Base, get_db
from .models import MonitorNode, HeartbeatEvent
from .dingtalk import ding_send_keyword_text
from .tasks import scan_and_alert, cleanup_history, init_service_mark_nodes, record_node_event


templates = Jinja2Templates(directory=str(APP_ROOT / "templates"))


def fmt_cst(dt):
    """UI 展示用：UTC -> UTC+8，无时区后缀"""
    if not dt:
        return ""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


# 注册 Jinja2 filter：模板里用 {{ xxx | cst }}
templates.env.filters["cst"] = fmt_cst

app = FastAPI(title="Heartbeat Monitor")
app.mount("/static", StaticFiles(directory=str(APP_ROOT / "static")), name="static")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# 创建表
Base.metadata.create_all(bind=engine)


# -------------------------
# DTO（保留但目前未强制使用）
# -------------------------
class HeartbeatIn(BaseModel):
    node_key: str
    ts: Optional[str] = None
    status: Optional[str] = "ok"


# -------------------------
# 登录校验
# -------------------------
def require_login(request: Request):
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=401, detail="Not logged in")


# -------------------------
# Debug：原样回显 body（便于设备侧调试）
# -------------------------
@app.post("/api/debug_raw")
async def debug_raw(request: Request):
    raw = await request.body()
    return {
        "content_type": request.headers.get("content-type"),
        "raw": raw.decode("utf-8", errors="ignore"),
    }


# -------------------------
# 心跳接口
# -------------------------
@app.post("/api/heartbeat")
async def heartbeat(request: Request, db=Depends(get_db)):
    raw = (await request.body()).decode("utf-8", errors="ignore").strip()
    ct = (request.headers.get("content-type") or "").lower()
    payload = None

    # 1) form-urlencoded：尝试解析 key=value
    if "application/x-www-form-urlencoded" in ct:
        qs = parse_qs(raw, keep_blank_values=True)
        if "node_key" in qs and qs["node_key"]:
            payload = {"node_key": qs["node_key"][0]}
        else:
            # 有些设备 header 是 form，但 body 实际是 JSON 字符串
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
    else:
        # 2) 非 form：常规 JSON
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None

    # 3) payload 若是字符串（被包了一层引号），再 loads 一次
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            pass

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "msg": "Body is not a JSON object or form field",
                "content_type": request.headers.get("content-type"),
                "raw": raw,
            },
        )

    node_key = payload.get("node_key")
    if not node_key:
        raise HTTPException(400, "missing node_key")

    # ===== 保持原逻辑：更新 last_seen + 写历史 =====
    node = db.query(MonitorNode).filter(MonitorNode.node_key == node_key).first()
    if not node:
        raise HTTPException(404, f"node_key not found: {node_key}")

    if not node.enabled:
        return {"ok": True, "ignored": True, "reason": "node disabled"}

    now = datetime.utcnow()
    node.last_seen_at = now
    db.commit()

    src_ip = request.client.host if request.client else None
    ev = HeartbeatEvent(
        node_id=node.id,
        node_key=node.node_key,
        received_at=now,
        source_ip=src_ip,
        payload=raw,
    )
    db.add(ev)
    db.commit()

    return {"ok": True, "server_time": now.isoformat() + "Z"}


# -------------------------
# 管理 UI
# -------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/ui/nodes", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"err": None})


@app.post("/login", response_class=HTMLResponse)
def login_action(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(request=request, name="login.html", context={"err": "密码错误"})
    request.session["logged_in"] = True
    return RedirectResponse(url="/ui/nodes", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# -------------------------
# 节点列表
# -------------------------
@app.get("/ui/nodes", response_class=HTMLResponse)
def nodes_list(request: Request, db=Depends(get_db), _: None = Depends(require_login)):
    nodes = db.query(MonitorNode).order_by(MonitorNode.id.desc()).all()
    now = datetime.utcnow()
    view = []
    for n in nodes:
        if n.last_seen_at:
            delta_min = int((now - n.last_seen_at).total_seconds() / 60)
            online = (now - n.last_seen_at).total_seconds() <= n.interval_min * 60
        else:
            delta_min = None
            online = False

        view.append(
            {
                "id": n.id,
                "node_key": n.node_key,
                "enabled": n.enabled,
                "interval_min": n.interval_min,
                "last_seen_at": n.last_seen_at,
                "ago_min": delta_min,
                "online": online,
                "ding_keyword": n.ding_keyword,
                "alert_cooldown_min": n.alert_cooldown_min,
            }
        )

    return templates.TemplateResponse(request=request, name="nodes.html", context={"nodes": view})


# -------------------------
# 新建节点
# -------------------------
@app.get("/ui/nodes/new", response_class=HTMLResponse)
def node_new_page(request: Request, _: None = Depends(require_login)):
    return templates.TemplateResponse(
        request=request,
        name="node_form.html",
        context={
            "mode": "new",
            "node": None,
            "err": None,
            "defaults": {
                "ding_keyword": DEFAULT_DING_KEYWORD,
                "interval_min": DEFAULT_INTERVAL_MIN,
                "alert_cooldown_min": DEFAULT_ALERT_COOLDOWN_MIN,
            },
        },
    )


@app.post("/ui/nodes/new")
def node_new_action(
    request: Request,
    node_key: str = Form(...),
    ding_webhook: str = Form(...),
    ding_keyword: str = Form(...),
    interval_min: int = Form(DEFAULT_INTERVAL_MIN),
    alert_cooldown_min: int = Form(DEFAULT_ALERT_COOLDOWN_MIN),
    enabled: bool = Form(False),
    db=Depends(get_db),
    _: None = Depends(require_login),
):
    exists = db.query(MonitorNode).filter(MonitorNode.node_key == node_key).first()
    if exists:
        return templates.TemplateResponse(
            request=request,
            name="node_form.html",
            context={
                "mode": "new",
                "node": None,
                "err": "node_key 已存在",
                "defaults": {
                    "ding_keyword": ding_keyword,
                    "interval_min": interval_min,
                    "alert_cooldown_min": alert_cooldown_min,
                },
            },
        )

    node = MonitorNode(
        node_key=node_key.strip(),
        ding_webhook=ding_webhook.strip(),
        ding_keyword=ding_keyword.strip(),
        interval_min=interval_min,
        alert_cooldown_min=alert_cooldown_min,
        enabled=enabled,
    )
    db.add(node)
    db.commit()  # 先提交以获得 node.id

    # 如果创建时就是启用：写一条“创建并启用/激活”系统事件 + 刷新 last_seen_at
    if node.enabled:
        record_node_event(db, node, payload="节点创建并启用/激活", refresh_last_seen=True)
        db.commit()

    return RedirectResponse(url="/ui/nodes", status_code=302)


# -------------------------
# 编辑节点
# -------------------------
@app.get("/ui/nodes/{node_id}/edit", response_class=HTMLResponse)
def node_edit_page(request: Request, node_id: int, db=Depends(get_db), _: None = Depends(require_login)):
    node = db.query(MonitorNode).filter(MonitorNode.id == node_id).first()
    if not node:
        return RedirectResponse(url="/ui/nodes", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="node_form.html",
        context={
            "mode": "edit",
            "node": node,
            "err": None,
            "defaults": None,
        },
    )


@app.post("/ui/nodes/{node_id}/edit")
def node_edit_action(
    request: Request,
    node_id: int,
    ding_webhook: str = Form(...),
    ding_keyword: str = Form(...),
    interval_min: int = Form(DEFAULT_INTERVAL_MIN),
    alert_cooldown_min: int = Form(DEFAULT_ALERT_COOLDOWN_MIN),
    enabled: bool = Form(False),
    db=Depends(get_db),
    _: None = Depends(require_login),
):
    node = db.query(MonitorNode).filter(MonitorNode.id == node_id).first()
    if not node:
        return RedirectResponse(url="/ui/nodes", status_code=302)

    prev_enabled = node.enabled

    node.ding_webhook = ding_webhook.strip()
    node.ding_keyword = ding_keyword.strip()
    node.interval_min = interval_min
    node.alert_cooldown_min = alert_cooldown_min
    node.enabled = enabled

    # 仅当启用状态发生变化时，写系统事件；启用时刷新 last_seen_at
    if prev_enabled != node.enabled:
        if node.enabled:
            record_node_event(db, node, payload="节点启用/激活（编辑保存）", refresh_last_seen=True)
        else:
            record_node_event(db, node, payload="节点停用（编辑保存）", refresh_last_seen=False)

    db.commit()
    return RedirectResponse(url="/ui/nodes", status_code=302)


# -------------------------
# 启停/删除/历史/测试钉钉
# -------------------------
@app.post("/ui/nodes/{node_id}/toggle")
def node_toggle(node_id: int, db=Depends(get_db), _: None = Depends(require_login)):
    node = db.query(MonitorNode).filter(MonitorNode.id == node_id).first()
    if node:
        if node.enabled:
            node.enabled = False
            record_node_event(db, node, payload="节点停用（列表切换）", refresh_last_seen=False)
        else:
            node.enabled = True
            record_node_event(db, node, payload="节点启用/激活（列表切换）", refresh_last_seen=True)

        db.commit()

    return RedirectResponse(url="/ui/nodes", status_code=302)


@app.post("/ui/nodes/{node_id}/delete")
def node_delete(node_id: int, db=Depends(get_db), _: None = Depends(require_login)):
    node = db.query(MonitorNode).filter(MonitorNode.id == node_id).first()
    if node:
        db.delete(node)
        db.commit()
    return RedirectResponse(url="/ui/nodes", status_code=302)


@app.get("/ui/nodes/{node_id}/history", response_class=HTMLResponse)
def node_history(request: Request, node_id: int, limit: int = 200, db=Depends(get_db), _: None = Depends(require_login)):
    node = db.query(MonitorNode).filter(MonitorNode.id == node_id).first()
    if not node:
        return RedirectResponse(url="/ui/nodes", status_code=302)

    events = (
        db.query(HeartbeatEvent)
        .filter(HeartbeatEvent.node_id == node_id)
        .order_by(HeartbeatEvent.received_at.desc())
        .limit(limit)
        .all()
    )
    return templates.TemplateResponse(request=request, name="history.html", context={"node": node, "events": events, "limit": limit})


@app.post("/ui/nodes/{node_id}/test_ding")
def test_ding(node_id: int, db=Depends(get_db), _: None = Depends(require_login)):
    node = db.query(MonitorNode).filter(MonitorNode.id == node_id).first()
    if node:
        ding_send_keyword_text(node.ding_webhook, node.ding_keyword, f"测试消息：{node.node_key} 配置正常")
    return RedirectResponse(url="/ui/nodes", status_code=302)


@app.get("/health")
def health():
    return JSONResponse({"ok": True})


# -------------------------
# 调度器
# -------------------------
scheduler = BackgroundScheduler()


@app.on_event("startup")
def on_startup():
    # ✅ 每次服务启动：对所有 enabled 节点写“服务初始化/节点激活”历史，并刷新 last_seen_at
    init_service_mark_nodes()

    scheduler.add_job(scan_and_alert, "interval", seconds=SCAN_SECONDS, id="scan_job", replace_existing=True)
    # 历史清理每天跑一次（原逻辑保留）
    scheduler.add_job(cleanup_history, "cron", hour=3, minute=0, id="cleanup_job", replace_existing=True)
    scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()