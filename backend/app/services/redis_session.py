"""统一会话存储服务 - PostgreSQL持久化 + Redis缓存"""

import json
import os
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

import redis
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None
_USE_REDIS = False

SESSION_TTL = 7 * 24 * 3600
SESSION_LIST_KEY = "chat:sessions"
TRIP_CONTEXT_PREFIX = "trip:context:"


def _get_redis() -> Optional[redis.Redis]:
    global _redis_client, _USE_REDIS
    if _USE_REDIS and _redis_client:
        return _redis_client

    redis_host = os.getenv("REDIS_HOST", "")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD", "") or None
    redis_db = int(os.getenv("REDIS_DB", "0"))
    redis_url = os.getenv("REDIS_URL", "")

    try:
        if redis_host:
            _redis_client = redis.Redis(
                host=redis_host, port=redis_port, password=redis_password,
                db=redis_db, decode_responses=True,
            )
        elif redis_url:
            _redis_client = redis.from_url(redis_url, decode_responses=True)
        else:
            _USE_REDIS = False
            return None

        _redis_client.ping()
        _USE_REDIS = True
        logger.info("[Redis Session] Connected successfully")
        return _redis_client
    except Exception as e:
        logger.warning(f"[Redis Session] Connection failed, fallback to PostgreSQL: {e}")
        _USE_REDIS = False
        _redis_client = None
        return None


def is_redis_available() -> bool:
    return _get_redis() is not None


def _session_key(session_id: str) -> str:
    return f"chat:session:{session_id}"


def _trip_context_key(session_id: str) -> str:
    return f"{TRIP_CONTEXT_PREFIX}{session_id}"


def save_session(session_id: str, messages: List[Dict[str, str]]) -> bool:
    from .pg_persistence import pg_save_session, is_pg_available

    pg_ok = False
    if is_pg_available():
        pg_ok = pg_save_session(session_id, messages)

    r = _get_redis()
    if r:
        try:
            data = json.dumps(messages, ensure_ascii=False)
            r.setex(_session_key(session_id), SESSION_TTL, data)
            r.zadd(SESSION_LIST_KEY, {session_id: datetime.now().timestamp()})
            r.expire(SESSION_LIST_KEY, SESSION_TTL)
            return True
        except Exception as e:
            logger.error(f"Redis save session failed: {e}")
            return pg_ok

    return pg_ok


def load_session(session_id: str) -> Optional[List[Dict[str, str]]]:
    r = _get_redis()
    if r:
        try:
            data = r.get(_session_key(session_id))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis load session failed: {e}")

    from .pg_persistence import pg_load_session, is_pg_available
    if is_pg_available():
        result = pg_load_session(session_id)
        if result and r:
            try:
                r.setex(_session_key(session_id), SESSION_TTL, json.dumps(result, ensure_ascii=False))
            except Exception:
                pass
        return result

    return None


def delete_session(session_id: str) -> bool:
    from .pg_persistence import pg_delete_session, is_pg_available

    pg_ok = False
    if is_pg_available():
        pg_ok = pg_delete_session(session_id)

    r = _get_redis()
    if r:
        try:
            r.delete(_session_key(session_id))
            r.delete(_trip_context_key(session_id))
            r.zrem(SESSION_LIST_KEY, session_id)
            return True
        except Exception as e:
            logger.error(f"Redis delete session failed: {e}")
            return pg_ok

    return pg_ok


def list_sessions() -> List[Dict[str, Any]]:
    from .pg_persistence import pg_list_sessions, is_pg_available

    r = _get_redis()
    if r:
        try:
            session_ids = r.zrevrange(SESSION_LIST_KEY, 0, -1)
            result = []
            for sid in session_ids:
                messages = load_session(sid)
                if messages and len(messages) > 0:
                    first_user_msg = ""
                    for m in messages:
                        if m.get("role") == "user":
                            first_user_msg = m.get("content", "")[:30]
                            break
                    score = r.zscore(SESSION_LIST_KEY, sid)
                    result.append({
                        "session_id": sid,
                        "title": first_user_msg or f"Session {sid}",
                        "message_count": len(messages),
                        "updated_at": datetime.fromtimestamp(score).isoformat() if score else None,
                    })
            if result:
                return result
        except Exception as e:
            logger.error(f"Redis list sessions failed: {e}")

    if is_pg_available():
        return pg_list_sessions()

    return []


def save_trip_context(session_id: str, context: Dict[str, Any]) -> bool:
    from .pg_persistence import pg_save_trip_context, is_pg_available

    pg_ok = False
    if is_pg_available():
        pg_ok = pg_save_trip_context(session_id, context)

    r = _get_redis()
    if r:
        try:
            data = json.dumps(context, ensure_ascii=False)
            r.setex(_trip_context_key(session_id), SESSION_TTL, data)
            return True
        except Exception as e:
            logger.error(f"Redis save trip context failed: {e}")
            return pg_ok

    return pg_ok


def load_trip_context(session_id: str) -> Optional[Dict[str, Any]]:
    r = _get_redis()
    if r:
        try:
            data = r.get(_trip_context_key(session_id))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis load trip context failed: {e}")

    from .pg_persistence import pg_load_trip_context, is_pg_available
    if is_pg_available():
        result = pg_load_trip_context(session_id)
        if result and r:
            try:
                r.setex(_trip_context_key(session_id), SESSION_TTL, json.dumps(result, ensure_ascii=False))
            except Exception:
                pass
        return result

    return None
