from __future__ import annotations

from celery import Celery

from app.core.config import settings


celery_app = Celery(
    "compliance_gateway",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    # 统一队列名，便于按业务隔离/扩容（worker 可指定 -Q）
    task_default_queue=settings.celery_task_queue,
    result_expires=settings.celery_result_expires_seconds,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 便于前端区分 “已开始执行” 与 “仅排队”
    task_track_started=True,
)

