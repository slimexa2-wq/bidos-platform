"""公告字段的模型整理（2026-10-09 新增；同日扩展 Agent 通道）。

定位：**只做「信息整理」，不做「是否参与」的判断。**
规则引擎（`tender_details.extract_details`）已经抽到的字段，本模块**一律不覆盖**；
只在规则留空的位置，把文字型字段从公告正文里找出来，并且要求给出**原文引用**，
引用在正文里找不到的一律丢弃（防幻觉的硬闸门）。

两条调用路径**共用同一个闸门** `apply_proposals()`：

* ``enrich_detail()`` —— 进程内 HTTP Provider（`app/ai/openai_compatible.py`）调用；
* ``app/services/ai_handoff.py`` —— WorkBuddy Agent 通道，由 Agent 用自己的模型整理后回传。

刻意排除的字段及原因（业务口径，不随实现变化）：

* ``招标金额`` —— 金额有单位 / 总额单价 / 服务年限不可混算的口径，交回规则抽取；
* ``报名截止时间`` / ``开标时间`` —— 时间进入提醒与保证金链路，错一个数字代价高，
  保持规则抽取 + 人工核对。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterable

from app.ai.base import AIProvider, AIProviderError
from app.core.config import settings

__all__ = [
    "ALLOWED_FIELDS",
    "MAX_VALUE_CHARS",
    "SYSTEM_PROMPT",
    "apply_proposals",
    "build_prompt",
    "enrich_detail",
    "missing_fields",
    "normalize",
    "parse_items",
]

#: 允许模型整理的字段（文字型、可从正文直接引用）。
ALLOWED_FIELDS: tuple[str, ...] = (
    "甲方名称",
    "代理机构",
    "项目区域",
    "项目实施地点",
    "项目编号",
    "采购方式",
    "服务期限",
)

#: 单字段取值长度上限：超过视为整段摘抄，丢弃。
MAX_VALUE_CHARS = 200

SYSTEM_PROMPT = (
    "你是招投标公告的信息整理助手。任务：从给定的公告正文里，找出指定字段的原文取值。"
    "硬性要求："
    "1) 只依据正文，不得推测、不得补造、不得引入正文之外的知识；"
    "2) 每个字段必须给出一段**在正文中原样连续出现**的片段作为引用（quote）；"
    "3) 正文里找不到的字段，不要输出该字段，不要用空字符串占位；"
    "4) 只做信息提取，不要评价项目、不要建议是否参与投标；"
    "5) 只输出 JSON，结构为 "
    '{"items":[{"field":"字段名","value":"取值","quote":"正文原样片段"}]}，'
    "不要输出任何解释文字或代码块标记。"
)

_WS_RE = re.compile(r"\s+")
_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def normalize(text: str) -> str:
    """折叠空白，用于引用比对（模型常改换行/空格，不能因此误判为幻觉）。"""
    return _WS_RE.sub("", text or "")


def missing_fields(detail: dict[str, Any] | None) -> list[str]:
    """规则留空、且落在允许清单里的字段。"""
    fields = (detail or {}).get("fields") or {}
    return [name for name in ALLOWED_FIELDS if not str(fields.get(name) or "").strip()]


def build_prompt(title: str, missing: Iterable[str], body: str, body_chars: int | None = None) -> str:
    """构造整理请求。HTTP Provider 与 Agent 通道共用同一份提示词，保证口径一致。"""
    limit = body_chars if body_chars is not None else settings.ai_body_chars
    excerpt = (body or "")[: max(500, int(limit))]
    return (
        f"公告标题：{title}\n"
        f"待补字段：{'、'.join(missing)}\n"
        "公告正文：\n"
        f"{excerpt}"
    )


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = _FENCE_RE.sub("", raw or "").strip()
    try:
        loaded = json.loads(cleaned)
        return loaded if isinstance(loaded, dict) else {"items": loaded}
    except json.JSONDecodeError:
        pass
    start, depth = -1, 0
    for index, char in enumerate(cleaned):
        if char == "{":
            if start < 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError:
                    start = -1
    raise ValueError("模型输出中没有可解析的 JSON 对象")


def parse_items(raw: str) -> list[dict[str, Any]]:
    """从模型输出里取出 proposals 数组。"""
    payload = _extract_json(raw)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("模型输出缺少 items 数组")
    return [item for item in items if isinstance(item, dict)]


def apply_proposals(
    detail: dict[str, Any],
    proposals: Iterable[dict[str, Any]],
    *,
    body: str,
    origin: dict[str, Any],
    duration_ms: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """**唯一的写入闸门**：校验 proposals 后合并进 detail，返回新的 detail。

    闸门规则（两条调用路径完全一致）：

    1. 只接受「规则留空」的字段 —— 已抽到的值一律不动；
    2. ``quote`` 必须在**库内正文**中原样出现（折叠空白后比对），否则按幻觉丢弃；
    3. 取值长度超过 :data:`MAX_VALUE_CHARS` 丢弃；
    4. 同一字段只接受一次；
    5. 不产生任何「是否参与 / 是否报名」的写入。
    """
    result = dict(detail or {})
    fields = dict(result.get("fields") or {})
    evidence = dict(result.get("evidence") or {})
    missing = missing_fields(result)
    excerpt = (body or "")[: max(500, int(settings.ai_body_chars))]
    normalized_body = normalize(excerpt)

    filled: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in proposals or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        value = str(item.get("value") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if field not in missing:
            rejected.append({"field": field, "value": value[:60], "reason": "字段不在待补清单内（规则已抽到或不在允许清单）"})
            continue
        if field in filled:
            rejected.append({"field": field, "value": value[:60], "reason": "同字段重复提交"})
            continue
        if not value:
            rejected.append({"field": field, "value": "", "reason": "取值为空"})
            continue
        if len(value) > MAX_VALUE_CHARS:
            rejected.append({"field": field, "value": value[:60], "reason": "取值过长，疑似整段摘抄"})
            continue
        if not quote or normalize(quote) not in normalized_body:
            rejected.append({"field": field, "value": value[:60], "reason": "引用未在正文中找到，按幻觉丢弃"})
            continue
        fields[field] = value
        evidence[field] = {
            "label": "模型整理",
            "text": value,
            "quote": quote,
            "ai": True,
            "provider": origin.get("provider") or "",
            "model": origin.get("model") or "",
        }
        filled.append(field)

    result["fields"] = fields
    result["evidence"] = evidence
    assist: dict[str, Any] = {
        "status": "ok" if filled else "empty",
        "provider": origin.get("provider") or "",
        "model": origin.get("model") or "",
        "origin": origin.get("kind") or "unknown",
        "requested": missing,
        "filled": filled,
        "rejected": rejected,
        "note": note
        or "模型只补规则留空的文字型字段；已抽到的规则结果与是否参与判断均未被修改。",
    }
    if duration_ms is not None:
        assist["durationMs"] = duration_ms
    result["aiAssist"] = assist
    return result


def enrich_detail(
    provider: AIProvider,
    *,
    title: str,
    body: str,
    detail: dict[str, Any],
    body_chars: int | None = None,
) -> dict[str, Any]:
    """进程内 Provider 路径：调模型 → 解析 JSON → 过 :func:`apply_proposals` 闸门。"""
    origin = {"provider": provider.name, "model": provider.model, "kind": "http_provider"}
    missing = missing_fields(detail)
    if not body or not body.strip():
        return {**detail, "aiAssist": {"status": "skipped", "reason": "无公告正文，不做模型整理", **origin}}
    if not missing:
        return {
            **detail,
            "aiAssist": {"status": "skipped", "reason": "规则已抽到的字段之外无待补内容", **origin},
        }

    started = time.monotonic()
    try:
        raw = provider.complete(
            system=SYSTEM_PROMPT,
            user=build_prompt(title, missing, body, body_chars),
        )
    except AIProviderError as exc:
        return {
            **detail,
            "aiAssist": {
                "status": "failed",
                "durationMs": int((time.monotonic() - started) * 1000),
                "error": exc.as_dict(),
                "note": "模型整理失败，字段保持规则抽取结果，未做任何替换。",
                **origin,
            },
        }
    duration = int((time.monotonic() - started) * 1000)
    try:
        proposals = parse_items(raw)
    except ValueError as exc:
        return {
            **detail,
            "aiAssist": {
                "status": "failed",
                "durationMs": duration,
                "error": {"kind": "bad_response", "message": str(exc), "detail": (raw or "")[:400]},
                "note": "模型输出无法解析为 JSON，字段保持不变。",
                **origin,
            },
        }
    return apply_proposals(detail, proposals, body=body, origin=origin, duration_ms=duration)
