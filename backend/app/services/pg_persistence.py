"""PostgreSQL持久化服务 - 会话和行程上下文的持久化存储"""

import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from .database import get_sync_session, init_db, _db_initialized
from .models import ChatSession, TripContext

logger = logging.getLogger(__name__)

_pg_available = None


def _check_pg() -> bool:
    global _pg_available
    if _pg_available is not None:
        return _pg_available
    try:
        session = get_sync_session()
        session.execute("SELECT 1")
        session.close()
        _pg_available = True
        logger.info("[PostgreSQL] Connection verified")
        return True
    except Exception as e:
        logger.warning(f"[PostgreSQL] Connection failed: {e}")
        _pg_available = False
        return False


def is_pg_available() -> bool:
    return _check_pg()


def pg_save_session(session_id: str, messages: List[Dict[str, str]]) -> bool:
    try:
        session = get_sync_session()
        existing = session.query(ChatSession).filter_by(session_id=session_id).first()
        first_user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                first_user_msg = m.get("content", "")[:30]
                break

        if existing:
            existing.messages = messages
            existing.message_count = len(messages)
            existing.title = first_user_msg or f"Session {session_id}"
            existing.updated_at = datetime.utcnow()
        else:
            record = ChatSession(
                session_id=session_id,
                messages=messages,
                message_count=len(messages),
                title=first_user_msg or f"Session {session_id}",
            )
            session.add(record)
        session.commit()
        session.close()
        return True
    except Exception as e:
        logger.error(f"PostgreSQL save session failed: {e}")
        return False


def pg_load_session(session_id: str) -> Optional[List[Dict[str, str]]]:
    try:
        session = get_sync_session()
        record = session.query(ChatSession).filter_by(session_id=session_id).first()
        session.close()
        if record and record.messages:
            return record.messages
        return None
    except Exception as e:
        logger.error(f"PostgreSQL load session failed: {e}")
        return None


def pg_delete_session(session_id: str) -> bool:
    try:
        session = get_sync_session()
        session.query(ChatSession).filter_by(session_id=session_id).delete()
        session.query(TripContext).filter_by(session_id=session_id).delete()
        session.commit()
        session.close()
        return True
    except Exception as e:
        logger.error(f"PostgreSQL delete session failed: {e}")
        return False


def pg_list_sessions() -> List[Dict[str, Any]]:
    try:
        session = get_sync_session()
        records = session.query(ChatSession).order_by(
            ChatSession.updated_at.desc()
        ).all()
        session.close()
        result = []
        for r in records:
            result.append({
                "session_id": r.session_id,
                "title": r.title or f"Session {r.session_id}",
                "message_count": r.message_count,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            })
        return result
    except Exception as e:
        logger.error(f"PostgreSQL list sessions failed: {e}")
        return []


def pg_save_trip_context(session_id: str, context: Dict[str, Any]) -> bool:
    try:
        session = get_sync_session()
        existing = session.query(TripContext).filter_by(session_id=session_id).first()
        if existing:
            existing.context_data = context
            existing.updated_at = datetime.utcnow()
        else:
            record = TripContext(
                session_id=session_id,
                context_data=context,
            )
            session.add(record)
        session.commit()
        session.close()
        return True
    except Exception as e:
        logger.error(f"PostgreSQL save trip context failed: {e}")
        return False


def pg_load_trip_context(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        session = get_sync_session()
        record = session.query(TripContext).filter_by(session_id=session_id).first()
        session.close()
        if record and record.context_data:
            return record.context_data
        return None
    except Exception as e:
        logger.error(f"PostgreSQL load trip context failed: {e}")
        return None
