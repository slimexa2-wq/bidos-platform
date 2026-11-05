"""模型适配层测试（2026-10-09 接手后新增）。

覆盖三件事：

1. **provider 契约**：SSE 流式累积、非流式 JSON 回退、错误分类。
2. **整理逻辑的防幻觉闸门**：引用在正文里找不到的取值必须被丢弃。
3. **不可越界**：规则已抽到的字段、以及任何「是否参与」的判断，模型都不许改。

测试里的模型端点是本进程内起的 HTTP 服务，只验证**传输与解析契约**；
它不代表已接入真实模型 —— 真实模型由 BIDOS_AI_* 环境变量在运行期注入。
"""

from __future__ import annotations

import copy
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps/api'))

from app.ai.base import AIProviderError
from app.ai.mock import MockAIProvider
from app.ai.openai_compatible import OpenAICompatibleProvider
from app.services.ai_enrichment import (
    ALLOWED_FIELDS,
    SYSTEM_PROMPT,
    apply_proposals,
    enrich_detail,
)

# ---------------------------------------------------------------------------
# 本地假端点：只实现 OpenAI 兼容协议的线格式
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        path = self.path

        if path.startswith("/auth"):
            body = b'{"error":{"message":"bad key"}}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not payload.get("stream") or path.startswith("/json"):
            self._json_response()
            return

        self._sse_response()

    def _json_response(self):
        text = self.server.reply_text
        body = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_response(self):
        text = self.server.reply_text
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        frames = [
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"content": text[: len(text) // 2]}}]},
            {"choices": [{"delta": {"content": text[len(text) // 2 :]}}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 34, "total_tokens": 55},
            },
        ]
        for frame in frames:
            self.wfile.write(f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


@pytest.fixture()
def fake_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # 默认回复：一条合规的整理结果
    server.reply_text = json.dumps(
        {
            "items": [
                {
                    "field": "代理机构",
                    "value": "成都某招标代理有限公司",
                    "quote": "招标代理机构：成都某招标代理有限公司",
                }
            ]
        },
        ensure_ascii=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _provider(base_url: str, **kwargs) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(base_url=base_url, model="fake-model", **kwargs)


# ---------------------------------------------------------------------------
# provider 契约
# ---------------------------------------------------------------------------


def test_sse_stream_is_accumulated(fake_endpoint):
    provider = _provider(fake_endpoint)
    text = provider.complete(system="s", user="u")
    assert "成都某招标代理有限公司" in text
    assert provider.last_call["status"] == "ok"
    assert provider.last_call["usage"]["total_tokens"] == 55
    assert provider.available is True


def test_non_stream_json_response_falls_back(fake_endpoint):
    provider = _provider(fake_endpoint, stream=False)
    text = provider.complete(system="s", user="u")
    assert "招标代理" in text
    assert provider.last_call["usage"]["total_tokens"] == 18


def test_endpoint_ignoring_stream_still_parses(fake_endpoint):
    """端点忽略 stream 直接回 JSON 时，必须能解析而不是报空。"""
    provider = _provider(f"{fake_endpoint}/json")
    text = provider.complete(system="s", user="u")
    assert "招标代理" in text


def test_auth_error_is_classified(fake_endpoint):
    provider = _provider(fake_endpoint.replace("/v1", "/auth"), max_retries=0)
    with pytest.raises(AIProviderError) as excinfo:
        provider.complete(system="s", user="u")
    assert excinfo.value.kind == "auth"
    assert provider.last_call["status"] == "failed"


def test_unreachable_endpoint_is_classified():
    # 取一个当前确定没人监听的端口
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = _provider(f"http://127.0.0.1:{port}/v1", timeout=1.5, max_retries=0)
    with pytest.raises(AIProviderError) as excinfo:
        provider.complete(system="s", user="u")
    assert excinfo.value.kind == "unreachable"


def test_mock_provider_is_never_available_evidence():
    provider = MockAIProvider()
    assert provider.available is False
    assert provider.mode == "mock"


# ---------------------------------------------------------------------------
# 整理逻辑
# ---------------------------------------------------------------------------


BODY = (
    "某区人力资源和社会保障局劳务派遣服务采购公告\n"
    "采购人名称：某区人力资源和社会保障局\n"
    "招标代理机构：成都某招标代理有限公司\n"
    "项目实施地点：成都市某区\n"
    "本项目预算金额：人民币 800 万元\n"
)


def _detail(**fields):
    base = {
        "项目名称": "某区劳务派遣服务采购公告",
        "甲方名称": "",
        "代理机构": "",
        "项目区域": "",
        "项目实施地点": "",
        "项目编号": "",
        "采购方式": "",
        "服务期限": "",
        "招标金额": "人民币 800 万元",
        "报名截止时间": "",
        "开标时间": "",
    }
    base.update(fields)
    return {"version": 4, "fields": base, "evidence": {}, "status": "部分读取"}


def test_enrichment_fills_empty_field_when_quote_verified(fake_endpoint):
    provider = _provider(fake_endpoint)
    result = enrich_detail(provider, title="某区劳务派遣服务采购公告", body=BODY, detail=_detail())
    assert result["fields"]["代理机构"] == "成都某招标代理有限公司"
    assert result["evidence"]["代理机构"]["ai"] is True
    assert result["evidence"]["代理机构"]["label"] == "模型整理"
    assert result["aiAssist"]["status"] == "ok"
    assert "代理机构" in result["aiAssist"]["filled"]


def test_hallucinated_quote_is_dropped(fake_endpoint):
    fake_endpoint  # 占用 fixture 以便取端口
    server = None
    # 直接换一个返回「正文里没有的引用」的端点
    class _Bad(_Handler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            text = json.dumps(
                {
                    "items": [
                        {
                            "field": "代理机构",
                            "value": "北京某不存在的代理公司",
                            "quote": "招标代理机构：北京某不存在的代理公司",
                        }
                    ]
                },
                ensure_ascii=False,
            )
            self.server.reply_text = text
            self._sse_response()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Bad)
    server.reply_text = ""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = _provider(f"http://127.0.0.1:{server.server_port}/v1")
        result = enrich_detail(provider, title="t", body=BODY, detail=_detail())
        assert result["fields"]["代理机构"] == ""
        assert result["aiAssist"]["status"] == "empty"
        assert result["aiAssist"]["rejected"][0]["reason"].startswith("引用未在正文中找到")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_rule_extracted_value_is_never_overwritten(fake_endpoint):
    provider = _provider(fake_endpoint)
    detail = _detail(代理机构="规则已抽到的代理机构")
    result = enrich_detail(provider, title="t", body=BODY, detail=detail)
    assert result["fields"]["代理机构"] == "规则已抽到的代理机构"
    assert "代理机构" not in (result["aiAssist"]["requested"] or [])


def test_provider_failure_keeps_rule_fields_and_records_reason():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = _provider(f"http://127.0.0.1:{port}/v1", timeout=1.5, max_retries=0)
    detail = _detail(甲方名称="某区人力资源和社会保障局")
    result = enrich_detail(provider, title="t", body=BODY, detail=detail)
    assert result["fields"]["甲方名称"] == "某区人力资源和社会保障局"
    assert result["fields"]["代理机构"] == ""
    assert result["aiAssist"]["status"] == "failed"
    assert result["aiAssist"]["error"]["kind"] == "unreachable"


def test_empty_body_is_skipped_without_calling_model(fake_endpoint):
    provider = _provider(fake_endpoint)
    result = enrich_detail(provider, title="t", body="", detail=_detail())
    assert result["aiAssist"]["status"] == "skipped"
    assert result["fields"]["代理机构"] == ""


def test_system_prompt_forbids_decision_making():
    assert "不要建议是否参与投标" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 注册表状态
# ---------------------------------------------------------------------------


def _settings(**kwargs) -> SimpleNamespace:
    base = dict(
        ai_enabled=False,
        ai_provider="openai_compatible",
        ai_base_url="",
        ai_model="",
        ai_api_key="",
        ai_timeout=30.0,
        ai_proxy="",
        ai_max_per_run=3,
        ai_body_chars=6000,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_registry_reports_disabled_reason(monkeypatch):
    from app.ai import registry

    monkeypatch.setattr(registry, "settings", _settings(ai_enabled=False))
    registry.reset_provider()
    provider, reason = registry.build_provider()
    assert provider is None
    assert "未启用" in reason


def test_registry_reports_missing_config(monkeypatch):
    from app.ai import registry

    monkeypatch.setattr(registry, "settings", _settings(ai_enabled=True))
    registry.reset_provider()
    provider, reason = registry.build_provider()
    assert provider is None
    assert "BIDOS_AI_BASE_URL" in reason


def test_registry_builds_provider_when_configured(monkeypatch):
    from app.ai import registry

    monkeypatch.setattr(
        registry,
        "settings",
        _settings(ai_enabled=True, ai_base_url="http://127.0.0.1:9/v1", ai_model="m"),
    )
    registry.reset_provider()
    provider, reason = registry.build_provider()
    assert provider is not None and reason == ""
    status = registry.provider_status()
    assert status["available"] is True and status["mockFallback"] is False
    registry.reset_provider()


# ---------------------------------------------------------------------------
# 开关解析（2026-10-09 取证时踩到的真实缺陷，必须回归）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
     (" On ", True), ("0", False), ("false", False), ("off", False), ("", False), ("no", False)],
)
def test_env_flag_accepts_common_truthy_spellings(monkeypatch, value, expected):
    from app.core.config import env_flag

    monkeypatch.setenv("BIDOS_TEST_FLAG", value)
    assert env_flag("BIDOS_TEST_FLAG") is expected


# ---------------------------------------------------------------------------
# 写入闸门（Agent 通道与 HTTP Provider 共用同一套规则）
# ---------------------------------------------------------------------------


def test_apply_proposals_fills_and_marks_ai_evidence():
    detail = _detail()
    result = apply_proposals(
        detail,
        [{"field": "代理机构", "value": "成都某招标代理有限公司", "quote": "招标代理机构：成都某招标代理有限公司"}],
        body=BODY,
        origin={"provider": "workbuddy-agent", "model": "agent", "kind": "agent"},
    )
    assert result["fields"]["代理机构"] == "成都某招标代理有限公司"
    assert result["evidence"]["代理机构"]["ai"] is True
    assert result["aiAssist"]["origin"] == "agent"
    assert result["aiAssist"]["filled"] == ["代理机构"]


def test_apply_proposals_drops_unverifiable_quote():
    result = apply_proposals(
        _detail(),
        [{"field": "代理机构", "value": "北京某不存在的公司", "quote": "招标代理机构：北京某不存在的公司"}],
        body=BODY,
        origin={"provider": "workbuddy-agent", "kind": "agent"},
    )
    assert result["fields"]["代理机构"] == ""
    assert result["aiAssist"]["status"] == "empty"
    assert "引用未在正文中找到" in result["aiAssist"]["rejected"][0]["reason"]


def test_apply_proposals_never_overwrites_rule_value():
    detail = _detail(甲方名称="某区人力资源和社会保障局")
    result = apply_proposals(
        detail,
        [{"field": "甲方名称", "value": "有人伪造的甲方", "quote": "采购人名称：某区人力资源和社会保障局"}],
        body=BODY,
        origin={"provider": "workbuddy-agent", "kind": "agent"},
    )
    assert result["fields"]["甲方名称"] == "某区人力资源和社会保障局"
    assert "甲方名称" not in result["aiAssist"]["requested"]


def test_apply_proposals_rejects_oversized_and_duplicate():
    result = apply_proposals(
        _detail(),
        [
            {"field": "项目编号", "value": "X" * 300, "quote": "采购人名称：某区人力资源和社会保障局"},
            {"field": "代理机构", "value": "成都某招标代理有限公司", "quote": "招标代理机构：成都某招标代理有限公司"},
            {"field": "代理机构", "value": "第二家公司", "quote": "招标代理机构：成都某招标代理有限公司"},
        ],
        body=BODY,
        origin={"provider": "workbuddy-agent", "kind": "agent"},
    )
    reasons = [item["reason"] for item in result["aiAssist"]["rejected"]]
    assert any(r.startswith("取值过长") for r in reasons)
    assert "同字段重复提交" in reasons
    assert result["aiAssist"]["filled"] == ["代理机构"]


def test_allowed_fields_exclude_amount_and_dates():
    """金额与时间必须留在规则侧：单位/服务年限不可混算，时间进提醒链路。"""
    for forbidden in ("招标金额", "报名截止时间", "开标时间"):
        assert forbidden not in ALLOWED_FIELDS


# ---------------------------------------------------------------------------
# Agent 通道：正文只认库内原文
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """够用的假会话，但**刻意复刻真实 Session 的提交语义**。

    为什么必须这么做（2026-10-09 线上实测踩过的坑）：

    ``sessionmaker()`` 生成的 Session 作为上下文管理器时，退出只 ``close()``，
    不会 ``commit()``；未提交的改动会被回滚。最初的假会话没有这一层，于是
    「写路径漏了 commit」在单测里完全不可见 —— 接口返回
    ``applied=10 / filledFields=26``，库里 ``aiAssist`` 全是空值。

    所以这里：进入时快照 metadata，未 commit 就退出则还原；commit 之后才生效。
    """

    def __init__(self, project, pointers):
        self.project = project
        self.pointers = pointers
        self.rolled_back = False
        self.committed = False
        self._snapshot = None

    def __enter__(self):
        self._snapshot = copy.deepcopy(getattr(self.project, "metadata_", None))
        return self

    def __exit__(self, *exc):
        if not self.committed and self.project is not None and self._snapshot is not None:
            self.project.metadata_ = copy.deepcopy(self._snapshot)
        return False

    def get(self, _model, key, **_kwargs):
        return self.project if self.project is not None and key == self.project.id else None

    def scalars(self, stmt):
        # 按目标表区分：查 projects 返回项目，查 source_pointers 返回正文指针。
        if "projects" in str(stmt):
            return _FakeScalars([self.project] if self.project is not None else [])
        return _FakeScalars(self.pointers)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True
        if self.project is not None and self._snapshot is not None:
            self.project.metadata_ = copy.deepcopy(self._snapshot)


def _project(pid="cand-1", **meta):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=pid,
        name="某区劳务派遣服务采购公告",
        workflow_stage="公告候选",
        metadata_={"tender_detail": _detail(), **meta},
        created_at="2026-10-09T10:00:00+08:00",
    )


def _pointer(text):
    from types import SimpleNamespace

    return SimpleNamespace(
        project_id="cand-1", original_text=text, metadata_={"purpose": "tender_detail"}
    )


def _handoff(project, pointers):
    from app.services.ai_handoff import AiHandoff

    return AiHandoff(lambda: _FakeSession(project, pointers))


def test_handoff_apply_uses_db_body_not_caller_supplied_text():
    """调用方即使伪造一份"正文"，也不能让幻觉引用通过 —— 校验只认库内原文。"""
    from app.services.ai_handoff import AiHandoff  # noqa: F401

    project = _project()
    handoff = _handoff(project, [_pointer(BODY)])
    report = handoff.apply(
        [
            {
                "project_id": "cand-1",
                "body": "招标代理机构：某伪造公司",  # 调用方夹带，应被忽略
                "items": [{"field": "代理机构", "value": "某伪造公司", "quote": "招标代理机构：某伪造公司"}],
            }
        ]
    )
    assert report["applied"] == 0
    assert report["reports"][0]["status"] == "no_change"
    assert project.metadata_["tender_detail"]["fields"]["代理机构"] == ""


def test_handoff_dry_run_does_not_persist():
    project = _project()
    handoff = _handoff(project, [_pointer(BODY)])
    report = handoff.apply(
        [
            {
                "project_id": "cand-1",
                "items": [{"field": "代理机构", "value": "成都某招标代理有限公司", "quote": "招标代理机构：成都某招标代理有限公司"}],
            }
        ],
        dry_run=True,
    )
    assert report["dryRun"] is True
    assert report["reports"][0]["status"] == "validated"
    assert project.metadata_["tender_detail"]["fields"]["代理机构"] == ""


def test_handoff_apply_persists_and_matches_expectation():
    project = _project()
    handoff = _handoff(project, [_pointer(BODY)])
    report = handoff.apply(
        [
            {
                "project_id": "cand-1",
                "items": [{"field": "代理机构", "value": "成都某招标代理有限公司", "quote": "招标代理机构：成都某招标代理有限公司"}],
            }
        ]
    )
    assert report["applied"] == 1
    detail = project.metadata_["tender_detail"]
    assert detail["fields"]["代理机构"] == "成都某招标代理有限公司"
    assert detail["aiAssist"]["appliedBy"] == "workbuddy-agent"
    assert report["reports"][0]["before"] == {"代理机构": ""}
    assert report["reports"][0]["after"] == {"代理机构": "成都某招标代理有限公司"}


def _capturing_handoff(project, pointers):
    """返回 (handoff, sessions)：sessions 记录每一次打开的会话，便于断言是否提交。"""
    from app.services.ai_handoff import AiHandoff

    sessions: list[_FakeSession] = []

    def factory():
        session = _FakeSession(project, pointers)
        sessions.append(session)
        return session

    return AiHandoff(factory), sessions


def test_handoff_write_paths_must_commit_the_transaction():
    """写路径必须显式 commit —— 否则是「接口报成功、库里没数据」的伪成功。

    2026-10-09 实测：`sessionmaker()` 的 Session 作为上下文管理器退出时只
    ``close()`` 不 ``commit()``，改动被回滚。当时 ``POST /api/ai/apply`` 返回
    ``applied=10 / filledFields=26``，但 ``docker exec bidos-pg psql`` 查到的
    ``aiAssist`` / ``项目编号`` / ``项目区域`` / ``appliedBy`` **全为空**。

    本用例把三条写路径（applied / no_change / clear）全部钉在 commit 上，
    配合 `_FakeSession` 的「未提交即还原」语义，回归时必然失败。
    """
    one = [{"field": "代理机构", "value": "成都某招标代理有限公司", "quote": "招标代理机构：成都某招标代理有限公司"}]

    # 1) 正常写入路径
    project = _project()
    handoff, sessions = _capturing_handoff(project, [_pointer(BODY)])
    handoff.apply([{"project_id": "cand-1", "items": one}])
    assert sessions[-1].committed is True
    assert project.metadata_["tender_detail"]["fields"]["代理机构"] == "成都某招标代理有限公司"

    # 2) 无可补字段（no_change）：仍要提交，才能留下可审计的 aiAssist 记录
    project2 = _project()
    handoff2, sessions2 = _capturing_handoff(project2, [_pointer(BODY)])
    handoff2.apply([{"project_id": "cand-1", "items": []}])
    assert sessions2[-1].committed is True
    assert project2.metadata_["tender_detail"]["aiAssist"]["status"] == "empty"

    # 3) 回滚路径
    project3 = _project()
    handoff3, sessions3 = _capturing_handoff(project3, [_pointer(BODY)])
    handoff3.apply([{"project_id": "cand-1", "items": one}])
    handoff3.clear("cand-1")
    assert sessions3[-1].committed is True
    # clear 只删带「模型整理」标记的字段（键整个移除），规则值与人工值不动
    assert "代理机构" not in project3.metadata_["tender_detail"]["fields"]
    assert "aiAssist" not in project3.metadata_["tender_detail"]


def test_uncommitted_write_is_discarded_by_fake_session():
    """反向自证：假会话确实复刻了真实语义 —— 不 commit 的改动必须看不见。

    这条用例保护的是**测试本身的有效性**：如果哪天有人把 `_FakeSession.__exit__`
    的还原逻辑删掉，上面那条 commit 断言就会退化成永远通过的空断言。
    """
    project = _project()
    session = _FakeSession(project, [_pointer(BODY)])
    with session as opened:
        opened.project.metadata_ = {**opened.project.metadata_, "sentinel": "未提交"}
    assert "sentinel" not in project.metadata_

    session2 = _FakeSession(project, [_pointer(BODY)])
    with session2 as opened:
        opened.project.metadata_ = {**opened.project.metadata_, "sentinel": "已提交"}
        opened.commit()
    assert project.metadata_.get("sentinel") == "已提交"


def test_handoff_rejects_non_candidate_stage():
    project = _project()
    project.workflow_stage = "盲投信息"
    handoff = _handoff(project, [_pointer(BODY)])
    report = handoff.apply([{"project_id": "cand-1", "items": []}])
    assert report["reports"][0]["status"] == "rejected"


def test_handoff_clear_only_removes_ai_marked_fields():
    project = _project()
    detail = project.metadata_["tender_detail"]
    detail["fields"]["代理机构"] = "模型补的"
    detail["fields"]["甲方名称"] = "人工填的"
    detail["evidence"]["代理机构"] = {"label": "模型整理", "ai": True, "text": "模型补的"}
    handoff = _handoff(project, [_pointer(BODY)])
    result = handoff.clear("cand-1")
    assert result["removed"] == ["代理机构"]
    assert project.metadata_["tender_detail"]["fields"]["甲方名称"] == "人工填的"
    assert "代理机构" not in project.metadata_["tender_detail"]["fields"]


def test_handoff_pending_skips_when_no_body_in_db():
    project = _project()
    handoff = _handoff(project, [])
    snapshot = handoff.pending(limit=5, project_id="cand-1")
    assert snapshot["count"] == 0
    assert snapshot["skipped"][0]["reason"].startswith("库内无公告正文")

