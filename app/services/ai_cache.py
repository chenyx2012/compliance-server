"""AI 误报分析 Redis 缓存。

键约定（hash 见 false_positive.case_hash，与 ai_false_positive_cases.code_snippet_hash 一致）：
  fp:{hash}                     最终判定（批量复核 / 集成分析结果）
  fp:{hash}:ai_analyze          第一步初判
  fp:{hash}:comparison_analyze  第二步对比分析
只缓存模型成功返回的结果；模型不可用时的兜底判定不入缓存。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_STAGES = ("ai_analyze", "comparison_analyze")


class AICacheService:
    def __init__(self) -> None:
        self._redis = None
        self._lock = threading.Lock()

    def _client(self):
        if self._redis is not None:
            return self._redis
        with self._lock:
            if self._redis is not None:
                return self._redis
            try:
                import redis

                client = redis.from_url(settings.redis_url, decode_responses=True)
                client.ping()
                self._redis = client
            except Exception as e:
                logger.warning("AICacheService redis unavailable: %s", e)
                return None
        return self._redis

    @staticmethod
    def key(md5: str, stage: Optional[str] = None) -> str:
        return f"fp:{md5}:{stage}" if stage else f"fp:{md5}"

    def get(self, md5: str, stage: Optional[str] = None) -> Optional[Dict[str, Any]]:
        r = self._client()
        if not r:
            return None
        try:
            raw = r.get(self.key(md5, stage))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def get_many(self, md5_list: list) -> Dict[str, Dict[str, Any]]:
        """批量取最终判定，返回 {md5: value}。"""
        r = self._client()
        if not r or not md5_list:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(md5_list), 500):
            chunk = md5_list[i : i + 500]
            try:
                values = r.mget([self.key(m) for m in chunk])
            except Exception as e:
                logger.warning("AICacheService mget failed: %s", e)
                return out
            for m, raw in zip(chunk, values):
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if obj.get("judgment"):
                    out[m] = obj
        return out

    def set(
        self,
        md5: str,
        value: Dict[str, Any],
        stage: Optional[str] = None,
        ttl: Optional[int] = None,
    ) -> None:
        r = self._client()
        if not r:
            return
        try:
            r.setex(
                self.key(md5, stage),
                ttl or settings.ai_cache_ttl_seconds,
                json.dumps(value, ensure_ascii=False),
            )
        except Exception as e:
            logger.warning("AICacheService set failed: %s", e)

    def stats(self) -> Dict[str, int]:
        r = self._client()
        empty = {"total_keys": 0, "ai_analyze": 0, "comparison_analyze": 0, "final": 0}
        if not r:
            return empty
        counts = dict(empty)
        for k in r.scan_iter("fp:*", count=500):
            counts["total_keys"] += 1
            stage = k.rsplit(":", 1)[-1] if k.count(":") >= 2 else ""
            if stage in _STAGES:
                counts[stage] += 1
            else:
                counts["final"] += 1
        return counts

    def clear(self) -> int:
        r = self._client()
        if not r:
            return 0
        keys = list(r.scan_iter("fp:*", count=500))
        n = 0
        for i in range(0, len(keys), 500):
            n += int(r.delete(*keys[i : i + 500]))
        return n


ai_cache_service = AICacheService()
