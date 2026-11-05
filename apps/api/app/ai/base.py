"""模型能力抽象层（2026-10-09 接手后新增）。

设计原则（沿用本项目既有红线）：

* **AI 只做信息整理，不做决策。** 本层只提供「给定系统提示 + 用户提示，返回一段文本」
  这一件事；是否参与投标、是否进入评估一律由人决定，不在本层表达。
* **失败必须可见。** 所有调用失败抛 ``AIProviderError`` 并带 ``kind``，由调用方记录到
  详情里（``aiAssist``），绝不静默降级成「好像调用了」。
* **mock 不等于真实。** ``MockAIProvider.available`` 恒为 ``False``，任何把 mock 当真实
  模型证据的展示都必须先看这个字段。

原 ``AIProvider.summarize`` 签名保留（既有 ``app/ai/mock.py`` 与潜在调用方不破坏），
在其上补 ``complete`` 作为真实调用入口。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

__all__ = ["AIProvider", "AIProviderError", "DEFAULT_SYSTEM_PROMPT"]

#: 缺省系统提示。SDK / 服务端普遍要求 messages[0] 必须是 system，否则直接报错；
#: 这里给一个中性兜底，避免调用方漏传。
DEFAULT_SYSTEM_PROMPT = (
    "你是招投标公告信息整理助手。只依据给定文本做信息提取与归类，"
    "不推测、不补造、不给出是否参与投标的建议。"
)


class AIProviderError(RuntimeError):
    """模型调用失败。``kind`` 是稳定分类，供上层如实记录失败原因。

    kind 取值：
        ``not_configured`` / ``disabled`` / ``auth`` / ``rate_limited`` /
        ``model_or_endpoint`` / ``timeout`` / ``unreachable`` /
        ``bad_response`` / ``provider_error``
    """

    def __init__(self, message: str, *, kind: str = "provider_error", detail: Any = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": str(self), "detail": self.detail}


class AIProvider(ABC):
    """模型能力最小接口。"""

    #: 供应商标识，用于在 /health 与详情里如实标注来源。
    name: str = "unknown"
    #: ``real`` = 真实模型；``mock`` = 桩，不得当真实证据。
    mode: str = "unknown"
    #: 实际使用的模型 id。
    model: str = ""

    @property
    def available(self) -> bool:
        """是否可发起**真实**模型调用。mock 一律 False。"""
        return self.mode == "real"

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> str:
        """一次文本补全，返回模型输出原文。失败抛 AIProviderError。"""
        raise NotImplementedError

    # -- 兼容既有签名 --------------------------------------------------------

    def summarize(self, *, prompt: str, evidence: list[dict[str, Any]]) -> str:
        """把证据条目整理成一段说明（旧接口，语义不变）。"""
        payload = "\n".join(
            json.dumps(item, ensure_ascii=False, default=str) for item in (evidence or [])
        )
        return self.complete(
            system=DEFAULT_SYSTEM_PROMPT,
            user=f"{prompt}\n\n证据材料：\n{payload}",
        )

    # -- 自述 ----------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """给 /health 与 /api/ai/status 用：如实说明能力与最近一次调用。"""
        return {
            "name": self.name,
            "mode": self.mode,
            "model": self.model,
            "available": self.available,
            "lastCall": getattr(self, "last_call", None),
        }
