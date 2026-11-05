"""业务类别拆分：把真表「项目类型」这个混合列拆成业务维度与客户维度。

## 为什么需要这个模块

真表 `投标项目汇总9.17.xlsx` 等文件的「项目类型」列是**混合列**：

* 业务类型：劳务外包 / 劳务派遣 / 物业服务 / 保安服务 …（描述我们提供什么）
* 客户类型：企业 / 机关 / 酒店 …（描述甲方是什么单位）

`field_mapping.yaml` 因此把它映射到 `meta.business_category`（jsonb）而不是
`projects.project_type`，并标注「待业务确认」。结果 `projects.project_type`
全库 0/2257，连带两个下游功能空转：

* `services/similarity.py` 把 `project_type` 当**类别特征、权重 0.30** → 一直在空转
* `scripts/build_entity_graph.py` 的竞对 `specialties` 被迫改用 business_category

## 拆分规则（确定性、可审计、不编造）

1. 空值 / 占位符（`/` `-` `无` …）→ 不产出
2. 命中**客户类型白名单**（精确匹配）→ 只产出 client_category，不产出 project_type
3. 其余 → 按 `BUSINESS_FAMILY_RULES` **有序**关键词表归一为业务族
4. 一条规则都不命中 → `matched=False`，原值直传 project_type（**不猜**）

原始值永远保留在 `meta_fields.business_category`，归一结果与原始值不同时
额外写 `meta_fields.project_type_raw` 便于回溯。
"""

from __future__ import annotations

import re

# 客户类型白名单：精确匹配。描述的是「甲方是什么单位」，不是「我们提供什么服务」。
CLIENT_CATEGORIES = frozenset(
    {"企业", "机关", "酒店", "学校", "医院", "事业单位", "政府", "部队", "团体组织"}
)

# 占位符：不是类型
PLACEHOLDERS = frozenset({"/", "-", "—", "无", "其他", "待定"})

# 有序关键词规则表：**先匹配者胜**。顺序有语义，不要随意调换。
#
# 顺序理由（每条都有真实取值支撑）：
#   1. 「派遣」必须先于「劳务」：否则「劳务派遣」被并进劳务外包（口径错误）
#   2. 具体业务词（保安/保洁/物业/食堂/后勤/招聘/装卸）必须先于「外包」：
#      否则「保安外包」会被并进劳务外包，丢掉真实业务属性
#   3. 「外包」必须先于「人力」：「人力外包」「人力资源外包」在招投标语境里
#      是**用工外包**（我方派人做甲方的活），不是 HR 职能外包，
#      而「人力资源服务」「人力服务」不含「外包」，仍归人力资源服务
#   4. 「劳务外包」规则里**不放「用工」**：真值里只有「灵活用工（服务）」含
#      「用工」且不含「劳务/外包」，它属于人力资源服务而非劳务外包
#   5. 「其他服务」兜底放最后
BUSINESS_FAMILY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"派遣"), "劳务派遣"),
    (re.compile(r"保安|安保|秩序|门卫"), "保安服务"),
    (re.compile(r"保洁|清洁|环卫"), "保洁服务"),
    (re.compile(r"物业"), "物业服务"),
    (re.compile(r"食堂|后厨"), "食堂服务"),
    (re.compile(r"后勤"), "后勤服务"),
    (re.compile(r"招聘|猎头"), "招聘服务"),
    (re.compile(r"装卸|搬运|叉车"), "装卸搬运"),
    (re.compile(r"劳务|外包"), "劳务外包"),
    (re.compile(r"人力|人事|社保|灵活用工"), "人力资源服务"),
    (
        re.compile(r"服务|管理|运营|运维|技术|检测|培训|咨询|维护|工程|生产"),
        "其他服务",
    ),
)


def classify(raw: object) -> tuple[str | None, str | None, bool]:
    """把原始 business_category 拆成 (project_type, client_category, matched)。

    matched=False 表示没有命中任何规则、project_type 是原值直传 ——
    调用方应把这批值单独报出来等业务确认，而不是当成已归一。
    """
    if raw is None:
        return None, None, False
    value = re.sub(r"\s+", " ", str(raw)).strip()
    if not value or value in PLACEHOLDERS:
        return None, None, False
    if value in CLIENT_CATEGORIES:
        return None, value, True
    for pattern, family in BUSINESS_FAMILY_RULES:
        if pattern.search(value):
            return family, None, True
    return value, None, False
