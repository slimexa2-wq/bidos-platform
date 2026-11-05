"""数据质量守卫：垃圾项目名/甲方名拦截（验收清单 §P0 数据质量要求）。

背景事故（2026-09-24）
--------------------
真表「投标项目汇总9.17.xlsx」的「未中标项目」Sheet 有 3 行结构错位：
单元格里的**竞对复盘描述**落进了"项目名称"列（如
「1. 以安保服务为核心业务，专业化程度高…」），"甲方名称"列同样是描述文本。
导入脚本未做名称合法性校验，3 条垃圾项目入库并在竞对分析页渲染，被用户当场抓包。

规则设计方法（重要，勿凭直觉改）
--------------------------------
第一版规则凭直觉写了「含换行 → 垃圾」「长度>60 → 垃圾」，
**在真实数据上验证后发现严重误伤**（2274 条真名实测）：

    规则                 命中   真阳   误伤
    编号列表开头            3     3      0    ← 可用
    叙事标点 。/；           0     0      0    ← 可用（安全网）
    长度 > 60 字           12     3      9    ← 误伤（联合采购名合法地很长，最长 87 字）
    含换行符               22     3     19    ← 误伤（Excel 单元格换行是常见排版）

结论：**换行与长度都不是垃圾信号**，一律不做判据。
最终判据只有"结构性叙事特征"——真项目名不会用「1. 2. 3.」编号，也不会带句末标点。

判据（全部确定性，无 LLM）
--------------------------
1. empty         : 空值
2. numbered_list : 以「N.」「N、」「N．」开头，或正文中出现 ≥2 个编号条目
3. narrative     : 含中文句末标点「。」「；」（真项目名 0 命中）
4. narrative_run : 逗号 ≥3 且含复盘描述短语（双条件，避免误伤）

命中任意一条 → 该行**不入主表**，转 HUMAN_REVIEW_REQUIRED 事件
（保留完整原文供人工判定真名），绝不静默丢弃、也绝不入库。

换行处理
--------
换行本身合法，但会影响前端展示。统一在导入侧做**归一化**（\\n → 空格），
原始文本保留在 SourcePointer 的 raw payload 中，可回溯、无损。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "invalid_reason",
    "is_valid_project_name",
    "normalize_display_name",
    "PROJECT_NAME_INVALID_REASON",
]

# 编号条目：'1. ' / '1、' / '1．'
_NUMBERED_ITEM = re.compile(r"(?:^|(?<=\s))\d+\s*[.、．]\s*")
# 编号开头
_NUMBERED_HEAD = re.compile(r"^\s*\d+\s*[.、．]")

# 中文句末标点（真项目名实测 0 命中）
_SENTENCE_PUNCT = ("。", "；")

# 复盘描述短语（仅与"逗号≥3"组合时才判垃圾，单出现不判）
_NARRATIVE_PHRASES = (
    "核心业务", "涵盖", "擅长", "适配", "依托", "深耕", "聚焦",
    "专业化程度", "公信力", "协同管理", "一体化解决方案", "建立稳定合作",
    "区域聚焦度", "业务辐射", "暂未拓展", "暂未发现",
)


def normalize_display_name(raw: Any) -> str:
    """展示用归一化：折叠所有空白（含换行）为单个空格。

    不改变语义，只把 Excel 单元格里的软换行压平，避免前端渲染成多行。
    """
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def invalid_reason(name: Any) -> str | None:
    """返回垃圾名判定原因；合法项目名返回 None。

    注意：判据只看**结构**，不看长度、不看换行（见模块 docstring 实测表）。
    """
    if name is None:
        return "empty"
    value = str(name).strip()
    if not value:
        return "empty"

    flat = normalize_display_name(value)

    # 1) 编号列表：复盘描述的典型格式
    if _NUMBERED_HEAD.match(flat):
        return "numbered_list"
    if len(_NUMBERED_ITEM.findall(flat)) >= 2:
        return "numbered_list"

    # 2) 句末标点：真项目名不会带句号/分号
    if any(p in flat for p in _SENTENCE_PUNCT):
        return "narrative"

    # 3) 逗号密集 + 复盘描述短语（双条件）
    if flat.count("，") >= 3 and any(p in flat for p in _NARRATIVE_PHRASES):
        return "narrative_run"

    return None


def is_valid_project_name(name: Any) -> bool:
    return invalid_reason(name) is None


# 兼容旧调用名
PROJECT_NAME_INVALID_REASON = invalid_reason
