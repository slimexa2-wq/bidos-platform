"""桩实现。**不是真实模型**，``available`` 恒为 False，禁止当接入证据。

保留它的唯一用途：无模型环境下让上层代码路径可跑通（例如单测、演示），
并把「这是示例输出」写在返回值里，避免被误读成真实模型结果。
"""

from __future__ import annotations

from typing import Any

from app.ai.base import AIProvider


class MockAIProvider(AIProvider):
    name = "mock"
    mode = "mock"
    model = ""

    @property
    def available(self) -> bool:
        return False

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> str:
        return (
            "[Mock AI] 未接入真实模型，本段为占位输出，不得作为业务依据。"
            f"system={system[:40]!r} user={user[:60]!r}"
        )

    def summarize(self, *, prompt: str, evidence: list[dict[str, Any]]) -> str:
        return f"[Mock AI] 已基于 {len(evidence)} 条证据整理：{prompt}。请接入真实模型后重试。"
