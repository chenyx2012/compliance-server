"""AI 误报分析 Redis 缓存（键 fp:{md5}）。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class AICacheService:
    def __init__(self) -> None:
        self._redis = None

    def _client(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis

            self._redis = redis.from_url(settings.redis_url, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception as e:
            logger.warning("AICacheService redis unavailable: %s", e)
            self._redis = None
            return None

    @staticmethod
    def key(md5: str) -> str:
        return f"fp:{md5}"

    def get(self, md5: str) -> Optional[Dict[str, Any]]:
        r = self._client()
        if not r:
            return None
        raw = r.get(self.key(md5))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set(self, md5: str, value: Dict[str, Any], ttl: int = 86400 * 7) -> None:
        r = self._client()
        if not r:
            return
        r.setex(self.key(md5), ttl, json.dumps(value, ensure_ascii=False))

    def stats(self) -> Dict[str, int]:
        r = self._client()
        if not r:
            return {"total_keys": 0, "ai_analyze": 0, "comparison_analyze": 0}
        keys = list(r.scan_iter("fp:*", count=500))
        ai_analyze = 0
        comparison = 0
        for k in keys[:2000]:
            try:
                raw = r.get(k)
                if not raw:
                    continue
                obj = json.loads(raw)
                src = obj.get("source") or obj.get("stage") or ""
                if src == "comparison_analyze":
                    comparison += 1
                else:
                    ai_analyze += 1
            except Exception:
                continue
        return {
            "total_keys": len(keys),
            "ai_analyze": ai_analyze,
            "comparison_analyze": comparison,
        }

    def clear(self) -> int:
        r = self._client()
        if not r:
            return 0
        keys = list(r.scan_iter("fp:*", count=500))
        n = 0
        if keys:
            # 分批删除
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                n += int(r.delete(*chunk))
        return n


ai_cache_service = AICacheService()
