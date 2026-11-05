"""模型 Provider 装配与状态自述（2026-10-09 新增）。

只做一件事：按环境配置决定「现在能不能发起真实模型调用」，并把这个结论**如实**
暴露给 /health 与 /api/ai/status。禁止在未配置时装作已接入。

配置（全部来自环境变量，见 core/config.py）：

    BIDOS_AI_ENABLED=1          总开关
    BIDOS_AI_BASE_URL=...       OpenAI 兼容端点根地址
    BIDOS_AI_MODEL=...          模型 id
    BIDOS_AI_API_KEY=...        凭据（本地端点可留空）
    BIDOS_AI_PROVIDER=...       仅用于标注来源
    BIDOS_AI_TIMEOUT / BIDOS_AI_PROXY / BIDOS_AI_MAX_PER_RUN / BIDOS_AI_BODY_CHARS
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.base import AIProvider, AIProviderError
from app.ai.mock import MockAIProvider
from app.ai.openai_compatible import OpenAICompatibleProvider
from app.core.config import settings

__all__ = ["get_provider", "build_provider", "provider_status", "reset_provider"]

logger = logging.getLogger("bidos.ai")

_cache: dict[str, Any] = {"built": False, "provider": None, "reason": ""}


def build_provider() -> tuple[AIProvider | None, str]:
    """返回 ``(provider, reason)``。provider 为 None 时 reason 必须是可读的阻塞原因。"""
    if not settings.ai_enabled:
        return None, "未启用：BIDOS_AI_ENABLED 不是 true（默认关闭，现有流程不受影响）"
    base_url = (settings.ai_base_url or "").strip()
    model = (settings.ai_model or "").strip()
    missing = [name for name, value in (("BIDOS_AI_BASE_URL", base_url), ("BIDOS_AI_MODEL", model)) if not value]
    if missing:
        return None, "未配置：缺少 " + " / ".join(missing)
    try:
        provider = OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=settings.ai_api_key,
            name=settings.ai_provider or "openai_compatible",
            timeout=settings.ai_timeout,
            proxy=settings.ai_proxy,
        )
    except AIProviderError as exc:  # 配置非法：如实报错，不回退 mock 冒充可用
        return None, f"配置非法：{exc}"
    return provider, ""


def get_provider() -> AIProvider | None:
    """进程内单例。未配置/未启用返回 None（调用方必须处理 None）。"""
    if not _cache["built"]:
        provider, reason = build_provider()
        _cache.update({"built": True, "provider": provider, "reason": reason})
        if provider is None:
            logger.info("模型能力未接入：%s", reason)
        else:
            logger.info("模型能力已接入：%s / %s", provider.name, provider.model)
    return _cache["provider"]


def reset_provider() -> None:
    """配置变更后重建（供状态/探针接口使用）。"""
    _cache.update({"built": False, "provider": None, "reason": ""})


def provider_status(*, refresh: bool = False) -> dict[str, Any]:
    if refresh:
        reset_provider()
    provider = get_provider()
    status: dict[str, Any] = {
        "enabled": bool(settings.ai_enabled),
        "configured": bool((settings.ai_base_url or "").strip() and (settings.ai_model or "").strip()),
        "available": provider is not None and provider.available,
        "provider": settings.ai_provider,
        "model": provider.model if provider else (settings.ai_model or ""),
        "endpoint": (settings.ai_base_url or "").rstrip("/") or "",
        "keyPresent": bool(settings.ai_api_key),
        "maxPerRun": settings.ai_max_per_run,
        "bodyChars": settings.ai_body_chars,
        "reason": _cache["reason"],
        "lastCall": getattr(provider, "last_call", None) if provider else None,
        "mockFallback": isinstance(provider, MockAIProvider) or provider is None,
    }
    return status
