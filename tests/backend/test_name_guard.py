"""name_guard 回归测试：垃圾名拦截 + **零误伤**。

为什么要固化成测试
------------------
2026-09-24 事故：真表「投标项目汇总9.17.xlsx」的「未中标项目」sheet 尾部
第 140~173 行是一整块**战略/工作计划**，被当成项目导入并渲染到竞对分析页。

第一版修复凭直觉写了「含换行 → 垃圾」「长度>60 → 垃圾」，在真实数据上
误伤严重（2274 条真名里误杀 28 条）。本测试把**真实数据样本**固化下来：
- GARBAGE_FROM_REAL_DATA：12 条必须被拦截
- LEGIT_BUT_TRICKY：28 条（含换行 / 超 60 字）必须**放行**

任何人想加新规则，必须先让这两组全绿。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.name_guard import (  # noqa: E402
    invalid_reason,
    is_valid_project_name,
    normalize_display_name,
)


# ── 真实数据夹具（来自 bidos 真库，勿手改）─────────────────────────
# 12 条战略/复盘文本：必须全部被拦截
GARBAGE_FROM_REAL_DATA = [
    "1. 按 “项目类型（企业 / 机关 / 酒店）+ 区域（成都 / 宜宾 / 其他）” 分类\n2. 知识库内容：投标方案模板（含评分点适配）、报价参考标准（成本区间、溢价范围）、竞争对手档案（核心优势、投标习惯）\n3. 每月更新 1 次，确保内容时效性",
    "1. 新增 “竞争难度评估” 环节（投标前 3 天）\n2. 评估指标：竞争对手数量（＞8 家为高难度）、头部企业参与度（≥2 家为高难度）、自身匹配度（资质 / 案例匹配率＜60% 为高难度）\n3. 中标概率＜30% 的项目，提交总经理办公会审议后决定是否参与",
    "1. 将成都、宜宾定位为 A 类核心市场\n2. 投入 60% 以上投标资源\n3. 重点突破企业类 + 机关类项目",
    "1. 对项目数＜3 个的城市，采取 “选择性参与”\n2. 仅投标利润率≥行业均值、竞争度＜5 家的项目",
    "1. 以安保服务为核心业务，专业化程度高\n2. 可提供定制化安保方案，适配不同场景需求\n3. 具备安保人员标准化培训和管理体系",
    "1. 聚焦物业全链条服务，涵盖保洁、秩序维护、设施运维\n2. 擅长大型国企 / 集团类项目的整体服务外包\n3. 具备多项目协同管理能力，可承接复合型外包需求",
    "1. 以人才服务为核心，涵盖岗位外包、劳务派遣、人才培训\n2. 具备国企背景，公信力强，适配政府 / 国企类人才需求\n3. 可提供 “人才服务 + 政策咨询” 一体化解决方案",
    "1. 选取宜宾绿城、四川奕晟保安等 3-5 家头部企业\n2. 重点分析其报价策略（溢价率、成本控制）、服务方案（本地化适配、风险防控）、资质优势（认证资质、成功案例）\n3. 形成《头部企业对标分析报告》",
    "1. 投标文件中突出 “本地服务团队”（人员配置、办公地址）、“快速响应”（2 小时内到场、24 小时服务热线）、“本地化资源”（本地供应商合作、政策适配）\n2. 针对成都、宜宾项目，单独制作本地化服务附件",
    "1. 新区域评估维度：竞争难度（头部企业占比）、市场规模（年项目量）、利润率（平均毛利率）\n2. 评估得分≥70 分方可进入，＜60 分直接排除",
    "1. 确定帮扶对象：宜宾、市场、龙泉公司（未中标项目占比超 60%）\n2. 派遣投标专家驻点指导（每月 3-5 天）\n3. 重点提升方案编制（逻辑架构、亮点提炼）和报价能力（成本核算、报价策略）",
    "1. 建立 “未公示” 项目跟踪台账\n2. 跟踪方式：政府招标网监测（每日 1 次）、甲方电话回访（每周 1 次）、行业人脉打听（每月 1 次）\n3. 信息获取后及时更新竞争分析数据库",
]

# 28 条"看起来像垃圾但其实是真项目名"：必须全部放行
# 特征：含 Excel 软换行（22 条）、超 60 字（12 条，最长 87 字）
LEGIT_BUT_TRICKY = [
    "仁寿首创水务有限公司\n县城、视高片区劳务外包服务",
    "仁寿首创水务有限公司\n富加、汪洋片区劳务外包服务",
    "四川长江造纸仪器有限责任公司\n生产区和办公区保洁服务",
    "四川天府龙芽股份有限公司\n“人力资源服务”合作商采购项目",
    "四川宏达股份有限公司\n股份公司本部劳务派遣（驾驶员）比选采购",
    "乐山市校地共建产教融合基地及配套设施项目用房运营2026年\n劳务外包服务采购项目",
    "四川天府检验检测集团有限公司采购物业服务\n比选项目",
    "成都交投高善成实业有限公司GX2022-02（071/05）号地块住宅、商业及配套项目2026-2027年度营销类服务外包",
    "四川省机场集团有限公司成都空港大酒店、四川省机场集团有限公司天府空港云享酒店分公司、四川省机场集团有限公司西昌空港云享酒店分公司联合采购2026年岗位外包客房服务项目",
    "四川蒙顶山理真茶业有限公司\n整体劳务外包服务采购项目",
    "中铁文旅健康仁寿分公司劳务外\n包服务采购项目",
    "四川省烟草公司眉山市公司2026年食堂及物业管理业务外包服务\n采购项目",
    "四川省机场集团有限公司成都空港大酒店、四川省机场集团有限公司天府空港云享酒店分公司、四川省机场集团有限公司西昌空港云享酒店分公司联合采购2025年岗位外包项目01包",
    "四川省机场集团有限公司成都空港大酒店、四川省机场集团有限公司天府空港云享酒店分公司、四川省机场集团有限公司西昌空港云享酒店分公司联合采购2025年岗位外包项目02包",
    "两场（双流+天府）生产、行政区域清洁及生产劳务量化人力外包服务采购项目/采购包1：两场（双流+天府）生产、行政区域清洁人力外包服务采购项目",
    "两场（双流+天府）生产、行政区域清洁及生产劳务量化人力外包服务采购项目/采购包2：两场（双流+天府）生产劳务量化人力外包服务采购项目",
    "国网信通产业集团四川中电启明星信息技术有限公司2025年产业单位第十次服务类授权竞争性谈判采购【网格电话平台基础功能优化技术服务】",
    "岷江东风岩航电枢纽工程\n设计施工五星联合体总承包项目部技术服务",
    "华电抽蓄工程管理（成都）有限公司工程（后勤服务类班车）华电抽蓄工程管理（成都）有限公司2025-2028年车辆驾驶及综合业务外包服务",
    "四川港投经济技术研究有限公司\n2026年度综合服务项目采购（第二次）",
    "眉山农村商业银行股份有限公司\n总行办公大楼物业服务采购项目",
    "香飘飘四川食品有限公司\n2025-2026年度生产线劳务外包项目",
    "宜宾天亿新材料科技有限公司\n2026年年度转运装卸及破碎磨粉劳务服务项目",
    "锂电新材2026年度劳务服务采购项目\n包1托盘清洗、叉车转运、产成品出入库管理",
    "锂电新材2026年度劳务服务采购项目\n包2物料搬运",
    "重庆茶园馨乐庭连心酒店\n2026年10月-2027年10月安保服务采购",
    "成都空港城市空间物业服务有限公司华润九里儿童公园、万家社区公园和小游园项目环境保洁服务、绿化养护服务、公共秩序维护服务采购项目",
    "成都凡易物流有限责任公司\n2025年-2026年劳务派遣服务项目",
]


class TestGarbageDetection(unittest.TestCase):
    """垃圾判定：真实数据里的 12 条必须全部命中。"""

    def test_all_real_garbage_caught(self):
        missed = [s for s in GARBAGE_FROM_REAL_DATA if invalid_reason(s) is None]
        self.assertEqual(
            missed, [],
            f"以下真实垃圾文本未被拦截（漏网）：{missed}",
        )

    def test_numbered_list_detected(self):
        self.assertEqual(
            invalid_reason("1. 以安保服务为核心业务，专业化程度高\n2. 可提供定制化方案"),
            "numbered_list",
        )

    def test_narrative_punctuation_detected(self):
        self.assertEqual(invalid_reason("某公司提供全链条服务。"), "narrative")
        self.assertEqual(invalid_reason("甲；乙"), "narrative")

    def test_narrative_run_detected(self):
        s = "聚焦物业全链条服务，涵盖保洁，秩序维护，设施运维，具备协同能力"
        self.assertEqual(invalid_reason(s), "narrative_run")

    def test_empty_detected(self):
        for v in (None, "", "   ", "\n\t"):
            self.assertEqual(invalid_reason(v), "empty")


class TestZeroFalsePositive(unittest.TestCase):
    """零误伤：真项目名即使含换行/超长，也必须放行。"""

    def test_all_legit_names_pass(self):
        wrongly_rejected = [s for s in LEGIT_BUT_TRICKY if not is_valid_project_name(s)]
        self.assertEqual(
            wrongly_rejected, [],
            f"以下真实项目名被误杀：{wrongly_rejected}",
        )

    def test_multiline_is_not_garbage(self):
        """换行是 Excel 单元格排版，不是垃圾信号（曾误伤 22 条）。"""
        s = "仁寿首创水务有限公司\n县城、视高片区劳务外包服务"
        self.assertIsNone(invalid_reason(s))

    def test_long_name_is_not_garbage(self):
        """联合采购名可以很长（真名最长 87 字），不是垃圾信号（曾误伤 9 条）。"""
        s = ("四川省机场集团有限公司成都空港大酒店、四川省机场集团有限公司"
             "天府空港云享酒店分公司、四川省机场集团有限公司西昌空港云享酒店"
             "分公司联合采购2026年岗位外包客房服务项目")
        self.assertTrue(len(s) > 60)
        self.assertIsNone(invalid_reason(s))

    def test_core_business_keyword_alone_is_not_garbage(self):
        """「核心业务」单出现不判垃圾：真名里有「非核心业务外包服务」。"""
        s = "四川明星电力股份有限公司遂宁市明星酒店有限公司2026年非核心业务外包服务"
        self.assertIsNone(invalid_reason(s))

    def test_normal_names_pass(self):
        for s in (
            "乐山海友7.0酒店服务团队劳务外包项目（第二次）公开招标",
            "岷江东风岩航电枢纽工程设计施工五星联合体总承包项目部技术服务",
            "宜宾智轨T1、T2、T4线保洁服务项",
        ):
            self.assertIsNone(invalid_reason(s), s)


class TestNormalizeDisplayName(unittest.TestCase):
    """展示归一化：折叠换行，但不改语义。"""

    def test_collapses_newline(self):
        self.assertEqual(
            normalize_display_name("仁寿首创水务有限公司\n县城、视高片区劳务外包服务"),
            "仁寿首创水务有限公司 县城、视高片区劳务外包服务",
        )

    def test_collapses_multiple_whitespace(self):
        self.assertEqual(normalize_display_name("  A \n\t B  "), "A B")

    def test_idempotent_on_clean(self):
        s = "香飘飘四川食品有限公司 2025-2026年度生产线劳务外包项目"
        self.assertEqual(normalize_display_name(s), s)


if __name__ == "__main__":
    unittest.main()
