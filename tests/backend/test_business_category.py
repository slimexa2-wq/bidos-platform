"""业务类别拆分回归测试 —— 夹具取自真库真实取值（2026-09-28 实测分布）。

为什么必须有这组测试：
`business_category` 是**混合列**，拆分规则靠有序关键词表实现，顺序有语义。
任何人调整 `BUSINESS_FAMILY_RULES` 的顺序或加新规则，都可能把
「劳务派遣」并进「劳务外包」，或把「企业/机关」这类客户类型误写进
`projects.project_type`。这组测试就是那道闸门。

用例分两组：
  MUST_MATCH    —— 必须命中的真实取值（含最容易踩的顺序陷阱）
  MUST_EXCLUDE  —— 必须**不**进 project_type 的值（客户类型 / 占位符）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.business_category import classify  # noqa: E402

# (原始值, 期望的业务族)。取自真库 business_category 的实际取值与频次。
MUST_MATCH: tuple[tuple[str, str], ...] = (
    # 量最大的几个，必须准确
    ("劳务外包", "劳务外包"),
    ("劳务派遣", "劳务派遣"),
    ("劳务服务", "劳务外包"),
    ("服务外包", "劳务外包"),
    ("业务外包", "劳务外包"),
    ("岗位外包", "劳务外包"),
    ("人力外包", "劳务外包"),
    ("外包服务", "劳务外包"),
    ("人力资源外包", "劳务外包"),
    ("劳务服务外包", "劳务外包"),
    ("劳务用工", "劳务外包"),
    ("派遣、外包", "劳务派遣"),  # 顺序陷阱：含「外包」，但「派遣」必须先命中
    ("残疾人劳务派遣", "劳务派遣"),
    ("劳务派遣（残疾人）", "劳务派遣"),
    ("残疾人派遣", "劳务派遣"),
    # 秩序/安保 归保安服务
    ("安保服务", "保安服务"),
    ("保安服务", "保安服务"),
    ("秩序维护", "保安服务"),
    ("秩序服务", "保安服务"),
    ("保安保洁服务", "保安服务"),  # 顺序陷阱：同时含「保安」「保洁」，保安先命中
    # 保洁 / 物业 / 食堂 / 后勤
    ("保洁服务", "保洁服务"),
    ("清洁服务", "保洁服务"),
    ("保洁外包", "保洁服务"),
    ("物业服务", "物业服务"),
    ("物业管理", "物业服务"),
    ("食堂服务", "食堂服务"),
    ("后厨外包", "食堂服务"),
    ("后勤服务", "后勤服务"),
    # 招聘 / 装卸 / 人力
    ("招聘服务", "招聘服务"),
    ("代理招聘", "招聘服务"),
    ("猎头服务", "招聘服务"),
    ("装卸服务", "装卸搬运"),
    ("物资搬运", "装卸搬运"),
    ("人力资源服务", "人力资源服务"),
    ("人事管理服务", "人力资源服务"),
    ("灵活用工服务", "人力资源服务"),
    ("社保托管", "人力资源服务"),
    # 兜底族
    ("运营服务", "其他服务"),
    ("运维服务", "其他服务"),
    ("技术服务", "其他服务"),
    ("检测服务", "其他服务"),
    # 空白折叠：真表里有「劳务外包\n劳务派遣」这种多行值
    ("劳务外包\n劳务派遣", "劳务派遣"),
    ("  物业服务  ", "物业服务"),
)

# 必须**不**产出 project_type 的值（客户维度 / 占位符 / 空）
MUST_EXCLUDE: tuple[str, ...] = (
    "企业",
    "机关",
    "酒店",
    "/",
    "-",
    "无",
    "待定",
    "",
    "   ",
)


class BusinessCategoryClassifyTest(unittest.TestCase):
    def test_must_match_real_values(self) -> None:
        failures: list[str] = []
        for raw, expected in MUST_MATCH:
            project_type, client_category, matched = classify(raw)
            if project_type != expected or client_category is not None or not matched:
                failures.append(
                    f"{raw!r} → project_type={project_type!r} client={client_category!r} "
                    f"matched={matched}，期望 {expected!r}"
                )
        self.assertEqual(failures, [], "业务族归一结果不符合预期：\n" + "\n".join(failures))

    def test_must_exclude_client_and_placeholder_values(self) -> None:
        failures: list[str] = []
        for raw in MUST_EXCLUDE:
            project_type, client_category, _ = classify(raw)
            if project_type is not None:
                failures.append(f"{raw!r} 不应进 project_type，实得 {project_type!r}")
            # 客户类型必须被识别出来，而不是被丢弃
            if raw in {"企业", "机关", "酒店"} and client_category != raw:
                failures.append(f"{raw!r} 应识别为 client_category，实得 {client_category!r}")
        self.assertEqual(failures, [], "排除规则不符合预期：\n" + "\n".join(failures))

    def test_unmatched_value_passes_through_verbatim(self) -> None:
        """一条规则都不命中时，原值直传且 matched=False —— 绝不猜、绝不丢。"""
        project_type, client_category, matched = classify("资格预审")
        self.assertEqual(project_type, "资格预审")
        self.assertIsNone(client_category)
        self.assertFalse(matched, "未命中规则必须标记 matched=False，供业务复核")

    def test_none_and_placeholder_are_not_reported_as_unmatched(self) -> None:
        for raw in (None, "", "/", "无"):
            _, _, matched = classify(raw)
            self.assertFalse(matched)

    def test_dispatch_beats_labour_ordering(self) -> None:
        """顺序守卫：这条一旦失败，说明规则表被重排了，会污染劳务外包口径。"""
        self.assertEqual(classify("劳务派遣")[0], "劳务派遣")
        self.assertEqual(classify("劳务外包")[0], "劳务外包")


if __name__ == "__main__":
    unittest.main()
