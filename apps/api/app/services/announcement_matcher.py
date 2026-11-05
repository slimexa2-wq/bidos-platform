"""公告 ↔ 重点客户/品类 匹配器（W4）。

只做检索与证据组织：给定公告文本（标题+采购人）与重点客户名单，输出三级匹配
结果与品类命中。不写库、不推送——候选一律交人工确认后才有下游动作。

三级口径（与 docs/DATA_DICTIONARY 的实体归一约定一致）：
  exact      公告文本原样包含重点客户全称；
  normalized 归一化（全半角括号/空白/地名前缀）后包含；
  alias      别名表命中（别名同样走归一化）。

品类关键词取自盲投筛选关键词清单口径（劳务派遣/外包/安保/保洁/社工/产线等），
双条件（客户命中 + 品类命中）才提级推送。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 品类关键词：与盲投渠道表「盲投关键词」sheet 的业务口径对齐
CATEGORY_KEYWORDS: tuple[str, ...] = (
    "劳务派遣",
    "劳务外包",
    "劳务",
    "外包",
    "劳务用工",
    "岗位外包",
    "保安",
    "安保",
    "保洁",
    "物业",
    "社工",
    "社会工作者",
    "产线",
    "派遣",
    "食堂",
    "后勤",
    "小时工",
    "秩序维护",
)

# 地名前缀只剥一层；「四川」剥掉后还允许再剥「成都」这类市前缀的场景交给归一化后的包含匹配
_REGION_PREFIX = re.compile(
    r"^(四川省?|成都市?|宜宾市?|绵阳市?|德阳市?|眉山市?|内江市?|乐山市?|泸州市?|重庆市?|自贡市?|南充市?|达州市?)"
)
_BRACKETS = re.compile(r"[（）()【】\[\]「」『』]")
_SPACES = re.compile(r"\s+")

_FULLWIDTH_TRANS = str.maketrans({
    "（": "(",
    "）": ")",
    "／": "/",
    "　": " ",
    "，": ",",
})


def normalize_name(raw: str | None) -> str:
    """实体/公告归一化：全角→半角、去空白与括号、剥一层地名前缀、小写。"""
    if not raw:
        return ""
    s = str(raw).strip().translate(_FULLWIDTH_TRANS)
    s = _BRACKETS.sub("", s)
    s = _SPACES.sub("", s)
    s = _REGION_PREFIX.sub("", s)
    return s.lower()


def contains_normalized(haystack: str, needle: str) -> bool:
    """归一化后的包含判断；needle 过短（<4 字）不判命中，避免「保安」这类词误报。"""
    n = normalize_name(needle)
    if len(n) < 4:
        return False
    return n in normalize_name(haystack)


@dataclass(frozen=True)
class WatchTarget:
    """重点客户：名称 + 别名 + 告警路由（意向跟进单位/分公司）。"""

    name: str
    aliases: tuple[str, ...] = ()
    alert_route: str = ""

    def terms(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class MatchResult:
    """一条公告对一个重点客户的匹配证据。"""

    target: WatchTarget
    tier: str  # exact | normalized | alias
    matched_term: str
    category_hit: bool
    category_terms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def escalate(self) -> bool:
        """双条件提级：客户命中且品类命中。"""
        return self.category_hit


def match_announcement(
    announcement_text: str,
    targets: list[WatchTarget],
    *,
    category_keywords: tuple[str, ...] = CATEGORY_KEYWORDS,
) -> list[MatchResult]:
    """公告文本对重点客户清单做三级匹配。

    同一目标多级别命中时取最高级（exact > normalized > alias）；
    别名与名称命中互相独立，多个目标命中则各出一条（人工裁决归属）。
    """
    hits: list[MatchResult] = []
    cat_terms = tuple(k for k in category_keywords if k in announcement_text)
    tier_rank = {"exact": 3, "normalized": 2, "alias": 1}
    best: dict[str, MatchResult] = {}
    for target in targets:
        for rank, term in enumerate(target.terms()):
            is_name = rank == 0
            if is_name:
                # 名称级防误报闸：归一化后 <4 字的名称（哨兵值 "/"、"-"、单字简称）
                # 直接跳过——exact 层是原文包含判断，没有它 "/" 会命中一切公告
                # （2026-09-30 真库事故：291 条公告被吸附到甲方="/" 的项目上）。
                # 别名不受此闸：别名是业务白名单显式登记的简称。
                if len(normalize_name(term)) < 4:
                    continue
                tier = "exact" if (term and term in announcement_text) else "normalized"
                hit = bool(term) and (term in announcement_text or contains_normalized(announcement_text, term))
            else:
                # 别名即使原文原样出现也记 alias：目标全称并未在公告里出现。
                # 别名是业务白名单显式登记的简称，原文命中不受 4 字防误报闸限制。
                tier = "alias"
                hit = bool(term) and (term in announcement_text or contains_normalized(announcement_text, term))
            if not hit:
                continue
            result = MatchResult(
                target=target,
                tier=tier,
                matched_term=term,
                category_hit=bool(cat_terms),
                category_terms=cat_terms,
            )
            prev = best.get(target.name)
            if prev is None or tier_rank[tier] > tier_rank[prev.tier]:
                best[target.name] = result
    hits.extend(best.values())
    # 双条件优先、匹配级别次之，输出稳定顺序
    hits.sort(key=lambda r: (not r.escalate, -tier_rank[r.tier], r.target.name))
    return hits
