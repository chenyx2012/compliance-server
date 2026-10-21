"""同步 SQLAlchemy 会话（Celery worker / 同步工具共用），避免每任务 create_engine+dispose。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine = None
_SessionLocal = None


def get_sync_engine():
    global _engine, _SessionLocal
    if _engine is None:
        db_url = settings.database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
        _engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600, pool_size=5)
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


@contextmanager
def sync_session() -> Iterator[Session]:
    get_sync_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
