"""证据聚合器 / 相似项目检索的真实行为测试（P4）。

本机没有 PostgreSQL，因此用 **SQLite 内存库 + 生产 ORM 模型** 造夹具，验证：

1. 有历史数据时 support / caution / unknown 三组都能产出，且 support / caution 的
   每条证据都带**真实** Source Pointer；
2. 数据缺失时证据进 unknown，**绝不编造**（support 为空）；
3. 低置信度证据不进 support / caution，而是降级到 unknown 并写明原因；
4. ``human_decision_required`` 恒为 True（含 demo 降级路径）；
5. 报价问题**必须同时**产出支持高价与支持压价的证据，不能只给一边；
6. 检索层不可用时相似项目降级为确定性打分，并标注「语义排序暂不可用」；
7. 数据源不可用时抛显式异常，不返回空证据假装「没有数据」。
"""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.domain import SearchHit, SourcePointer as DomainSourcePointer, SourceType  # noqa: E402
from app.repositories.models import (  # noqa: E402
    Base,
    CompetitorStat,
    Entity,
    EntityAlias,
    Organization,
    Project,
    ProjectResult,
)
from app.repositories.models import SourcePointer as SourcePointerRow  # noqa: E402
from app.services.decision_support import DecisionSupportService  # noqa: E402
from app.services.evidence_aggregator import (  # noqa: E402
    EvidenceAggregator,
    EvidenceSourceUnavailable,
    SqlEvidenceSource,
    classify_question,
)
from app.services.similarity import SimilarityService  # noqa: E402


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):  # noqa: ANN001, ARG001
    """生产 ORM 用 JSONB；SQLite 测试里降级为 JSON，保证 DDL 可建。"""
    return "JSON"


NOW = datetime.now(timezone.utc)

#: 本项目预算 1200 万（用于中标价/预算比率规则）
BUDGET = 12_000_000.0

COMPETITOR = "宝航人力资源有限公司"


class _BrokenSearch:
    """模拟「检索层在，但向量服务挂了」。"""

    class _Cap:
        vector = True
        reasons: dict = {}

    def effective_capability(self):  # noqa: ANN201
        return self._Cap()

    def search(self, query, limit=20):  # noqa: ANN001, ARG002
        raise RuntimeError("BGE-M3 服务不可达")


class _VectorSearch:
    """模拟「向量检索可用」，只对指定项目给语义分。"""

    class _Cap:
        vector = True
        reasons: dict = {}

    def __init__(self, scores: dict) -> None:
        self._scores = scores

    def effective_capability(self):  # noqa: ANN201
        return self._Cap()

    def search(self, query, limit=20):  # noqa: ANN001, ARG002
        return [
            SearchHit(
                id=pid,
                type="project",
                title=pid,
                snippet="",
                score=score,
                source=DomainSourcePointer(
                    id=f"sp-{pid}",
                    source_type=SourceType.internal,
                    source_name="语义召回",
                ),
            )
            for pid, score in self._scores.items()
        ]


class _NoVectorSearch:
    """模拟「检索层在，但没有 embedding 能力」。"""

    class _Cap:
        vector = False
        reasons = {"vector": "pgvector 扩展未启用"}

    def effective_capability(self):  # noqa: ANN201
        return self._Cap()

    def search(self, query, limit=20):  # noqa: ANN001, ARG002
        raise AssertionError("无向量能力时不应调用检索层")


