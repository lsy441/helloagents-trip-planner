"""PostgreSQL数据库引擎和会话管理"""

import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from ..config import get_settings

logger = logging.getLogger(__name__)

Base = declarative_base()

_async_engine = None
_async_session_factory = None
_sync_engine = None
_sync_session_factory = None
_db_initialized = False


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        settings = get_settings()
        _async_engine = create_async_engine(
            settings.postgres_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _async_engine


def get_async_session_factory():
    global _async_session_factory
    if _async_session_factory is None:
        engine = get_async_engine()
        _async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _async_session_factory


def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        settings = get_settings()
        _sync_engine = create_engine(
            settings.postgres_sync_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _sync_engine


def get_sync_session_factory():
    global _sync_session_factory
    if _sync_session_factory is None:
        engine = get_sync_engine()
        _sync_session_factory = sessionmaker(engine, expire_on_commit=False)
    return _sync_session_factory


async def init_db():
    """初始化数据库 - 创建所有表"""
    global _db_initialized
    if _db_initialized:
        return

    try:
        engine = get_async_engine()
        from .models import ChatSession, TripContext
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _db_initialized = True
        logger.info("[PostgreSQL] Database initialized successfully")
    except Exception as e:
        logger.warning(f"[PostgreSQL] Database init failed, will retry on first use: {e}")
        _db_initialized = False


async def get_db_session() -> AsyncSession:
    """获取异步数据库会话"""
    if not _db_initialized:
        await init_db()
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


def get_sync_session():
    """获取同步数据库会话"""
    factory = get_sync_session_factory()
    return factory()


async def close_db():
    """关闭数据库连接"""
    global _async_engine, _sync_engine
    if _async_engine:
        await _async_engine.dispose()
        _async_engine = None
    if _sync_engine:
        _sync_engine.dispose()
        _sync_engine = None
