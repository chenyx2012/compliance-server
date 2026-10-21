"""北京时间工具（DB 写入统一用 UTC+8）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_BEIJING = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    """返回当前北京时间（naive/aware 均可被 MySQL DATETIME 接受；使用 aware）。"""
    return datetime.now(timezone.utc).astimezone(_BEIJING)
