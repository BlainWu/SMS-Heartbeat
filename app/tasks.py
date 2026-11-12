from datetime import datetime, timedelta
from .db import SessionLocal
from .models import MonitorNode, HeartbeatEvent
from .dingtalk import ding_send_keyword_text
from .config import HISTORY_RETENTION_DAYS


def fmt_cst(dt):
    """把 UTC 的 naive datetime 转成 UTC+8 并格式化输出（无时区后缀）"""
    if not dt:
        return "N/A"
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def init_service_mark_nodes():
    """
    服务启动时：对所有 enabled 节点写一条“服务初始化/节点激活”事件，
    并刷新 last_seen_at 为 now(UTC)，从而避免服务刚启动就立刻告警。
    """
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        nodes = db.query(MonitorNode).filter(MonitorNode.enabled == True).all()

        for n in nodes:
            # 以“服务启动”为新的基准时间（仍存 UTC，不改业务判断逻辑）
            n.last_seen_at = now
            # 启动后从零开始计算告警节奏更可控（可避免历史冷却带来的不确定）
            n.last_alert_at = None

            ev = HeartbeatEvent(
                node_id=n.id,
                node_key=n.node_key,
                received_at=now,
                source_ip="server",
                payload="服务初始化/节点激活"
            )
            db.add(ev)

        db.commit()
    finally:
        db.close()


def record_node_event(db, node: MonitorNode, payload: str, refresh_last_seen: bool = False):
    """
    记录系统事件到历史，并按需刷新 last_seen_at（启用/激活时使用）
    说明：db 为外部传入的同一会话，便于在路由里统一 commit。
    """
    now = datetime.utcnow()
    if refresh_last_seen:
        node.last_seen_at = now
        node.last_alert_at = None

    ev = HeartbeatEvent(
        node_id=node.id,
        node_key=node.node_key,
        received_at=now,
        source_ip="server",
        payload=payload
    )
    db.add(ev)


def scan_and_alert():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        nodes = db.query(MonitorNode).filter(MonitorNode.enabled == True).all()

        for n in nodes:
            if n.last_seen_at is None:
                overdue = True
                overdue_min = None
            else:
                overdue = now - n.last_seen_at > timedelta(minutes=n.interval_min)
                overdue_min = int((now - n.last_seen_at).total_seconds() / 60)

            if not overdue:
                continue

            if n.last_alert_at and now - n.last_alert_at < timedelta(minutes=n.alert_cooldown_min):
                continue

            msg = (
                f"短信网关心跳停止：{n.node_key}。"
                f"最后心跳：{fmt_cst(n.last_seen_at)}，超时约 {overdue_min} 分钟。"
            )
            ding_send_keyword_text(n.ding_webhook, n.ding_keyword, msg)

            n.last_alert_at = now
            db.commit()
    finally:
        db.close()


def cleanup_history():
    """清理历史：保留最近 N 天"""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=HISTORY_RETENTION_DAYS)
        db.query(HeartbeatEvent).filter(HeartbeatEvent.received_at < cutoff).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()