"""
华为 AI 动态 token / 公开大模型替换配置。

优先级：
1. 若配置了 AI_OPENAI_COMPAT_BASE_URL → 走 OpenAI 兼容代理（能力等价替换）
2. 否则尝试华为动态 token（HMAC 签名，凭据齐全时）
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default) or ""


class HuaweiAIConfig:
    def __init__(self) -> None:
        self.app_id = _env("HUAWEI_AI_APP_ID")
        self.static_token = _env("HUAWEI_AI_STATIC_TOKEN")
        self.subject_code = _env("HUAWEI_AI_SUBJECT_CODE")
        self.signature_key = _env("HUAWEI_AI_SIGNATURE_KEY")
        self.token_url = _env(
            "HUAWEI_AI_TOKEN_URL",
            "https://console.his.huawei.com/agi/agi_agent/infer/sandbox/v1/auth/token",
        )
        self.chat_url = _env(
            "HUAWEI_AI_CHAT_URL",
            "https://console.his.huawei.com/agi/agi_agent/infer/sandbox/v1/chat/completions",
        )
        # OpenAI 兼容替换
        self.openai_base = (_env("AI_OPENAI_COMPAT_BASE_URL") or "").rstrip("/")
        self.openai_api_key = _env("AI_OPENAI_COMPAT_API_KEY")
        self.openai_model = _env("AI_OPENAI_COMPAT_MODEL") or settings.ai_model_default

    @property
    def use_openai_compat(self) -> bool:
        return bool(self.openai_base)

    def get_dynamic_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取鉴权 token；OpenAI 兼容模式下返回 api_key。"""
        if self.use_openai_compat:
            return self.openai_api_key or "EMPTY"

        now = time.monotonic()
        if (
            not force_refresh
            and _token_cache["token"]
            and now < float(_token_cache["expires_at"]) - 60
        ):
            return _token_cache["token"]

        # 无完整凭据时：若仅有 static_token 则直接使用
        if self.static_token and not (self.app_id and self.signature_key):
            _token_cache["token"] = self.static_token
            _token_cache["expires_at"] = now + 25 * 60
            return self.static_token

        if not (self.app_id and self.static_token and self.signature_key):
            logger.warning("huawei_ai_config — missing credentials for dynamic token")
            return None

        ts = str(int(time.time()))
        # 契约再造：常见 HMAC-SHA256(app_id + timestamp + static_token, signature_key)
        raw = f"{self.app_id}{ts}{self.static_token}"
        signature = hmac.new(
            self.signature_key.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        payload = {
            "app_id": self.app_id,
            "static_token": self.static_token,
            "subject_code": self.subject_code,
            "timestamp": ts,
            "signature": signature,
        }
        try:
            with httpx.Client(verify=False, timeout=30.0) as client:
                r = client.post(self.token_url, json=payload)
                if not r.is_success:
                    logger.error("get_dynamic_token HTTP %s: %s", r.status_code, r.text[:300])
                    return None
                body = r.json()
                token = body.get("access_token") or body.get("token") or body.get("dynamic_token")
                expires_in = int(body.get("expires_in") or 25 * 60)
                if not token:
                    logger.error("get_dynamic_token — no token in response: %s", body)
                    return None
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + expires_in
                return token
        except Exception as e:
            logger.exception("get_dynamic_token failed: %s", e)
            return None


huawei_ai_config = HuaweiAIConfig()


def chat_completions(
    *,
    messages: list,
    model: Optional[str] = None,
    temperature: float = 0.1,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """统一 chat/completions 调用（华为或 OpenAI 兼容）。"""
    cfg = huawei_ai_config
    request_data: Dict[str, Any] = {
        "model": model or (cfg.openai_model if cfg.use_openai_compat else settings.ai_model_default),
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "top_k": top_k,
    }

    if cfg.use_openai_compat:
        url = f"{cfg.openai_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.openai_api_key or ''}",
        }
        # 去掉 OpenAI 不认识的 top_k
        payload = {k: v for k, v in request_data.items() if v is not None and k != "top_k"}
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            return r.json()

    token = cfg.get_dynamic_token()
    if not token:
        raise RuntimeError("无法获取动态token")
    headers = {
        "Content-Type": "application/json",
        "Authorization": token,
    }
    with httpx.Client(verify=False, timeout=timeout) as client:
        r = client.post(cfg.chat_url, headers=headers, json=request_data)
        r.raise_for_status()
        return r.json()
