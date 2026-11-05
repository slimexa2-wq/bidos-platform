"""采集降级链（交接文档 §7.2 / §八 采集优先级）。

优先级：``API/RSS → HTTP（含 TLS 重试）→ Playwright → 人工兜底``。

为什么要有这个模块
------------------

改造前每个来源在 ``config/sources.yaml`` 里**只有一个** ``strategy``，
registry 按它一次性分派到某个 Connector；那一层失败 = 整个来源失败。
这不是「降级」，而是「单点 + 失败可见」。真实故障恰好需要跨层：

* ``www.cdggzy.com``：OpenSSL 3.x 默认 ``SECLEVEL=2`` 下 TLS 握手**稳定失败**
  （``SSLV3_ALERT_HANDSHAKE_FAILURE``），降到 ``SECLEVEL=1`` 才通 —— 同一层内的 TLS 降级。
* 间歇性 ``SSL: UNEXPECTED_EOF_WHILE_READING``：同层重试 + 逐级放宽 TLS 后才通。
* ``www.ccgp-sichuan.gov.cn``：Vue SPA，``http`` 层结构上抓不到 →
  症状是 ``parse_no_items``，**必须**降级到 Playwright 才有内容。
* ``www.cebpubservice.com``：命中反爬（网易易盾），Playwright 也过不去
  （合规红线不做验证码绕过）→ 只能落到人工兜底。

本模块只负责**记账**：记录每一层的尝试顺序、成功/失败、失败归类与原因。
真正的抓取动作在各 Connector 里实现，健康度落库在 ``source_health_store`` 里实现。

铁律
----

1. **不许静默跳过**：每一层的失败都要留下一条 ``LayerAttempt``，
   汇总写进 ``source_health.strategy`` / ``last_error`` / ``metadata``。
2. **不许伪造成功**：只有真的解析出条目才算该层成功；
   「拿到 200 但 0 条」不是成功（除非该层的语义明确允许，见 API 层注释）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "LAYER_RSS",
    "LAYER_SEARCH_API",
    "LAYER_JSON_API",
    "LAYER_HTTP",
    "LAYER_PLAYWRIGHT",
    "LAYER_HUMAN",
    "VALID_LAYERS",
    "LayerAttempt",
    "AttemptTrail",
    "build_chain",
]

# ---------------------------------------------------------------- 层级名
#: 官方 API / RSS（最优先：不依赖页面结构，站点改版不断）
LAYER_RSS = "rss"
LAYER_SEARCH_API = "search_api"
LAYER_JSON_API = "json_api"
#: 纯 HTTP + 最小 CSS 选择器（含 TLS 重试/降级、UA、超时策略）
LAYER_HTTP = "http"
#: 无头浏览器（应对 SPA / JS 渲染 / 需要执行脚本才出列表的站点）
LAYER_PLAYWRIGHT = "playwright"
#: 人工兜底：机器层全部失败时显式交人工，绝不静默判「无公告」
LAYER_HUMAN = "needs_human"

#: 可写入 ``fallback_chain`` 的合法层级（写错在加载期就报错，不留到运行期变 0 条）
VALID_LAYERS: tuple[str, ...] = (
    LAYER_RSS,
    LAYER_SEARCH_API,
    LAYER_JSON_API,
    LAYER_HTTP,
    LAYER_PLAYWRIGHT,
    LAYER_HUMAN,
)

#: 层级中文名（写进 last_error / 事件，让运维一眼看懂挂在哪一层）
LAYER_LABELS: dict[str, str] = {
    LAYER_RSS: "API/RSS",
    LAYER_SEARCH_API: "站点检索接口",
    LAYER_JSON_API: "签名 JSON 接口",
    LAYER_HTTP: "HTTP+选择器",
    LAYER_PLAYWRIGHT: "Playwright 无头浏览器",
    LAYER_HUMAN: "人工兜底",
}


def build_chain(strategy: str, fallback: Iterable[str] | None) -> tuple[str, ...]:
    """组装降级链 = 主策略（第 1 层）+ ``fallback_chain``（依次降级）。

    * 未配置 ``fallback_chain`` 时返回 ``(strategy,)`` —— **与改造前行为完全一致**，
      降级是显式开启的能力，绝不隐式给每个来源塞一个浏览器层（那会让本地
      fixture 测试去启动 Chromium，既慢又假）。
    * 自动剔除与主策略重复的层级，避免「http → http」这种假降级。
    * 未知层级名立刻 ``ValueError``：配置写错必须在加载期炸，
      不能拖到运行期表现成「0 条」让人误判成页面改版。
    """
    chain: list[str] = [strategy]
    for layer in fallback or ():
        name = str(layer).strip()
        if not name:
            continue
        if name not in VALID_LAYERS:
            raise ValueError(
                f"fallback_chain 含未知层级 {name!r}，合法值：{', '.join(VALID_LAYERS)}"
            )
        if name not in chain:
            chain.append(name)
    return tuple(chain)


@dataclass(frozen=True)
class LayerAttempt:
    """某一层的尝试结果。"""

    layer: str
    ok: bool
    kind: str | None = None  # 失败归类（进 SourceHealth.last_error 前缀）
    message: str | None = None
    items_found: int = 0
    elapsed_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "label": LAYER_LABELS.get(self.layer, self.layer),
            "ok": self.ok,
            "kind": self.kind,
            "message": self.message,
            "items_found": self.items_found,
            "elapsed_ms": self.elapsed_ms,
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass
class AttemptTrail:
    """一次 ``fetch`` 的完整尝试轨迹（按尝试顺序）。"""

    chain: tuple[str, ...] = ()
    attempts: list[LayerAttempt] = field(default_factory=list)

    def add(self, attempt: LayerAttempt) -> LayerAttempt:
        self.attempts.append(attempt)
        return attempt

    @property
    def succeeded_layer(self) -> str | None:
        for attempt in self.attempts:
            if attempt.ok:
                return attempt.layer
        return None

    @property
    def reached_human_layer(self) -> bool:
        return any(a.layer == LAYER_HUMAN for a in self.attempts)

    def tried(self) -> str:
        """``http>playwright`` 形式的链路串（写进 source_health.strategy）。"""
        return ">".join(a.layer for a in self.attempts) or ">".join(self.chain)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "chain": list(self.chain),
            "tried": self.tried(),
            "succeeded_layer": self.succeeded_layer,
            "attempts": [a.to_dict() for a in self.attempts],
        }

    def summary(self) -> str:
        """给人看的中文一句话（写进 last_error 尾部 / SOURCE_FAILED 事件）。"""
        parts = [
            f"{LAYER_LABELS.get(a.layer, a.layer)}[{a.layer}]"
            f"{'成功' if a.ok else f'失败({a.kind})'}"
            for a in self.attempts
        ]
        return " → ".join(parts) if parts else "未尝试任何层级"
