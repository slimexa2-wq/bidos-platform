"""SqlSearchService 在无 PostgreSQL 环境下的真实行为测试。

本机没有 PostgreSQL / pg_trgm / pgvector，因此用 **SQLite 内存库 + 纯 Python 兜底实现**
验证检索链路。真实 PG 专属 SQL（`similarity()` / `<=>`）被 dialect 分支隔离，
这里额外断言它们在 sqlite 下绝不会被执行。
"""

import sys
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.repositories.models import (  # noqa: E402
    Base,
    Entity,
    EntityAlias,
    Organization,
    Project,
    ProjectResult,
    SourceObject,
)
from app.repositories.models import SourcePointer as SourcePointerRow  # noqa: E402
from app.services.search_degrade import (  # noqa: E402
    SearchDegradeError,
    detect_capabilities,
    search_with_fallback,
)
from app.services.search_sql import (  # noqa: E402
    SearchBackendUnavailable,
    SearchFilters,
    SqlSearchService,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):  # noqa: ANN001, ARG001
    """生产 ORM 用 JSONB；SQLite 测试里降级为 JSON，保证 DDL 可建。"""
    return "JSON"


class SqlSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self._seed()

    def tearDown(self) -> None:
        self.engine.dispose()

    # -- 测试数据 -----------------------------------------------------------

    def _seed(self) -> None:
        with Session(self.engine) as session:
            session.add(Organization(id="org1", name="四川祥能人力资本服务有限公司"))

            session.add_all(
                [
                    Entity(
                        id="e-baohang",
                        organization_id="org1",
                        entity_type="competitor",
                        canonical_name="宝航人力资源有限公司",
                        metadata_={},
                    ),
                    Entity(
                        id="e-xiangneng",
                        organization_id="org1",
                        entity_type="company",
                        canonical_name="四川祥能人力资本服务有限公司",
                        metadata_={},
                    ),
                ]
            )
            session.add_all(
                [
                    # 「baohang」与规范名没有任何公共 2-gram，必须靠别名扩展才能命中
                    EntityAlias(id="al1", entity_id="e-baohang", alias="baohang"),
                    EntityAlias(id="al2", entity_id="e-baohang", alias="宝航"),
                    EntityAlias(id="al3", entity_id="e-xiangneng", alias="祥能"),
                ]
            )

            session.add_all(
                [
                    Project(
                        id="p1",
                        organization_id="org1",
                        name="成都天府医院劳务外包服务项目",
                        client_name="宝航人力资源有限公司",
                        region="成都",
                        project_type="劳务外包",
                        workflow_stage="结果复盘",
                        status="已中标",
                        deadline=datetime(2025, 6, 1),
                        project_end_at=datetime(2027, 6, 1),
                        source_of_truth="投标项目计划9.13.xlsx",
                        metadata_={},
                    ),
                    Project(
                        id="p2",
                        organization_id="org1",
                        name="绵阳科创园区物业服务项目",
                        client_name="绵阳科创园区管委会",
                        region="绵阳",
                        project_type="物业服务",
                        workflow_stage="结果复盘",
                        status="未中标",
                        deadline=datetime(2024, 3, 1),
                        source_of_truth="投标项目计划9.13.xlsx",
                        metadata_={},
                    ),
                    Project(
                        id="p3",
                        organization_id="org1",
                        name="成都轨道交通劳务派遣服务项目",
                        client_name="成都轨道交通集团",
                        region="成都",
                        project_type="劳务派遣",
                        workflow_stage="标书制作",
                        status="进行中",
                        deadline=datetime(2025, 9, 1),
                        source_of_truth="投标项目计划9.13.xlsx",
                        metadata_={},
                    ),
                    # 故意不给 Source Pointer，验证「没有指针也要有显式占位指针」
                    Project(
                        id="p4",
                        organization_id="org1",
                        name="德阳保安服务项目",
                        client_name="德阳某单位",
                        region="德阳",
                        project_type="保安服务",
                        workflow_stage="结果复盘",
                        status="未中标",
                        deadline=datetime(2023, 5, 1),
                        metadata_={},
                    ),
                ]
            )

            session.add_all(
                [
                    ProjectResult(
                        id="r1",
                        project_id="p1",
                        result="中标",
                        winner="宝航人力资源有限公司",
                        reasons=[],
                        lessons=[],
                        metadata_={},
                    ),
                    ProjectResult(
                        id="r2",
                        project_id="p2",
                        result="未中标",
                        reasons=[],
                        lessons=[],
                        metadata_={},
                    ),
                ]
            )

            session.add_all(
                [
                    SourcePointerRow(
                        id="sp-p1",
                        project_id="p1",
                        source_name="投标项目计划9.13.xlsx",
                        source_type="sheet",
                        trust_level="internal",
                        sheet_name="项目台账",
                        row_id="12",
                        original_text="成都天府医院劳务外包服务项目 / 宝航人力资源有限公司",
                        metadata_={},
                    ),
                    SourcePointerRow(
                        id="sp-p2",
                        project_id="p2",
                        source_name="投标项目计划9.13.xlsx",
                        source_type="sheet",
                        trust_level="internal",
                        sheet_name="项目台账",
                        row_id="13",
                        metadata_={},
                    ),
                    SourcePointerRow(
                        id="sp-p3",
                        project_id="p3",
                        source_name="盲投项目信息收集汇总表2026(3).xlsx",
                        source_type="sheet",
                        trust_level="internal",
                        sheet_name="盲投汇总",
                        row_id="7",
                        metadata_={},
                    ),
                ]
            )

            session.add(
                SourceObject(
                    id="so1",
                    organization_id="org1",
                    source_id="mock-official",
                    external_id="ext-1",
                    source_type="official",
                    title="成都天府医院劳务外包服务项目中标公告",
                    source_url="https://example.com/award/1",
                    published_at=datetime(2025, 6, 15),
                    normalized_text=(
                        "成都天府医院劳务外包服务项目 中标结果公告 "
                        "中标单位 宝航人力资源有限公司 中标金额 860 万元"
                    ),
                    raw_metadata={},
                )
            )
            session.add(
                SourcePointerRow(
                    id="sp-so1",
                    source_object_id="so1",
                    source_name="成都公共资源交易网",
                    source_type="official",
                    trust_level="official",
                    source_url="https://example.com/award/1",
                    metadata_={},
                )
            )
            session.commit()

    def _service(self, **kwargs) -> SqlSearchService:
        return SqlSearchService(self.engine, **kwargs)

    # -- 1. 中文关键词命中项目名 -------------------------------------------

    def test_chinese_keyword_hits_project_name(self):
        hits = self._service().search("劳务外包", limit=20)
        ids = [hit.id for hit in hits]
        self.assertIn("p1", ids)
        project_hits = [hit for hit in hits if hit.type == "project"]
        self.assertTrue(project_hits)
        self.assertEqual(project_hits[0].id, "p1")

    # -- 2. 实体别名扩展（必须靠 entity_aliases 才能命中） ------------------

    def test_entity_alias_expansion_is_required_for_baohang(self):
        service = self._service()
        expanded = service.expand_entity_names("baohang")
        self.assertIn("宝航人力资源有限公司", expanded)

        hits = service.search("baohang", limit=20)
        by_id = {hit.id: hit for hit in hits}
        self.assertIn("p1", by_id)
        # 项目表里的甲方名与查询词没有公共字符，只有别名扩展能连上
        self.assertNotIn("baohang", "宝航人力资源有限公司")
        self.assertIn("entity_alias", by_id["p1"].metadata["matched_via"])

    # -- 3. 结构化过滤（region + project_type + 时间范围） -----------------

    def test_structured_filters_region_type_and_date_range(self):
        hits = self._service().search(
            "",
            limit=50,
            filters=SearchFilters(
                region="成都",
                project_type="劳务外包",
                date_from=datetime(2025, 1, 1),
                date_to=datetime(2025, 12, 31),
            ),
        )
        self.assertEqual({hit.id for hit in hits}, {"p1"})

    def test_structured_filter_result_field(self):
        hits = self._service().search("", limit=50, filters=SearchFilters(result="中标"))
        self.assertEqual({hit.id for hit in hits}, {"p1"})

    # -- 4. 每条结果都带 Source Pointer ------------------------------------

    def test_every_hit_carries_source_pointer(self):
        hits = self._service().search("", limit=50)
        self.assertTrue(hits)
        for hit in hits:
            self.assertIsNotNone(hit.source)
            self.assertTrue(hit.source.source_name)
        # 没有 source_pointers 记录的项目也要有显式占位指针，而不是空
        p4 = next(hit for hit in hits if hit.id == "p4")
        self.assertTrue(p4.source.source_name)
        self.assertTrue(p4.source.metadata.get("synthesized"))

        # 命中公告时，指针来自 source_pointers 关联
        ann = next(hit for hit in self._service().search("劳务外包", limit=20) if hit.type == "announcement")
        self.assertEqual(ann.source.source_name, "成都公共资源交易网")
        self.assertEqual(ann.source.source_url, "https://example.com/award/1")

    # -- 5. 降级路径：无向量 / 无 reranker ---------------------------------

    def test_degrade_without_vector_and_reranker(self):
        service = self._service()
        capability = service.capability
        self.assertTrue(capability.structured)
        self.assertFalse(capability.vector)
        self.assertFalse(capability.reranker)
        self.assertFalse(capability.trigram)  # sqlite 没有 pg_trgm
        self.assertIn("sqlite", capability.reasons["trigram"])

        outcome = search_with_fallback(service, "劳务外包", limit=20)
        self.assertTrue(outcome.hits)
        self.assertTrue(outcome.degraded)
        self.assertFalse(outcome.capability.vector)
        self.assertTrue(any("语义排序暂不可用" in n for n in outcome.notices))
        for hit in outcome.hits:
            self.assertFalse(hit.metadata["semantic_ranking"])
            self.assertEqual(hit.metadata["ranking_mode"], "deterministic")
            self.assertTrue(hit.metadata["degrade_notices"])

    # -- 6. 向量运行时失败 → 降级关键词，而不是整体失败 --------------------

    def test_vector_runtime_failure_degrades_to_keyword(self):
        def broken_embedder(_query: str):
            raise RuntimeError("BGE-M3 服务不可达")

        forced = replace(
            detect_capabilities(self.engine, embedder=broken_embedder),
            vector=True,
            reasons={},
        )
        service = SqlSearchService(self.engine, embedder=broken_embedder, capability=forced)

        hits = service.search("劳务外包", limit=20)
        self.assertTrue(hits)  # 关键词结果仍然返回
        self.assertIn("vector", service.runtime_degradations)
        self.assertFalse(service.effective_capability().vector)

        outcome = search_with_fallback(service, "劳务外包", limit=20)
        self.assertTrue(any("向量检索运行时失败" in n for n in outcome.notices))
        self.assertTrue(all(hit.metadata["semantic_ranking"] is False for hit in outcome.hits))

    # -- 7. DB 不可用 → 抛显式异常，绝不返回空列表 -------------------------

    def test_db_unavailable_raises_instead_of_empty(self):
        service = self._service()
        Base.metadata.drop_all(self.engine)

        with self.assertRaises(SearchBackendUnavailable):
            service.search("劳务外包", limit=20)
        with self.assertRaises(SearchDegradeError):
            search_with_fallback(service, "劳务外包", limit=20)

    # -- 8. 一句话自然语言检索（含「去年」确定性日期规则） -----------------

    def test_natural_language_query_returns_baohang_chengdu_outsourcing(self):
        hits = self._service().search("宝航去年在成都中了哪些劳务外包项目", limit=10)
        self.assertTrue(hits)
        self.assertEqual(hits[0].id, "p1")
        self.assertIn("entity_alias", hits[0].metadata["matched_via"])

    # -- 9. 无 PG 环境不得执行 pg_trgm / pgvector 专用 SQL -----------------

    def test_pg_only_sql_is_not_executed_on_sqlite(self):
        service = self._service()
        self.assertEqual(service.dialect, "sqlite")
        capability = service.capability.to_dict()
        self.assertFalse(capability["trigram"])
        self.assertFalse(capability["vector"])
        self.assertTrue(capability["python_fallback"])
        # 走 Python 兜底也能给出合理相似度
        from app.services.search_sql import trigram_similarity

        self.assertGreater(trigram_similarity("劳务外包", "成都劳务外包项目"), 0.3)
        self.assertEqual(trigram_similarity("劳务外包", "绵阳物业"), 0.0)


if __name__ == "__main__":
    unittest.main()
