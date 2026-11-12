from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Index
from datetime import datetime
from .db import Base

class MonitorNode(Base):
    __tablename__ = "monitor_nodes"
    id = Column(Integer, primary_key=True, index=True)
    node_key = Column(String(128), unique=True, index=True, nullable=False)

    enabled = Column(Boolean, default=True, nullable=False)
    interval_min = Column(Integer, default=15, nullable=False)

    ding_webhook = Column(String(512), nullable=False)
    ding_keyword = Column(String(64), nullable=False)

    last_seen_at = Column(DateTime, nullable=True)
    last_alert_at = Column(DateTime, nullable=True)

    alert_cooldown_min = Column(Integer, default=30, nullable=False)

class HeartbeatEvent(Base):
    __tablename__ = "heartbeat_events"
    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, nullable=False)
    node_key = Column(String(128), nullable=False)

    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source_ip = Column(String(64), nullable=True)

    payload = Column(Text, nullable=True)

Index("idx_hb_node_time", HeartbeatEvent.node_key, HeartbeatEvent.received_at)