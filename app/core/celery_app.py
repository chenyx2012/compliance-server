from __future__ import annotations

from celery import Celery

from app.core.config import settings


celery_app = Celery(
    "compliance_gateway",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_default_queue=settings.celery_task_queue,
    result_expires=settings.celery_result_expires_seconds,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    timezone="UTC",
    enable_utc=True,
    task_queues=None,  # 使用默认 + apply_async(queue=...)
    task_routes={
        "platform_tasks.s2_scan": {"queue": settings.celery_queue_scan_version},
        "platform.parse_csv_and_save": {"queue": settings.celery_queue_parse},
        "platform.batch_ai_verify_task": {"queue": settings.celery_queue_parse},
        "platform.import_open_source_from_task_path2_nvidia": {"queue": settings.celery_queue_import},
        "platform.import_open_source_from_task_path2_cpp": {"queue": settings.celery_queue_import},
        "platform.import_open_source_from_task_path2_java": {"queue": settings.celery_queue_import},
        "platform.import_open_source_from_task_path2_python": {"queue": settings.celery_queue_import},
        "platform.import_self_developed_from_path1": {"queue": settings.celery_queue_import},
    },
    imports=(
        "app.tasks.code_tree_tasks",
        "app.tasks.csv_parse_tasks",
        "app.tasks.file_ingest_tasks",
        "app.tasks.open_source_tasks",
        "app.tasks.self_developed_tasks",
        "app.tasks.s2_scan_tasks",
        "app.tasks.batch_ai_verify_tasks",
        "app.services.platform_tasks",
        "app.services.oat_scanner",
        "app.services.tasks",
    ),
)
