"""
MySQL 异步 + 同步连接与会话管理。

- async：aiomysql（FastAPI 路由）
- sync：pymysql（Celery worker / Batch）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

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

_sync_url = settings.database_url.replace("+aiomysql", "+pymysql")
sync_engine = create_engine(
    _sync_url,
    pool_pre_ping=True,
    pool_recycle=3600,
)
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)

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
    """创建所有表（若不存在）；并种子写入 open_source_code_repo_mapping。"""
    import app.models  # noqa: F401
    from app.models.open_source_code_repo_mapping import seed_repo_mappings

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSessionLocal() as session:
            try:
                n = await seed_repo_mappings(session)
                await session.commit()
                if n:
                    logging.info("init_db — seeded %d open_source_code_repo_mapping rows", n)
            except Exception as e:
                await session.rollback()
                logging.warning("init_db seed skipped: %s", e)
    except Exception as e:
        logging.warning("init_db skipped (database may be unavailable): %s", e)
