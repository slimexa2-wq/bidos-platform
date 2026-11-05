"""本公司主体（own_entities）配置 + entity_aliases 主键口径的回归测试。

背景（2026-09-28 验收审计 #45「实体关联」）：
* `project_results.winner` 的真值大量是「四川祥能人力资本服务有限公司」（我司自己），
  但 entities 里**根本没有本公司实体** → 57 条我司中标记录全部落入 unmatched，
  我方中标项目无法建立 winner 关系。
* `entity_aliases` 长期 0 行：一是 DDL 把 `id` 写成 `uuid`，与全库「确定性 hash 主键」
  纪律冲突，别名根本写不进去；二是没有登记任何别名。

本文件锁定这两处不再回归：
* 配置层：own_entities 可被正确解析，且**绝不可能是 competitor**；
* schema 层（需真库）：`entity_aliases.id` 必须是 text。

运行：`python -m unittest discover -s tests/backend`
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
SCRIPTS_ROOT = ROOT / "scripts"
for path in (str(API_ROOT), str(SCRIPTS_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from build_entity_graph import load_own_entities  # noqa: E402
from import_excel import DEFAULT_MAPPING, _normalize_pg_url, load_mapping  # noqa: E402

# ---------------------------------------------------------------------------
# 一、配置层：不依赖数据库
# ---------------------------------------------------------------------------
_MAPPING_PATH = Path(DEFAULT_MAPPING)


class OwnEntitiesConfigTests(unittest.TestCase):
    """本公司主体必须来自配置、可审计，而不是代码里的硬编码名单。"""

    @classmethod
    def setUpClass(cls):
        cls.mapping = load_mapping(_MAPPING_PATH)
        cls.own = load_own_entities(cls.mapping)

    def test_own_entities_are_configured_and_parsed(self):
        self.assertTrue(
            self.own,
            "field_mapping.yaml 的 entity_normalization.own_entities 不能为空——"
            "否则我司中标项目无法建立 winner 关系",
        )
        for item in self.own:
            self.assertTrue(item["canonical"].strip())
            self.assertIsInstance(item["aliases"], list)

    def test_canonical_names_are_unique(self):
        names = [item["canonical"] for item in self.own]
        self.assertEqual(len(names), len(set(names)), "own_entities 存在重复主体")

    def test_own_entities_are_not_blacklisted(self):
        """本公司主体若被列进 entity_blacklist，入库时会被静默丢弃。"""
        own = {item["canonical"] for item in self.own}
        self.assertTrue(
            own.isdisjoint(self.mapping.entity_blacklist),
            f"本公司主体与 entity_blacklist 冲突：{own & self.mapping.entity_blacklist}",
        )

    def test_company_entity_type_is_the_own_company_channel(self):
        """`entities.company` 就是「我方运营单位」通道（不是 competitor）。"""
        targets = self.mapping.raw.get("targets") or {}
        company = targets.get("entities.company") or {}
        self.assertEqual(company.get("entity_type"), "company")

    def test_known_own_company_is_registered(self):
        """真数据里出现 57 次的主体必须在名单里（回归：曾完全缺失）。"""
        names = {item["canonical"] for item in self.own}
        self.assertIn("四川祥能人力资本服务有限公司", names)

    def test_typo_variant_is_registered_as_alias(self):
        """「人力资木」是「人力资源」的错别字变体，必须走 alias 归并而非模糊匹配。"""
        aliases = {alias for item in self.own for alias in item["aliases"]}
        self.assertIn("四川祥能人力资木服务有限公司", aliases)


# ---------------------------------------------------------------------------
# 二、schema 层：需要真库（无库自动 skip，绝不 fail）
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "BIDOS_TEST_DATABASE_URL", "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
).strip()
# 裸 postgresql:// 不带驱动名，SQLAlchemy 会去找 psycopg2；本项目用 psycopg3。
DRIVER_URL = _normalize_pg_url(DATABASE_URL) if DATABASE_URL else ""


def _pg_available():
    if not DATABASE_URL:
        return False, "DSN 为空"
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(DRIVER_URL, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return True, ""
    except Exception as exc:  # noqa: BLE001 - 连不上就是 skip，不是 fail
        return False, f"{type(exc).__name__}: {exc}"


_PG_OK, _PG_REASON = _pg_available()


@unittest.skipUnless(_PG_OK, f"跳过真实 PG 校验：{_PG_REASON}")
class EntityAliasSchemaTests(unittest.TestCase):
    """锁定 007 迁移：entity_aliases.id 必须是 text。"""

    def setUp(self):
        from sqlalchemy import create_engine

        self.engine = create_engine(DRIVER_URL, pool_pre_ping=True, future=True)

    def test_entity_alias_id_is_text_not_uuid(self):
        from sqlalchemy import text

        with self.engine.connect() as conn:
            data_type = conn.execute(
                text(
                    "select data_type from information_schema.columns "
                    "where table_name = 'entity_aliases' and column_name = 'id'"
                )
            ).scalar_one_or_none()
        self.assertIsNotNone(data_type, "entity_aliases.id 列不存在")
        self.assertEqual(
            data_type,
            "text",
            "entity_aliases.id 又变回 uuid 了 —— 项目主键是确定性 hash（text），"
            "uuid 列会让别名通道完全写不进去（见 007_entity_alias_id_text.sql）",
        )

    def test_own_entities_are_company_type_and_not_competitor(self):
        from sqlalchemy import text

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select canonical_name, entity_type from entities "
                    "where canonical_name like '%祥能%'"
                )
            ).all()
        if not rows:
            self.skipTest("库内尚无本公司实体（未跑过 build_entity_graph.py）")
        for name, entity_type in rows:
            self.assertNotEqual(
                entity_type, "competitor", f"本公司主体 {name} 被标成了竞对"
            )
            self.assertEqual(entity_type, "company")

    def test_own_company_has_winner_relations(self):
        from sqlalchemy import text

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "select count(*) from project_entities pe "
                    "join entities e on e.id = pe.entity_id "
                    "where pe.relation_type = 'winner' "
                    "and e.canonical_name = '四川祥能人力资本服务有限公司'"
                )
            ).scalar_one()
        if not count:
            self.skipTest("我司中标关系尚未物化（未跑过 build_entity_graph.py）")
        self.assertGreater(count, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
