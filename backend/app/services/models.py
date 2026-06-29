"""PostgreSQL ORM模型"""

from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Index
from sqlalchemy.dialects.postgresql import JSONB

from .database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id = Column(String(64), primary_key=True)
    messages = Column(JSONB, default=list)
    title = Column(String(256), default="")
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_chat_sessions_updated_at", "updated_at"),
    )


class TripContext(Base):
    __tablename__ = "trip_contexts"

    session_id = Column(String(64), primary_key=True)
    context_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_trip_contexts_updated_at", "updated_at"),
    )