class EvidenceAggregatorTests(unittest.TestCase):
    # -- 夹具 ---------------------------------------------------------------

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _source(self) -> SqlEvidenceSource:
        return SqlEvidenceSource(self.engine)

    def _aggregator(self, **kwargs) -> EvidenceAggregator:
        return EvidenceAggregator(self._source(), **kwargs)

    def _seed_base(self) -> None:
        with Session(self.engine) as session:
            session.add(Organization(id="org1", name="四川祥能人力资本服务有限公司"))
            session.add(
                Entity(
                    id="e-baohang",
                    organization_id="org1",
                    entity_type="competitor",
                    canonical_name=COMPETITOR,
                    metadata_={},
                )
            )
            session.add(EntityAlias(id="al-baohang", entity_id="e-baohang", alias="宝航"))
            session.commit()

    def _add_project(
        self,
        session: Session,
        *,
        project_id: str,
        name: str,
        client: str | None = "成都天府医院",
        region: str | None = "成都",
        project_type: str | None = "劳务外包",
        budget: float | None = None,
        deadline: datetime | None = None,
        scale: int | None = None,
        pointer: bool = True,
        result: str | None = None,
        winner: str | None = None,
        our_price: float | None = None,
        winning_price: float | None = None,
        reasons: list[str] | None = None,
    ) -> None:
        metadata: dict = {}
        if scale:
            metadata["person_count"] = scale
        metadata["服务期限"] = "24个月"
        session.add(
            Project(
                id=project_id,
                organization_id="org1",
                name=name,
                client_name=client,
                region=region,
                project_type=project_type,
                workflow_stage="结果复盘" if result else "摘要评估",
                status=result or "进行中",
                budget=budget,
                deadline=deadline,
                source_of_truth="投标项目计划9.13.xlsx",
                metadata_=metadata,
            )
        )
        if pointer:
            session.add(
                SourcePointerRow(
                    id=f"sp-{project_id}",
                    project_id=project_id,
                    source_name="投标项目计划9.13.xlsx",
                    source_type="sheet",
                    trust_level="internal",
                    sheet_name="项目台账",
                    row_id=project_id,
                    original_text=name,
                    metadata_={},
                )
            )
        if result:
            session.add(
                ProjectResult(
                    id=f"r-{project_id}",
                    project_id=project_id,
                    result=result,
                    winner=winner,
                    winning_price=winning_price,
                    our_price=our_price,
                    reasons=list(reasons or []),
                    lessons=[],
                    reviewed=bool(reasons),
                    result_date=date(2025, 6, 1),
                    metadata_={},
                )
            )

    def _seed_rich(self) -> None:
        """目标项目 + 8 个同类型历史结果（4 失标偏高 / 3 中标 / 1 非价格失标）+ 竞对。"""
        self._seed_base()
        with Session(self.engine) as session:
            self._add_project(
                session,
                project_id="p-target",
                name="成都天府医院劳务外包服务项目（二期）",
                budget=BUDGET,
                deadline=NOW + timedelta(days=10),
                scale=150,
            )
            # 4 个失标：我司报价高于中标价，其中 3 个由竞对宝航中标
            for index, (our, winning) in enumerate(
                ((9_000_000, 8_000_000), (7_500_000, 7_000_000),
                 (6_200_000, 5_800_000), (5_000_000, 4_900_000)),
                start=1,
            ):
                self._add_project(
                    session,
                    project_id=f"h-lost-{index}",
                    name=f"成都天府医院劳务外包服务项目（历史{index}）",
                    scale=140,
                    result="未中标",
                    winner=COMPETITOR if index <= 3 else "其他供应商",
                    our_price=our,
                    winning_price=winning,
                    reasons=["报价偏高，价格分落后", "成本压力大"] if index == 4 else ["报价偏高"],
                )
            # 3 个中标：我司报价不高于中标价
            for index, (our, winning) in enumerate(
                ((4_000_000, 4_100_000), (3_600_000, 3_700_000), (5_500_000, 5_600_000)),
                start=1,
            ):
                self._add_project(
                    session,
                    project_id=f"h-won-{index}",
                    name=f"成都天府医院劳务外包服务项目（中标{index}）",
                    scale=130,
                    result="中标",
                    winner="四川祥能人力资本服务有限公司",
                    our_price=our,
                    winning_price=winning,
                )
            # 1 个非价格原因失标
            self._add_project(
                session,
                project_id="h-lost-nonprice",
                name="成都天府医院劳务外包服务项目（资质失标）",
                scale=120,
                result="未中标",
                winner="其他供应商",
                our_price=4_200_000,
                winning_price=4_300_000,
                reasons=["资质业绩不满足要求，技术方案得分低"],
            )
            # 截止时间临近的项目（确定性日期规则）
            self._add_project(
                session,
                project_id="p-deadline-soon",
                name="成都天府医院劳务派遣服务项目（紧急）",
                project_type="劳务派遣",
                budget=3_000_000,
                deadline=NOW + timedelta(hours=24),
                scale=80,
            )
            session.add(
                CompetitorStat(
                    entity_id="e-baohang",
                    regions=["成都"],
                    project_count=9,
                    won_count=5,
                    avg_quote_ratio=0.92,
                    activity="活跃",
                    specialties=["劳务外包"],
                    expiring_projects=1,
                    recent_trend=[1.0, 0.97, 0.93],
                    metadata_={},
                )
            )
            session.commit()

    def _seed_bare(self) -> None:
        """只有目标项目本身：其余维度全部缺失。"""
        self._seed_base()
        with Session(self.engine) as session:
            self._add_project(
                session,
                project_id="p-bare",
                name="某医院劳务外包服务项目（无历史数据）",
                budget=None,
                deadline=None,
                scale=None,
            )
            session.commit()

    def _seed_single_sample(self) -> None:
        """只有 1 条可比记录 —— 统计型结论置信度不足，必须进 unknown。"""
        self._seed_base()
        with Session(self.engine) as session:
            self._add_project(
                session,
                project_id="p-target",
                name="成都天府医院劳务外包服务项目（单样本）",
                budget=BUDGET,
                deadline=NOW + timedelta(days=10),
                scale=150,
            )
            self._add_project(
                session,
                project_id="h-only",
                name="成都天府医院劳务外包服务项目（唯一历史）",
                scale=145,
                result="未中标",
                winner=COMPETITOR,
                our_price=9_000_000,
                winning_price=8_000_000,
                reasons=["报价偏高"],
            )
            session.commit()

    @staticmethod
    def _by_id(items) -> dict:  # noqa: ANN001
        return {item.id: item for item in items}

    # -- 1. 三组证据 + Source Pointer ---------------------------------------

    def test_rich_data_produces_all_three_groups_with_source_pointers(self):
        self._seed_rich()
        result = self._aggregator().aggregate("p-target", "这次该高价还是压价")

        self.assertTrue(result.support, "有历史数据时 support 不应为空")
        self.assertTrue(result.caution, "有历史数据时 caution 不应为空")
        self.assertTrue(result.unknown, "unknown 组永不省略")

        # support / caution 的每条证据都必须能回链到**真实** Source Pointer
        for item in result.support + result.caution:
            self.assertTrue(item.source_pointer_ids, f"{item.id} 缺 Source Pointer")
            for pointer_id in item.source_pointer_ids:
                self.assertFalse(
                    str(pointer_id).startswith("synthetic:"),
                    f"{item.id} 只挂了占位指针，不能当结论",
                )
            self.assertNotIn("demotion_reason", item.metadata)
            self.assertGreaterEqual(item.confidence, result_threshold())

        # unknown 组必须说清「为什么不知道」
        for item in result.unknown:
            reason = item.metadata.get("demotion_reason")
            self.assertTrue(
                reason or item.metadata.get("kind") in {"missing_data", "standing_unknown"},
                f"{item.id} 既不是降级项也没标明缺失类型",
            )

        # 相似历史案例必须真的被引用（相似度 + 依据维）
        self.assertTrue(result.similar_projects)
        similar_items = [
            item
            for item in result.support + result.caution
            if item.metadata.get("kind") == "similar_case"
        ]
        self.assertTrue(similar_items)
        for item in similar_items:
            self.assertGreater(item.metadata["similarity"], 0.35)
            self.assertTrue(item.metadata["matched_features"])

        # 竞对证据必须绑定「竞对中标过的历史项目」作为来源
        competitor_items = [
            item
            for item in result.caution
            if item.metadata.get("kind") == "competitor"
        ]
        self.assertTrue(competitor_items, "竞对中标记录应产出竞对证据")
        self.assertIn("宝航人力资源有限公司", competitor_items[0].statement)
        self.assertEqual(competitor_items[0].metadata["trend"], "下探")
        self.assertEqual(competitor_items[0].metadata["samples"], 3)
        self.assertGreaterEqual(competitor_items[0].confidence, result_threshold())
        self.assertTrue(competitor_items[0].source_pointer_ids)

        # 关键事实与数据完整度
        self.assertEqual(result.context.client_name, "成都天府医院")
        self.assertEqual(result.context.budget, BUDGET)
        self.assertTrue(result.data_completeness["our_history_results"])
        self.assertTrue(result.data_completeness["competitor_stats"])
        # 招标文件尚未解析，评分规则必须如实标为缺口
        self.assertFalse(result.data_completeness["scoring_rule"])

        # 来源可回链：聚合结果给出全局指针清单
        self.assertTrue(result.source_pointer_ids)

    # -- 2. 数据缺失 → unknown，不编造 --------------------------------------

    def test_missing_data_goes_to_unknown_and_never_fabricates(self):
        self._seed_bare()
        result = self._aggregator().aggregate("p-bare", "这次该高价还是压价")

        self.assertEqual(result.support, [], "没有数据时 support 必须为空，不能编造证据")
        self.assertEqual(result.caution, [])
        self.assertTrue(result.unknown)

        ids = set(self._by_id(result.unknown))
        self.assertIn("ev_missing_budget", ids)
        self.assertIn("ev_missing_deadline", ids)
        self.assertIn("ev_unknown_no_our_price", ids)
        self.assertIn("ev_unknown_no_similar", ids)
        self.assertIn("ev_unknown_competitor_participation", ids)
        self.assertIn("ev_unknown_scoring_rule", ids)
        for item in result.unknown:
            self.assertLess(item.confidence, result_threshold())

        # 摘要必须明确写出缺了什么
        self.assertIn("缺失：", result.summary())
        self.assertIn("预算", result.summary())

    # -- 3. 低置信度不进 support / caution ----------------------------------

    def test_low_confidence_evidence_is_demoted_not_asserted(self):
        self._seed_single_sample()
        result = self._aggregator().aggregate("p-target", "这次该高价还是压价")

        threshold = result_threshold()
        for item in result.support + result.caution:
            self.assertGreaterEqual(item.confidence, threshold)

        # 只有 1 条可比报价记录 → 置信度 0.62，必须进 unknown 并写明 low_confidence
        unknown = self._by_id(result.unknown)
        self.assertIn("ev_our_quote_above_winner", unknown)
        self.assertEqual(unknown["ev_our_quote_above_winner"].metadata["demotion_reason"], "low_confidence")
        self.assertEqual(unknown["ev_our_quote_above_winner"].metadata["original_stance"], "caution")
        self.assertNotIn("ev_our_quote_above_winner", self._by_id(result.caution))

        # 单样本的中标价分布同样不能当结论
        self.assertEqual(unknown["ev_price_level_low"].metadata["demotion_reason"], "low_confidence")

    # -- 4. human_decision_required 恒为 True -------------------------------

    def test_human_decision_required_is_always_true(self):
        self._seed_rich()
        service = DecisionSupportService(self._source())

        for question in ("这次该高价还是压价", "这个项目该不该参与", "随便问问"):
            built = service.build("p-target", question)
            self.assertTrue(built.human_decision_required)

        self.assertTrue(service.build_demo("p-target", "该不该参与").human_decision_required)
        self.assertTrue(
            DecisionSupportService().build("p-target", "该不该参与").human_decision_required
        )
        # 项目不存在也不能返回 False
        self.assertTrue(service.build("p-not-exist", "该不该参与").human_decision_required)

    # -- 5. 报价问题必须两侧都有证据 ----------------------------------------

    def test_pricing_question_has_both_sides(self):
        self._seed_rich()
        result = self._aggregator().aggregate("p-target", "这次该高价还是压价")
        self.assertEqual(result.profile.side_a, "支持高价")
        self.assertEqual(result.profile.side_b, "支持压价")

        high_side = [
            item for item in result.support if item.metadata.get("bucket") == "support_a"
        ]
        low_side = [
            item for item in result.caution if item.metadata.get("bucket") == "support_b"
        ]
        self.assertTrue(high_side, "必须给出支持高价侧的证据")
        self.assertTrue(low_side, "必须给出支持压价侧的证据")

        kinds_high = {item.metadata.get("kind") for item in high_side}
        kinds_low = {item.metadata.get("kind") for item in low_side}
        self.assertIn("our_quote_position", kinds_high)
        self.assertIn("our_quote_position", kinds_low)
        self.assertIn("lost_reason", kinds_high)  # 非价格原因失标 → 压价未必有用
        self.assertIn("lost_reason", kinds_low)  # 价格原因失标 → 支持压价

        # AI 不给唯一报价：摘要里必须写明由人决定
        self.assertIn("不给出唯一报价", result.summary())

    # -- 6. 参与问题 --------------------------------------------------------

    def test_participation_question_uses_participation_rules(self):
        self._seed_rich()
        result = self._aggregator().aggregate("p-target", "这个项目该不该参与")
        self.assertEqual(result.profile.side_a, "支持参与")
        kinds = {
            item.metadata.get("kind")
            for item in result.support + result.caution + result.unknown
        }
        self.assertIn("win_rate", kinds)
        self.assertIn("client_history", kinds)
        # 报价类规则不应污染参与问题
        self.assertNotIn("our_quote_position", kinds)

    # -- 7. 确定性日期规则（不让 LLM 算日历） -------------------------------

    def test_deadline_rule_is_deterministic(self):
        self._seed_rich()
        result = self._aggregator().aggregate("p-deadline-soon", "这个项目该不该参与")
        deadline_items = [
            item for item in result.caution if item.id == "ev_deadline_approaching"
        ]
        self.assertTrue(deadline_items)
        self.assertEqual(deadline_items[0].metadata["kind"], "caution_factor")
        self.assertTrue(deadline_items[0].source_pointer_ids)

    # -- 8. 检索层不可用 → 降级并标注 ---------------------------------------

    def test_retrieval_unavailable_degrades_with_explicit_notice(self):
        self._seed_rich()
        service = SimilarityService(_BrokenSearch())
        result = EvidenceAggregator(self._source(), similarity=service).aggregate(
            "p-target", "这次该高价还是压价"
        )

        self.assertTrue(result.similar_projects, "语义不可用时仍应给出确定性相似项目")
        self.assertTrue(any("语义排序暂不可用" in notice for notice in result.notices))
        self.assertTrue(result.degraded)
        for item in result.similar_projects:
            self.assertEqual(item.similarity_kind, "deterministic")

        # 无向量能力时同样明确标注，而不是假装语义结果
        no_vector = EvidenceAggregator(
            self._source(), similarity=SimilarityService(_NoVectorSearch())
        ).aggregate("p-target", "这次该高价还是压价")
        self.assertTrue(any("语义排序暂不可用" in notice for notice in no_vector.notices))
        self.assertIn("pgvector", " ".join(no_vector.notices))

    def test_vector_available_marks_similarity_kind(self):
        self._seed_rich()
        service = SimilarityService(_VectorSearch({"h-lost-1": 0.95}))
        result = EvidenceAggregator(self._source(), similarity=service).aggregate(
            "p-target", "这次该高价还是压价"
        )
        boosted = [p for p in result.similar_projects if p.project_id == "h-lost-1"]
        self.assertTrue(boosted, "语义召回命中的项目应在相似列表里")
        self.assertEqual(boosted[0].similarity_kind, "vector+deterministic")
        self.assertFalse(result.degraded)
        self.assertEqual(result.notices, [])

    # -- 9. 相似度：确定性、可比维、缺失维 ----------------------------------

    def test_similarity_is_deterministic_and_reports_missing_features(self):
        target = {
            "id": "t",
            "project_type": "劳务外包",
            "region": "成都",
            "client_name": "成都天府医院",
            "metadata": {"person_count": 150, "服务期限": "24个月"},
        }
        candidates = [
            {
                "id": "c1",
                "project_type": "劳务外包",
                "region": "成都市",
                "client_name": "成都天府医院",
                "metadata": {"person_count": 140, "服务期限": "2年"},
                "source_pointer": {
                    "id": "sp-c1",
                    "source_type": "sheet",
                    "source_name": "投标项目计划9.13.xlsx",
                    "trust_level": "internal",
                },
            },
            {
                "id": "c2",
                "project_type": "保安服务",
                "region": "德阳",
                "client_name": "德阳某单位",
                "metadata": {},
                "source_pointer": {
                    "id": "sp-c2",
                    "source_type": "sheet",
                    "source_name": "投标项目计划9.13.xlsx",
                    "trust_level": "internal",
                },
            },
        ]
        service = SimilarityService()
        first = service.rank(target, candidates, limit=5)
        second = service.rank(target, candidates, limit=5)

        self.assertEqual(
            [(p.project_id, p.similarity) for p in first.items],
            [(p.project_id, p.similarity) for p in second.items],
            "相似度必须是确定性的",
        )
        self.assertEqual([p.project_id for p in first.items], ["c1"])
        top = first.items[0]
        self.assertIn("project_type", top.matched_features)
        self.assertIn("duration", top.matched_features)
        self.assertIn("scale", top.matched_features)
        self.assertEqual(top.source_pointer.id, "sp-c1")
        self.assertTrue(any("语义排序暂不可用" in notice for notice in first.notices))

        # 全维缺失的候选不得靠剩余维硬凑：类型/地区都不匹配时直接不返回
        self.assertNotIn("c2", [p.project_id for p in first.items])

    def test_similarity_marks_synthesized_pointer(self):
        target = {"id": "t", "project_type": "劳务外包", "region": "成都"}
        candidate = {
            "id": "c9",
            "project_type": "劳务外包",
            "region": "成都",
            "name": "无来源项目",
        }
        outcome = SimilarityService().rank(target, [candidate], limit=5)
        self.assertTrue(outcome.items)
        pointer = outcome.items[0].source_pointer
        self.assertTrue(str(pointer.id).startswith("synthetic:"))
        self.assertTrue(pointer.metadata.get("synthesized"))

    # -- 10. 数据源不可用 → 显式异常 ----------------------------------------

    def test_source_unavailable_raises_instead_of_empty_evidence(self):
        self._seed_rich()
        Base.metadata.drop_all(self.engine)
        with self.assertRaises(EvidenceSourceUnavailable):
            self._aggregator().aggregate("p-target", "这次该高价还是压价")

    # -- 11. demo 降级必须标注示例数据 --------------------------------------

    def test_demo_fallback_is_marked_as_sample_data(self):
        self._seed_rich()
        service = DecisionSupportService()  # 未注入真实数据源
        self.assertEqual(service.mode, "demo")

        built = service.build("p-target", "这次该高价还是压价")
        self.assertIn("示例数据", built.summary)
        self.assertTrue(built.support and built.caution and built.unknown)
        for item in built.support + built.caution + built.unknown:
            self.assertTrue(item.metadata.get("demo"))
            self.assertTrue(item.metadata.get("demo_notice"))

        # 真实路径则不得出现 demo 标记
        real = DecisionSupportService(self._source()).build("p-target", "这次该高价还是压价")
        self.assertEqual(DecisionSupportService(self._source()).mode, "evidence")
        self.assertNotIn("示例数据", real.summary)
        for item in real.support + real.caution + real.unknown:
            self.assertFalse(item.metadata.get("demo"))

    # -- 12. 项目不存在 -----------------------------------------------------

    def test_unknown_project_returns_only_unknown_group(self):
        self._seed_rich()
        result = self._aggregator().aggregate("p-missing", "这次该高价还是压价")
        self.assertEqual(result.support, [])
        self.assertEqual(result.caution, [])
        self.assertEqual([item.id for item in result.unknown], ["ev_unknown_project_missing"])
        self.assertIsNone(result.context)

    # -- 13. 问题分类（确定性） ---------------------------------------------

    def test_question_classification_is_deterministic(self):
        self.assertEqual(classify_question("这次该高价还是压价").kind.value, "pricing")
        self.assertEqual(classify_question("这个项目该不该参与").kind.value, "participation")
        self.assertEqual(classify_question("看看这个项目").kind.value, "generic")
        # 报价优先于参与
        self.assertEqual(classify_question("参与的话报价怎么定").kind.value, "pricing")


def result_threshold() -> float:
    from app.core.config import settings

    return settings.review_confidence_threshold


if __name__ == "__main__":
    unittest.main()
