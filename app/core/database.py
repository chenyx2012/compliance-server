"""
MySQL 异步连接与会话管理。

- 使用 SQLAlchemy 2.0 异步引擎（aiomysql）
- 提供 get_db 依赖供路由注入 AsyncSession
- init_db 在应用启动时创建表（若不存在）
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

if TYPE_CHECKING:
    pass

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：获取异步数据库会话，请求结束后关闭。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """创建所有表（若不存在）。在应用启动时调用。连接失败时仅打日志，不抛异常。"""
    import logging
    import app.models  # noqa: F401 - 注册模型到 Base.metadata
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        logging.warning("init_db skipped (database may be unavailable): %s", e)
