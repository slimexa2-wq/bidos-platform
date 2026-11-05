"""整标检查（验收清单 §二「标书制作 / 整标检查」缺口收尾）。

一句话职责
----------

对一份结构化的标书文档做**确定性规则检查**，在提交前找出人数 / 金额 / 日期
自相矛盾、缺章节、缺资格关键词等问题。检查对象是**传入的文档**（与 mock.json
的 documents 结构兼容），本轮不落库、不新增数据表。

反幻觉硬约束
------------

1. **规则引擎，无 LLM**。所有数字抽取、容差比较、日期解析都是确定性正则 +
   Python 计算（交接文档 §3.3 禁止 LLM 参与任何日期/金额计算）。
2. **没配置的规则不检查、解析不了的值不硬判**。meta 里缺 headcount / amount
   时对应规则跳过并给 notice 级提示，绝不拿默认值硬比。
3. 规则可单条开关（``rules`` 参数），便于不同项目按评标办法裁剪。

document 输入结构
-----------------

.. code-block:: python

    {
        "sections": [{"title": "技术方案", "content": "拟投入 45 人……"}],
        "meta": {
            "project_name": "XX医院劳务外包",
            "headcount": 45,          # 人数（人）
            "amount": 12000000.0,     # 金额（元）
            "dates": [                # 每项：{"label","start","end"} 或 "YYYY-MM-DD"
                {"label": "服务期", "start": "2026-10-01", "end": "2028-09-30"},
            ],
        },
    }

Issue 结构
----------

``{"severity": "error"|"warning"|"notice", "rule": str, "message": str, "location": str}``
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping

__all__ = [
    "DEFAULT_RULES",
    "DocumentChecker",
    "RULE_NAMES",
]

RULE_NAMES = (
    "headcount_consistency",   # 人数一致性
    "amount_consistency",      # 金额一致性（容差默认 1%）
    "date_consistency",        # 日期一致性（结束早于开始等）
    "required_keywords",       # 资格关键词缺失
    "required_sections",       # 章节缺失

    "project_name_consistency",
    "project_number_consistency",
    "amount_uppercase_consistency",
    "sub_amount_consistency",
    "period_consistency",
    "manager_consistency",
)

DEFAULT_RULES: dict[str, bool] = {name: True for name in RULE_NAMES}

#: 全文金额抽取：``12,345.67元`` / ``120万元`` / ``¥12,345.67`` / ``￥120``
_AMOUNT_YUAN = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*元")
_AMOUNT_WAN = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*万元")
_AMOUNT_SYMBOL = re.compile(r"[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
#: 全文人数抽取：``45人`` / ``45 名``（不匹配「名称」，要求数字紧跟 人/名）
_HEADCOUNT = re.compile(r"([0-9][0-9,]*)\s*(?:人|名)")
#: 日期抽取：YYYY-MM-DD / YYYY/MM/DD / YYYY年M月D日
_DATE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?")

_HEADCOUNT_MIN, _HEADCOUNT_MAX = 1, 100000
_AMOUNT_MIN = 100.0  # 低于此值视为非金额数字（如页码、编号），不参与比较

_PROJECT_NAME = re.compile(r"项目名称[：:]\s*([^\n，。；]+)")
_PROJECT_NUMBER = re.compile(r"项目编号[：:]\s*([a-zA-Z0-9\-]+)")
_AMOUNT_UPPERCASE = re.compile(r"(?:人民币)?大写[：:]?\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整正]+)")
_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*?合计[：:]\s*([0-9][0-9,]*(?:\.[0-9]+)?)", flags=re.DOTALL)
_PERIOD = re.compile(r"(?:工期|服务期限?|交货期)[：:]\s*([^\n，。；]+)")
_MANAGER_NAME = re.compile(r"(?:项目经理|项目负责人)[：:]\s*([^\n，。；\s]+)")
_MANAGER_ID = re.compile(r"(?:身份证号?码?)[：:]\s*([0-9Xx]{18})")



#: 「总数」语境标记。规则只能靠字面判断一个数字是不是**总人数**：
#:   「项目人员共计 180 人」→ 总数，不一致就是 error；
#:   「项目经理 1 名」      → 分项人数，与总人数本来就不该相等，只能是 warning。
#: 只认明确的汇总词，不用裸「共」「总」（太容易误命中）。
_TOTAL_MARKERS = (
    "共计",
    "合计",
    "总计",
    "总数",
    "总人数",
    "人员总数",
    "拟投入",
    "共投入",
    "累计",
    "投入人员",
)
#: 数字前多少个字符内出现标记即视为总数语境。
_TOTAL_WINDOW = 12



def _num2cn(num: float | None) -> str:
    if num is None:
        return ""
    num = round(float(num), 2)
    if num == 0:
        return "零元整"

    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟", "万", "拾", "佰", "仟", "亿", "拾", "佰", "仟"]

    int_part = int(num)
    dec_part = int(round((num - int_part) * 100))

    res = ""
    if int_part > 0:
        int_str = str(int_part)
        n = len(int_str)
        zero_flag = False
        for i in range(n):
            d = int(int_str[i])
            if d != 0:
                if zero_flag:
                    res += "零"
                    zero_flag = False
                res += digits[d] + units[n - i - 1]
            else:
                zero_flag = True
                if n - i - 1 == 4:
                    res += "万"
                    zero_flag = False
                elif n - i - 1 == 8:
                    res += "亿"
                    zero_flag = False
        res += "元"

    if dec_part == 0:
        res += "整"
    else:
        jiao = dec_part // 10
        fen = dec_part % 10
        if jiao > 0:
            res += digits[jiao] + "角"
        elif int_part > 0 and fen > 0:
            res += "零"
        if fen > 0:
            res += digits[fen] + "分"

    return res.replace("亿万", "亿")


def _to_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    """确定性日期解析；解析失败返回 None（不猜）。"""
    if isinstance(value, date) and not isinstance(value, type(date)):
        return value  # datetime 是 date 的子类，这里只需要日期部分
    text = str(value or "").strip()
    if not text:
        return None
    m = _DATE.search(text)
    if not m:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


class DocumentChecker:
    """整标检查规则引擎。

    参数：
        required_sections: 必须出现的章节名列表（标题规范化后互相包含即算命中）。
        required_keywords: 必须出现在全文的资格关键词列表。
        rules: 规则开关，``{"headcount_consistency": False}`` 形式；未提到的规则
               默认开启。
        amount_tolerance: 金额一致性的相对容差，默认 0.01（1%）。
    """

    def __init__(
        self,
        *,
        required_sections: Iterable[str] | None = None,
        required_keywords: Iterable[str] | None = None,
        rules: Mapping[str, bool] | None = None,
        amount_tolerance: float = 0.01,
    ) -> None:
        self._required_sections = [str(s) for s in (required_sections or []) if str(s).strip()]
        self._required_keywords = [str(k) for k in (required_keywords or []) if str(k).strip()]
        self._rules = dict(DEFAULT_RULES)
        if rules:
            for name, enabled in rules.items():
                if name in self._rules:
                    self._rules[name] = bool(enabled)
        self._amount_tolerance = max(0.0, float(amount_tolerance))

    # -- 主入口 -------------------------------------------------------------

    def check(self, document: dict) -> list[dict[str, str]]:
        """对传入文档跑全部启用的规则，返回 Issue 列表（无问题 → 空列表）。"""
        doc = document or {}
        sections = [
            {
                "title": str(s.get("title") or "").strip() if isinstance(s, dict) else str(s),
                "content": str(s.get("content") or "") if isinstance(s, dict) else "",
            }
            for s in (doc.get("sections") or [])
            if s is not None
        ]
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        full_text = "\n".join(f"{s['title']}\n{s['content']}" for s in sections)

        issues: list[dict[str, str]] = []
        if self._rules["headcount_consistency"]:
            issues.extend(self._check_headcount(sections, full_text, meta))
        if self._rules["amount_consistency"]:
            issues.extend(self._check_amounts(sections, full_text, meta))
        if self._rules["date_consistency"]:
            issues.extend(self._check_dates(meta))
        if self._rules["required_keywords"]:
            issues.extend(self._check_keywords(full_text))
        if self._rules["required_sections"]:
            issues.extend(self._check_sections(sections, full_text))

        if self._rules.get("project_name_consistency", False):
            issues.extend(self._check_project_name(sections, meta))
        if self._rules.get("project_number_consistency", False):
            issues.extend(self._check_project_number(sections, meta))
        if self._rules.get("amount_uppercase_consistency", False):
            issues.extend(self._check_amount_uppercase(sections, meta))
        if self._rules.get("sub_amount_consistency", False):
            issues.extend(self._check_sub_amounts(sections, meta))
        if self._rules.get("period_consistency", False):
            issues.extend(self._check_period(sections, meta))
        if self._rules.get("manager_consistency", False):
            issues.extend(self._check_manager(sections, meta))

        return issues

    # -- 规则 1：人数一致性 --------------------------------------------------

    def _check_headcount(
        self,
        sections: list[dict[str, str]],
        full_text: str,
        meta: dict[str, Any],
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        headcount = meta.get("headcount")
        if headcount is None:
            issues.append(
                {
                    "severity": "notice",
                    "rule": "headcount_consistency",
                    "message": "meta.headcount 未提供，无法做人数一致性检查（未用默认值硬比）。",
                    "location": "meta",
                }
            )
            return issues
        try:
            expected = float(headcount)
        except (TypeError, ValueError):
            issues.append(
                {
                    "severity": "notice",
                    "rule": "headcount_consistency",
                    "message": f"meta.headcount={headcount!r} 不是数字，人数一致性检查跳过。",
                    "location": "meta",
                }
            )
            return issues

        for section in sections:
            content = section["content"]
            for m in _HEADCOUNT.finditer(content):
                value = _to_number(m.group(1))
                if value is None or not (_HEADCOUNT_MIN <= value <= _HEADCOUNT_MAX):
                    continue
                if value == expected:
                    continue
                # 区分「总人数」与「分项人数」：前者不一致才是硬错误。
                prefix = content[max(0, m.start() - _TOTAL_WINDOW) : m.start()]
                if any(marker in prefix for marker in _TOTAL_MARKERS):
                    issues.append(
                        {
                            "severity": "error",
                            "rule": "headcount_consistency",
                            "message": (
                                f"文中出现人数「{m.group(0).strip()}」，与 meta.headcount"
                                f"（{expected:.0f}）不一致。"
                            ),
                            "location": f"章节「{section['title']}」",
                        }
                    )
                else:
                    # 没有汇总语境 → 可能是「项目经理 1 名」这类分项人数，
                    # 规则无法确知，降级为 warning 交人工，绝不硬判为错误。
                    issues.append(
                        {
                            "severity": "warning",
                            "rule": "headcount_consistency",
                            "message": (
                                f"文中出现人数「{m.group(0).strip()}」，与 meta.headcount"
                                f"（{expected:.0f}）不一致；该处没有「共计/拟投入」等总数语境，"
                                "可能是分项人数，请人工确认。"
                            ),
                            "location": f"章节「{section['title']}」",
                        }
                    )
        return issues

    # -- 规则 2：金额一致性 --------------------------------------------------

    def _check_amounts(
        self,
        sections: list[dict[str, str]],
        full_text: str,
        meta: dict[str, Any],
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        amount = meta.get("amount")
        if amount is None:
            issues.append(
                {
                    "severity": "notice",
                    "rule": "amount_consistency",
                    "message": "meta.amount 未提供，无法做金额一致性检查（未用默认值硬比）。",
                    "location": "meta",
                }
            )
            return issues
        try:
            expected = float(amount)
        except (TypeError, ValueError):
            issues.append(
                {
                    "severity": "notice",
                    "rule": "amount_consistency",
                    "message": f"meta.amount={amount!r} 不是数字，金额一致性检查跳过。",
                    "location": "meta",
                }
            )
            return issues
        tolerance = expected * getattr(self, "_amount_tolerance", 0.01)

        for section in sections:
            content = section["content"]
            hits: list[tuple[str, float]] = []
            # 先抽「万元」（避免「120万元」被 元 规则误读），再抽纯 元 / 货币符号
            wan_spans = list(_AMOUNT_WAN.finditer(content))
            for m in wan_spans:
                value = _to_number(m.group(1))
                if value is not None:
                    hits.append((m.group(0).strip(), value * 10000))
            covered = [(m.start(), m.end()) for m in wan_spans]

            def _in_wan(pos: int) -> bool:
                return any(start <= pos < end + 2 for start, end in covered)  # +2 覆盖「万元」两个字

            for pattern in (_AMOUNT_YUAN, _AMOUNT_SYMBOL):
                for m in pattern.finditer(content):
                    if _in_wan(m.start(1)):
                        continue
                    value = _to_number(m.group(1))
                    if value is None or value < _AMOUNT_MIN:
                        continue
                    hits.append((m.group(0).strip(), value))

            for text, value in hits:
                if abs(value - expected) > tolerance:
                    issues.append(
                        {
                            "severity": "error",
                            "rule": "amount_consistency",
                            "message": (
                                f"文中出现金额「{text}」（约 {value:.2f} 元），与 meta.amount"
                                f"（{expected:.2f} 元）偏差超过容差 {self._amount_tolerance:.0%}。"
                            ),
                            "location": f"章节「{section['title']}」",
                        }
                    )
        return issues

    # -- 规则 3：日期一致性 --------------------------------------------------

    def _check_dates(self, meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        dates = meta.get("dates")
        if not dates:
            issues.append(
                {
                    "severity": "notice",
                    "rule": "date_consistency",
                    "message": "meta.dates 为空，无法做日期一致性检查。",
                    "location": "meta",
                }
            )
            return issues
        for index, entry in enumerate(dates):
            if isinstance(entry, dict):
                label = str(entry.get("label") or entry.get("name") or f"第{index + 1}项")
                start_raw, end_raw = entry.get("start"), entry.get("end")
                if start_raw is None and end_raw is None:
                    continue
                start, end = _parse_date(start_raw), _parse_date(end_raw)
                if start_raw is not None and start is None:
                    issues.append(
                        {
                            "severity": "warning",
                            "rule": "date_consistency",
                            "message": f"「{label}」开始日期 {start_raw!r} 无法解析，未参与检查。",
                            "location": f"meta.dates[{index}]",
                        }
                    )
                if end_raw is not None and end is None:
                    issues.append(
                        {
                            "severity": "warning",
                            "rule": "date_consistency",
                            "message": f"「{label}」结束日期 {end_raw!r} 无法解析，未参与检查。",
                            "location": f"meta.dates[{index}]",
                        }
                    )
                if start and end and end < start:
                    issues.append(
                        {
                            "severity": "error",
                            "rule": "date_consistency",
                            "message": (
                                f"「{label}」结束日期（{end.isoformat()}）早于开始日期"
                                f"（{start.isoformat()}）。"
                            ),
                            "location": f"meta.dates[{index}]",
                        }
                    )
            else:
                if _parse_date(entry) is None:
                    issues.append(
                        {
                            "severity": "warning",
                            "rule": "date_consistency",
                            "message": f"日期 {entry!r} 无法解析，未参与检查。",
                            "location": f"meta.dates[{index}]",
                        }
                    )
        return issues

    # -- 规则 4：资格关键词缺失 ----------------------------------------------

    def _check_keywords(self, full_text: str) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        for keyword in self._required_keywords:
            if keyword not in full_text:
                issues.append(
                    {
                        "severity": "warning",
                        "rule": "required_keywords",
                        "message": f"全文未出现必含关键词「{keyword}」。", 
                        "location": "全文",
                    }
                )
        return issues


    # -- 新增规则 -----------------------------------------------------------

    def _check_project_name(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = meta.get("project_name")
        if not expected:
            return issues

        for section in sections:
            content = section["content"]
            for m in _PROJECT_NAME.finditer(content):
                found = m.group(1).strip()
                if found != expected:
                    issues.append({
                        "severity": "error",
                        "rule": "project_name_consistency",
                        "message": f"文中出现项目名称「{found}」，与 meta.project_name（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_project_number(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = meta.get("project_number")
        if not expected:
            return issues

        for section in sections:
            content = section["content"]
            for m in _PROJECT_NUMBER.finditer(content):
                found = m.group(1).strip()
                if found != expected:
                    issues.append({
                        "severity": "error",
                        "rule": "project_number_consistency",
                        "message": f"文中出现项目编号「{found}」，与 meta.project_number（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_amount_uppercase(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        amount = meta.get("amount")
        if amount is None:
            return issues

        expected_cn = _num2cn(amount)
        if not expected_cn:
            return issues

        # Remove suffix for robust comparison
        expected_cn_core = expected_cn.rstrip('整正')

        for section in sections:
            content = section["content"]
            for m in _AMOUNT_UPPERCASE.finditer(content):
                found = m.group(1).strip()
                found_core = found.rstrip('整正')
                if found_core != expected_cn_core:
                    issues.append({
                        "severity": "error",
                        "rule": "amount_uppercase_consistency",
                        "message": f"文中出现大写金额「{found}」，与总价大写（{expected_cn}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_sub_amounts(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        amount = meta.get("amount")
        if amount is None:
            return issues

        try:
            expected = float(amount)
        except (TypeError, ValueError):
            return issues

        tolerance = expected * getattr(self, "_amount_tolerance", 0.01)

        for section in sections:
            content = section["content"]
            for m in _SUB_AMOUNT_TOTAL.finditer(content):
                value = _to_number(m.group(1))
                if value is None:
                    continue
                if abs(value - expected) > tolerance:
                    issues.append({
                        "severity": "error",
                        "rule": "sub_amount_consistency",
                        "message": f"分项报价合计「{value}」，与总价（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_period(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = meta.get("period")
        if not expected:
            return issues

        for section in sections:
            content = section["content"]
            for m in _PERIOD.finditer(content):
                found = m.group(1).strip()
                if found != expected:
                    issues.append({
                        "severity": "error",
                        "rule": "period_consistency",
                        "message": f"文中出现工期/服务期限「{found}」，与 meta.period（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_manager(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected_name = meta.get("manager_name")
        expected_id = meta.get("manager_id")

        for section in sections:
            content = section["content"]

            if expected_name:
                for m in _MANAGER_NAME.finditer(content):
                    found = m.group(1).strip()
                    if found != expected_name:
                        issues.append({
                            "severity": "error",
                            "rule": "manager_consistency",
                            "message": f"文中出现项目经理「{found}」，与 meta.manager_name（{expected_name}）不一致。",
                            "location": f"章节「{section['title']}」",
                        })

            if expected_id:
                for m in _MANAGER_ID.finditer(content):
                    found = m.group(1).strip()
                    if found != expected_id:
                        issues.append({
                            "severity": "error",
                            "rule": "manager_consistency",
                            "message": f"文中出现身份证号「{found}」，与 meta.manager_id（{expected_id}）不一致。",
                            "location": f"章节「{section['title']}」",
                        })
        return issues

    # -- 规则 5：章节缺失 ----------------------------------------------------

    def _check_sections(
        self, sections: list[dict[str, str]], full_text: str
    ) -> list[dict[str, str]]:
        """章节完整性检查。

        两级判定（**修 2026-09-28 假阳性**）：

        1. **标题命中** → 该章节确实以独立标题存在，通过，不报。
        2. **仅正文命中** → 章节名出现在正文里，但没做成独立标题。规则无法确知它
           是否真的成章（也可能只是被提到），所以**不报缺失**——报缺失就是假阳性。
           改为汇总成一条 notice 交人工确认。
        3. **全文都找不到** → 才是真的缺失，报 warning。

        历史缺陷：此前只比对 ``sections[].title``。而纯文本入参会被归一成单个
        「正文（未分章节）」章节，于是**所有**必需章节必然报缺失——文档里明明写了
        也照报，规则直接退化成噪声（4 条假 warning 淹没真问题）。
        """
        issues: list[dict[str, str]] = []
        titles = [re.sub(r"\s+", "", s["title"]) for s in sections if s["title"]]
        body = re.sub(r"\s+", "", full_text)
        body_only: list[str] = []
        for required in self._required_sections:
            target = re.sub(r"\s+", "", required)
            if not target:
                continue
            if any(target in title or title in target for title in titles if title):
                continue  # 独立标题命中，通过
            if target in body:
                body_only.append(required)  # 只在正文出现，章节结构待人工确认
                continue
            issues.append(
                {
                    "severity": "warning",
                    "rule": "required_sections",
                    "message": f"缺少必需章节「{required}」。",
                    "location": "章节目录",
                }
            )
        if body_only:
            issues.append(
                {
                    "severity": "notice",
                    "rule": "required_sections",
                    "message": (
                        "以下必需章节未作为独立标题出现，仅在正文中被提及，"
                        "章节结构需人工确认："
                        + "、".join(f"「{s}」" for s in body_only)
                        + "。"
                    ),
                    "location": "章节目录",
                }
            )
        return issues
