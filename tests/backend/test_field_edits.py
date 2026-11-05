"""全字段编辑 / 新信息队列 / 报名转正 / 正源行判定（2026-09-30 王明李契约）。

锁死三方数据契约（回写引擎按这些标记消费，前端只打标）：
  * projects.metadata.system_edits：append-only 编辑历史，excel.sheet/row 取
    盲投月表指针，同 field 旧 pending 置 superseded，无指针必须 400 拒绝；
  * projects.metadata.candidate_promotion / candidate_row：报名转正标记；
  * dismissed：忽略/不报名标记（reason 可空），队列 = 公告候选且无 dismissed/promotion；
  * 我方结果正源行：source_family IN ('ledger_2026','announcement')，
    exclude_own_result=true 一律不算，无 family 旧行回退 source_label 含 '#2026年!'。

实现方式：sqlite 内存库 + 同一套 ORM（JSONB -> JSON DDL shim），
不依赖真实 PostgreSQL；API 层用 TestClient + 替换 main.repository。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.repositories.models import (  # noqa: E402
    Base,
    Organization,
    Project,
    ProjectResult,
    SourcePointer,
)
from app.repositories.postgres_repository import (  # noqa: E402
    BIDOS_EDIT_FIELDS,
    FieldEditError,
    PostgresRepository,
    ProjectNotFoundError,
    ProjectStateError,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "JSON"


_ORIG_TEXT = (
    "序号=131 | 项目区域=成都 | 项目类型=劳务外包 | 报名截止时间=2026-09-08"
    " | 甲方名称=测试甲方集团 | 项目名称=测试甲方食堂劳务外包项目"
    " | 报名参与情况=报名参与 | 信息筛选人=张三"
)


def _blind_project(pid: str, org: str, sheet: str = "9月", row: str = "131", **meta_extra):
    return Project(
        id=pid,
        organization_id=org,
        name="测试甲方食堂劳务外包项目",
        client_name="测试甲方集团",
        region="成都",
        project_type="劳务外包",
        workflow_stage="盲投信息",
        status="在筛",
        budget=560000,
        deadline=datetime(2026, 9, 8, 18, 0),
        metadata_={
            "source_pointer": {
                "id": f"bidos_ptr_{pid}",
                "sheet_name": sheet,
                "row_id": row,
                "source_name": "盲投项目信息收集汇总表2026",
                "file_name": "盲投项目信息 收集汇总表2026(3).xlsx",
                "original_text": _ORIG_TEXT,
            },
            **meta_extra,
        },
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _candidate(pid: str, org: str, *, with_landing: bool = True, **meta_extra):
    meta: dict = {}
    if with_landing:
        meta["landing"] = {
            "source_id": "sichuan_gov_procurement",
            "source_url": f"https://www.ccgp-sichuan.gov.cn/article?id={pid}",
            "published_at": "2026-09-09T17:53:07+08:00",
        }
    meta.update(meta_extra)
    return Project(
        id=pid,
        organization_id=org,
        name=f"公告候选项目{pid[-4:]}",
        client_name=None,
        region=None,
        project_type=None,
        workflow_stage="公告候选",
        status=None,
        budget=516000,
        metadata_=meta,
        created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


class FieldEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(engine)
        cls.Session = sessionmaker(bind=engine, expire_on_commit=False)
        cls.repo = PostgresRepository(cls.Session)
        cls._seed()

    @classmethod
    def _seed(cls):
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        with cls.Session() as s:
            s.add(Organization(id="org1", name="四川祥能"))
            # 盲投月表行（有指针）
            s.add(_blind_project("p_edit", "org1"))
            # supersede 用例专用行（避免与其它用例串历史）
            s.add(_blind_project("p_supersede", "org1", row="150"))
            # 第二个盲投行（报名参与、正源未中标）
            lost = _blind_project("p_lost", "org1", row="132")
            lost.metadata_ = dict(lost.metadata_)
            s.add(lost)
            # 无月表指针的普通项目
            s.add(
                Project(
                    id="p_noptr",
                    organization_id="org1",
                    name="无指针项目",
                    workflow_stage="报名管理",
                    metadata_={},
                    created_at=now,
                    updated_at=now,
                )
            )
            # 快照缺 sheet、但现役 source_pointers 有月表行的项目（活指针兜底）
            s.add(
                Project(
                    id="p_liveptr",
                    organization_id="org1",
                    name="活指针项目",
                    workflow_stage="盲投信息",
                    metadata_={"source_pointer": {"id": "snap-x", "row_id": "1"}},
                    created_at=now,
                    updated_at=now,
                )
            )
            s.add(
                SourcePointer(
                    id="sp-live-8yue",
                    project_id="p_liveptr",
                    source_name="盲投项目信息收集汇总表2026",
                    source_type="sheet",
                    trust_level="internal",
                    sheet_name="8月",
                    row_id="77",
                    created_at=now,
                )
            )
            # 公告候选：AI 抓取（有 landing）/ 人工 / 已忽略 / 已报名
            s.add(_candidate("c_ai", "org1", with_landing=True))
            s.add(_candidate("c_manual", "org1", with_landing=False))
            s.add(_candidate("c_api", "org1", with_landing=True))
            s.add(_candidate("c_api2", "org1", with_landing=False))
            s.add(_candidate("c_dismissed", "org1", with_landing=True, dismissed={"reason": "区域不符", "editor": "王明李", "at": "2026-09-29T10:00:00+00:00"}))
            s.add(_candidate("c_promoted", "org1", with_landing=True, candidate_promotion={"to_sheet": "9月", "editor": "王明李", "at": "2026-09-29T10:00:00+00:00", "status": "pending"}))
            # API 层测试专用盲投行（与其他用例互不串数据）
            s.add(_blind_project("p_api_edit", "org1", row="200"))
            s.add(
                Project(
                    id="c_stage_off",
                    organization_id="org1",
                    name="已转正候选",
                    workflow_stage="盲投信息",
                    metadata_={},
                    created_at=now,
                    updated_at=now,
                )
            )
            # 正源行判定 fixtures
            def _result(rid, pid, result, winner=None, family=None, label=None, exclude=None):
                meta: dict = {}
                if family is not None:
                    meta["source_family"] = family
                if label is not None:
                    meta["source_label"] = label
                if exclude is not None:
                    meta["exclude_own_result"] = exclude
                s.add(
                    ProjectResult(
                        id=rid,
                        project_id=pid,
                        result=result,
                        winner=winner,
                        metadata_=meta,
                    )
                )

            _result("r1", "p_edit", "中标", "四川祥能人力资本服务有限公司", family="ledger_2026")
            _result("r2", "p_lost", "未中标", "成都某某人力资源公司", family="ledger_2026")
            # 竞对统计行（打 exclude 标）→ 一律不算
            _result("r3", "p_comp", "中标", "宝航人力", family="competitor_stats", exclude=True)
            # 2025 台账 / 盲投中标表 / not_participated → 不算
            _result("r4", "p_2025", "中标", "某公司", family="ledger_2025")
            _result("r5", "p_blindwin", "中标", "某公司", family="blind_win_sheet")
            _result("r6", "p_notpart", "未中标", None, family="not_participated")
            # 无 family 旧行回退：source_label 含 '#2026年!' 才认
            _result("r7", "p_legacy_ok", "未中标", "某公司", label="台账汇总.xlsx#2026年!R5")
            _result("r8", "p_legacy_no", "中标", "某公司", label="台账汇总.xlsx#统计表!R12")
            # 字符串形式 exclude 标（历史导入形态）→ 不算
            _result("r9", "p_strflag", "中标", "某公司", family="ledger_2026", exclude="true")
            s.commit()

    # -- 1) 编辑 append：契约形状 ------------------------------------------
    def test_apply_field_edits_appends_contract_shape(self):
        out = self.repo.apply_field_edits(
            "p_edit",
            [
                {"field": "甲方名称", "value": "测试甲方集团改"},
                {"field": "BidOS跟进人", "value": "王明李"},
            ],
        )
        self.assertEqual(out["applied"], 2)
        self.assertEqual(out["excel"], {"sheet": "9月", "row": "131"})
        for edit in out["edits"]:
            self.assertTrue(edit["id"].startswith("edt_"))
            self.assertEqual(len(edit["id"]), len("edt_") + 8, "id 必须是 edt_<uuid8>")
            self.assertEqual(edit["editor"], "王明李")
            self.assertEqual(edit["status"] if "status" in edit else edit["excel"]["status"], "pending")
            excel = edit["excel"]
            self.assertEqual(excel["sheet"], "9月")
            self.assertEqual(excel["row"], "131")
            self.assertEqual(excel["column"], edit["field"])
            self.assertIsNone(excel["written_at"])
            self.assertIn("at", edit)
        # 落库：append 进 projects.metadata.system_edits
        with self.Session() as s:
            meta = s.get(Project, "p_edit").metadata_
        history = meta["system_edits"]
        self.assertEqual(len(history), 2)
        self.assertEqual({e["field"] for e in history}, {"甲方名称", "BidOS跟进人"})
        self.assertTrue(all(e["excel"]["status"] == "pending" for e in history))

    def test_bidos_edit_fields_are_the_contracted_four(self):
        self.assertEqual(
            set(BIDOS_EDIT_FIELDS),
            {"BidOS参与状态", "BidOS未参与原因", "BidOS跟进人", "BidOS备注"},
        )

    # -- 2) 同 field 旧 pending → superseded --------------------------------
    def test_same_field_pending_becomes_superseded(self):
        self.repo.apply_field_edits("p_supersede", [{"field": "BidOS备注", "value": "第一版"}])
        out = self.repo.apply_field_edits(
            "p_supersede",
            [
                {"field": "BidOS备注", "value": "第二版"},
                {"field": "BidOS未参与原因", "value": ""},
            ],
        )
        with self.Session() as s:
            history = s.get(Project, "p_supersede").metadata_["system_edits"]
        remarks = [e for e in history if e["field"] == "BidOS备注"]
        self.assertEqual(len(remarks), 2)
        # 契约：status 挂在 excel.status（不在顶层）
        self.assertEqual(remarks[0]["excel"]["status"], "superseded", "同 field 旧 pending 必须被 supersede")
        self.assertEqual(remarks[1]["excel"]["status"], "pending")
        self.assertEqual(remarks[1]["value"], "第二版")
        # 同 field 同时只允许一条 pending（回写引擎同列只落一次）
        pending_remarks = [e for e in remarks if e["excel"]["status"] == "pending"]
        self.assertEqual(len(pending_remarks), 1)
        self.assertEqual(out["applied"], 2)
        # 清空（value=""）也是合法编辑
        clears = [e for e in history if e["field"] == "BidOS未参与原因"]
        self.assertEqual(clears[-1]["value"], "")

    # -- 3) 无指针 → 拒绝（API 层映射 400），缺项目 → 404 --------------------
    def test_no_pointer_raises_and_missing_project_raises(self):
        with self.assertRaises(FieldEditError):
            self.repo.apply_field_edits("p_noptr", [{"field": "甲方名称", "value": "x"}])
        with self.assertRaises(FieldEditError):
            self.repo.apply_field_edits("p_edit", [])  # 空 edits
        with self.assertRaises(FieldEditError):
            self.repo.apply_field_edits("p_edit", [{"value": "缺 field"}])
        with self.assertRaises(ProjectNotFoundError):
            self.repo.apply_field_edits("p_nope", [{"field": "甲方名称", "value": "x"}])
        # 活指针兜底：快照缺 sheet 但 source_pointers 有 N月 行 → 可编辑
        out = self.repo.apply_field_edits("p_liveptr", [{"field": "BidOS备注", "value": "活指针"}])
        self.assertEqual(out["excel"], {"sheet": "8月", "row": "77"})

    # -- 4) _opportunity 渲染：pending/applied 覆盖同名列，不吐全历史 --------
    def test_opportunity_merges_pending_and_applied_edits_only(self):
        # 自带夹具（不依赖其它用例的执行顺序）：甲方名称走 pending 覆盖，
        # BidOS备注 走 applied 覆盖 + 一条 superseded 脏数据不得生效。
        self.repo.apply_field_edits(
            "p_edit",
            [
                {"field": "甲方名称", "value": "渲染覆盖甲方"},
                {"field": "BidOS参与状态", "value": "报名参与"},
                {"field": "BidOS备注", "value": "会话前的备注"},
            ],
        )
        # 把 BidOS备注 这条改成 applied（模拟回写引擎已落盘），再补一条更早的
        # superseded 脏记录；渲染必须取 pending/applied 的值、忽略 superseded。
        with self.Session() as s:
            project = s.get(Project, "p_edit")
            history = project.metadata_["system_edits"]
            for e in history:
                if e["field"] == "BidOS备注":
                    e["excel"]["status"] = "applied"
                    e["excel"]["written_at"] = "2026-09-30T12:00:00+00:00"
            history.append(
                {
                    "id": "edt_dead000",
                    "field": "BidOS备注",
                    "value": "更早被作废的值",
                    "editor": "王明李",
                    "at": "2026-09-30T00:00:00+00:00",
                    "excel": {"sheet": "9月", "row": "131", "column": "BidOS备注",
                              "status": "superseded", "written_at": None},
                }
            )
            project.metadata_ = dict(project.metadata_)
            s.commit()
        items = {o["id"]: o for o in self.repo.opportunities()}
        sf = items["p_edit"]["sourceFields"]
        self.assertEqual(sf["甲方名称"], "渲染覆盖甲方")
        self.assertEqual(sf["BidOS参与状态"], "报名参与")
        self.assertEqual(sf["BidOS备注"], "会话前的备注", "applied 编辑必须渲染；superseded 值不得覆盖")
        self.assertNotIn("system_edits", items["p_edit"], "只 merge 当前值，绝不外吐全历史")
        # 无编辑的行不受影响
        self.assertNotIn("BidOS参与状态", items["p_lost"]["sourceFields"])

    # -- 5) 新信息队列：公告候选且无 dismissed/promotion ----------------------
    def test_candidates_queue_scope(self):
        rows = self.repo.opportunities("candidates")
        ids = {r["id"] for r in rows}
        self.assertIn("c_ai", ids)
        self.assertIn("c_manual", ids)
        self.assertNotIn("c_dismissed", ids, "已忽略的候选必须退出队列")
        self.assertNotIn("c_promoted", ids, "已报名的候选必须退出队列")
        self.assertNotIn("c_stage_off", ids, "非公告候选阶段不进队列")
        self.assertNotIn("p_edit", ids)
        # 行形状同盲投行 + metadata
        ai = next(r for r in rows if r["id"] == "c_ai")
        self.assertIn("sourceFields", ai)
        self.assertIn("sources", ai)
        self.assertIn("metadata", ai)
        self.assertTrue(ai["metadata"]["landing"], "AI 抓取候选带 landing（前端判「AI助手」）")
        manual = next(r for r in rows if r["id"] == "c_manual")
        self.assertFalse(manual["metadata"].get("landing"))
        # 其他 scope 不受影响，且不带 metadata 键
        for item in self.repo.opportunities():
            self.assertNotIn("metadata", item)

    # -- 6) 报名转正：stage + 契约标记 --------------------------------------
    def test_promote_marks_stage_and_contract(self):
        out = self.repo.promote_candidate("c_ai")
        self.assertEqual(out["status"], "promoted")
        with self.Session() as s:
            project = s.get(Project, "c_ai")
            stage = project.workflow_stage
            meta = project.metadata_
        self.assertEqual(stage, "盲投信息")
        promotion = meta["candidate_promotion"]
        self.assertEqual(promotion["to_sheet"], "9月")
        self.assertEqual(promotion["editor"], "王明李")
        self.assertEqual(promotion["status"], "pending")
        self.assertIn("at", promotion)
        row = meta["candidate_row"]
        self.assertEqual(
            set(row.keys()),
            {"项目名称", "甲方名称", "招标公告网址", "开标时间", "渠道"},
        )
        self.assertTrue(row["项目名称"], "candidate_row.项目名称 必须取公告标题，不得为空")
        self.assertEqual(row["甲方名称"], "")
        self.assertIn("ccgp-sichuan.gov.cn", row["招标公告网址"])
        self.assertEqual(row["开标时间"], "")
        self.assertTrue(row["渠道"])
        # 转正后退出候选队列
        self.assertNotIn("c_ai", {r["id"] for r in self.repo.opportunities("candidates")})
        # 重复报名 / 已忽略 / 非候选阶段 → 状态冲突
        with self.assertRaises(ProjectStateError):
            self.repo.promote_candidate("c_ai")
        with self.assertRaises(ProjectStateError):
            self.repo.promote_candidate("c_dismissed")
        with self.assertRaises(ProjectStateError):
            self.repo.promote_candidate("c_stage_off")
        with self.assertRaises(ProjectNotFoundError):
            self.repo.promote_candidate("c_nope")
        # 候选无月表指针：编辑必须拒绝（报名≠有月表行，回写引擎加行后才定位得到）
        with self.assertRaises(FieldEditError):
            self.repo.apply_field_edits("c_manual", [{"field": "甲方名称", "value": "x"}])

    # -- 7) 忽略/不报名：dismissed 标记，reason 可空 --------------------------
    def test_dismiss_marks_and_leaves_queue(self):
        out = self.repo.dismiss_candidate("c_manual", reason="预算过低")
        self.assertEqual(out["status"], "dismissed")
        with self.Session() as s:
            project = s.get(Project, "c_manual")
            stage = project.workflow_stage
            dismissed = project.metadata_["dismissed"]
        self.assertEqual(stage, "公告候选", "忽略不改阶段：队列由 dismissed 标记过滤")
        self.assertEqual(dismissed["reason"], "预算过低")
        self.assertEqual(dismissed["editor"], "王明李")
        self.assertNotIn("c_manual", {r["id"] for r in self.repo.opportunities("candidates")})
        # 忽略时 reason 可空（「忽略」动作不填原因）
        self.repo.dismiss_candidate("c_manual", reason="")
        with self.Session() as s:
            self.assertEqual(s.get(Project, "c_manual").metadata_["dismissed"]["reason"], "")
        # 已报名的候选不能改成忽略
        with self.assertRaises(ProjectStateError):
            self.repo.dismiss_candidate("c_promoted")

    # -- 8) 我方结果正源行判定 ------------------------------------------------
    def test_own_results_only_positive_source_rows(self):
        rows = {r["projectId"]: r for r in self.repo.own_results()}
        # 正源：ledger_2026 两行
        self.assertIn("p_edit", rows)
        self.assertEqual(rows["p_edit"]["result"], "中标")
        self.assertIn("祥能", rows["p_edit"]["winner"])
        self.assertIn("p_lost", rows)
        self.assertEqual(rows["p_lost"]["result"], "未中标")
        # 无 family 旧行：'#2026年!' 才认
        self.assertIn("p_legacy_ok", rows)
        self.assertNotIn("p_legacy_no", rows)
        # 一律不算：竞对统计(exclude 标) / ledger_2025 / blind_win_sheet /
        # not_participated / 字符串 exclude 标
        for excluded in ("p_comp", "p_2025", "p_blindwin", "p_notpart", "p_strflag"):
            self.assertNotIn(excluded, rows, f"{excluded} 不是正源行，不得作为我方结果")

    # -- 9) API 层：400/404/409 映射 + scope=candidates + /api/results/own ----
    # 注意：字母序本方法最先执行，全部读写都落在 p_api_edit / c_api / c_api2
    # 专用夹具上，不碰其它用例的数据。
    def test_api_endpoints(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app import main as app_main

        client = TestClient(app_main.app)
        with patch.object(app_main, "repository", self.repo):
            # 编辑：非法入参 400 / 缺项目 404 / 无指针 400 / 成功 saved
            r = client.post("/api/projects/p_api_edit/fields", json={"edits": []})
            self.assertEqual(r.status_code, 400)
            r = client.post("/api/projects/p_nope/fields", json={"edits": [{"field": "甲方名称", "value": "x"}]})
            self.assertEqual(r.status_code, 404)
            r = client.post("/api/projects/p_noptr/fields", json={"edits": [{"field": "甲方名称", "value": "x"}]})
            self.assertEqual(r.status_code, 400)
            self.assertIn("月表", r.json()["detail"], "无指针必须显式报错，不静默")
            r = client.post(
                "/api/projects/p_api_edit/fields",
                json={"edits": [{"field": "BidOS备注", "value": "API 编辑"}]},
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "saved")
            self.assertEqual(r.json()["excel"], {"sheet": "9月", "row": "200"})

            # 候选队列 + 报名 + 重复报名 409 + 忽略
            r = client.get("/api/opportunities", params={"scope": "candidates"})
            self.assertEqual(r.status_code, 200)
            ids = {x["id"] for x in r.json()}
            self.assertIn("c_api", ids)
            r = client.post("/api/candidates/c_api/promote", json={})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["promotion"]["to_sheet"], "9月")
            r = client.post("/api/candidates/c_api/promote", json={})
            self.assertEqual(r.status_code, 409)
            r = client.get("/api/opportunities", params={"scope": "candidates"})
            self.assertNotIn("c_api", {x["id"] for x in r.json()})
            r = client.post("/api/candidates/c_api2/dismiss", json={"reason": "区域不符"})
            self.assertEqual(r.status_code, 200)
            r = client.get("/api/opportunities", params={"scope": "candidates"})
            self.assertNotIn("c_api2", {x["id"] for x in r.json()})

            # 正源我方结果
            r = client.get("/api/results/own")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["total"], len(body["results"]))
            self.assertIn("p_edit", {x["projectId"] for x in body["results"]})


if __name__ == "__main__":
    unittest.main()
