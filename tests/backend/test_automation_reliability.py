"""自动化失败可见、金额补取和跨年度防误关联；全部使用隔离数据。"""
import copy
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps/api'))
from app.domain import IngestedItem
from app.services.announcement_landing import AnnouncementLandingService, _ProjectTarget
from app.services.blind_collection import BlindCollection, TZ
from app.services.blind_workspace import BlindWorkspace
from app.services.portfolio_announcement_facts import PortfolioAnnouncementFacts
from app.services.result_announcement_backfill import BackfillOutcome, ParsedResult, ResultAnnouncementBackfill
from app.services.scheduler import SchedulerHost, SchedulerSettings
from app.services.tender_details import TenderDetailsService, extract_details, VERSION

NOW = datetime(2026, 10, 8, 9, tzinfo=TZ)
NAME = '人保财险扬州市分公司劳务外包服务入围采购项目'
CLIENT = '中国人民财产保险股份有限公司扬州市分公司'


def test_resume_retries_failed_channel_only():
    prior = {'lastRun': datetime.now(TZ).isoformat(), 'newCount': 2, 'channels': [
        {'name': '成功来源', 'status': '已读取'}, {'name': '失败来源', 'status': '采集失败'}]}
    service = BlindCollection(SimpleNamespace(configs=[]), None)
    service.catalog = lambda: [{'name': name, 'url': 'https://example.test/' + str(i)}
                              for i, name in enumerate(('成功来源', '失败来源'))]
    called = []
    # 逐站采集现在会同时收到窗口起止（_public_listing(channel, window_end, window_start)）。
    service._public_listing = lambda c, *a, **k: (called.append(c['name']) or c, [], '已读取', '')
    with patch('app.services.blind_collection.read_state', return_value={'collection': prior}), \
         patch('app.services.blind_collection.write_state'), \
         patch.object(BlindWorkspace, 'expire_candidates', return_value=0), \
         patch.object(TenderDetailsService, 'refresh_missing', return_value=[]):
        report = service._run(resume=True)
    assert called == ['失败来源']
    assert report['usable'] == 2 and report['newCount'] == 2


def test_window_floor_reaches_channels_and_report():
    """周一 72 小时的下限要真的落在每个渠道的筛选窗口上，而不只是写在报告里。"""
    seen = {}
    service = BlindCollection(SimpleNamespace(configs=[]), None)
    service.catalog = lambda: [{'name': '来源', 'url': 'https://example.test/x'}]

    def listing(channel, window_end=None, window_start=None):
        seen['end'], seen['start'] = window_end, window_start
        return channel, [], '已读取', ''

    service._public_listing = listing
    # 上次采集＝72 小时前（周五 16:30 → 周一 16:30）
    prior = {'windowEnd': (datetime.now(TZ) - timedelta(hours=72)).isoformat()}
    with patch('app.services.blind_collection.read_state', return_value={'collection': prior}), \
         patch('app.services.blind_collection.write_state'), \
         patch.object(BlindWorkspace, 'expire_candidates', return_value=0), \
         patch.object(TenderDetailsService, 'refresh_missing', return_value=[]):
        report = service._run(window_hours=72.0)
    hours = (seen['end'] - seen['start']).total_seconds() / 3600
    assert round(hours, 1) == 72.0, f'渠道侧窗口应为 72 小时，实际 {hours}'
    assert report['windowHours'] == 72.0
    assert report['windowStart'] == seen['start'].isoformat()


def test_search_failure_is_partial_and_auto_sync_gate_respected():
    svc = MagicMock()
    svc.run.return_value = BackfillOutcome(errors=['来源超时'])
    host = SchedulerHost(result_backfill=svc, settings=SchedulerSettings(auto_sync=False))
    record = host.run_result_backfill()
    assert record.status == 'partial_failure' and record.failures == 1
    svc.run.assert_called_once_with(use_active_search=False)


def test_runtime_status_includes_api_jobs():
    host = SchedulerHost()
    host._scheduler = SimpleNamespace(running=True, get_jobs=lambda: [
        SimpleNamespace(id='blind_daily_collection', next_run_time=NOW, trigger='cron[hour=9]'),
        SimpleNamespace(id='blind_candidate_details', next_run_time=NOW, trigger='interval[10m]'),
        SimpleNamespace(id='focus_competitor_public_results', next_run_time=NOW, trigger='interval[10m]')])
    status = host.status()
    assert status['job_count'] == 3
    assert len(status['next_runs']) == 3


def test_managed_external_job_does_not_run_when_disabled():
    callback = MagicMock()
    host = SchedulerHost(settings=SchedulerSettings(auto_sync=False))
    assert host.run_managed_job('collection', callback, external=True).status == 'skipped'
    callback.assert_not_called()


def test_missing_amount_retries_without_empty_loop():
    detail = {'version': VERSION, 'status': '已读取', 'fields': {'招标金额': ''},
              'amountStatus': '原文未找到', 'lastAttempt': NOW.isoformat()}
    assert not TenderDetailsService.retry_due(detail, NOW + timedelta(minutes=10))
    assert TenderDetailsService.retry_due(detail, NOW + timedelta(hours=6))
    detail['fields']['招标金额'] = '最高限价：40万元'
    assert not TenderDetailsService.retry_due(detail, NOW + timedelta(hours=6))


def test_explicit_undisclosed_and_unread_are_distinct():
    detail = extract_details('扬州劳务外包服务采购项目', '采购预算：不公开；采购人：扬州市某公司')
    assert detail['amountStatus'] == '未公开'
    assert not detail['fields']['招标金额']
    assert extract_details('某劳务外包项目', '登录后查看')['amountStatus'] == '待读取'


def test_client_only_does_not_bind_to_old_project():
    target = _ProjectTarget('p1', CLIENT + '旧办公场所物业采购项目', CLIENT, NOW)
    notice = IngestedItem(external_id='a', source_id='s', title=CLIENT + '2026年劳务外包采购公告',
                          text='采购人：' + CLIENT, published_at=NOW)
    assert AnnouncementLandingService._match_projects(notice, [target]) is None


def test_exact_name_ambiguous_years_does_not_pick_latest():
    target = _ProjectTarget('p1', NAME, CLIENT, NOW)
    notice = IngestedItem(external_id='a', source_id='s', title=NAME + '中标公告',
                          text='采购人：' + CLIENT, published_at=NOW)
    assert AnnouncementLandingService._match_projects(notice, [target]).project_id == 'p1'
    assert AnnouncementLandingService._match_projects(notice, [target, replace(target, project_id='p2')]) is None


def test_result_match_preserves_year_and_round():
    match = ResultAnnouncementBackfill._title_matches_project
    assert not match('2025年' + NAME, '2026年' + NAME + '中标公告')
    assert not match(NAME + '（二次）', NAME + '中标公告')
    assert match(NAME + '（二次）', NAME + '（二次）中标公告')
    target = {'opening_date': '2026-10-10', 'portfolio_record': True}
    assert not ResultAnnouncementBackfill._date_matches_target(target, date(2025, 10, 11))
    assert ResultAnnouncementBackfill._date_matches_target(target, date(2026, 10, 11))


def result_row():
    return {'id': 'result_2026', 'name': NAME, 'client': CLIENT, 'date': '2026-09-01',
            'ownMark': '', 'result': '待公布', 'winner': '', 'amount': '', 'publishedAt': '',
            'resultUrl': '', 'sourceFields': {}, 'sourceRows': []}


def award():
    return ParsedResult(winners=('江苏百得服务外包有限公司',), amount_yuan=42000, owner_ok=True,
                        result_date=date(2026, 9, 10), source_url='https://example.test/award', source_title=NAME + '中标公告')


def test_portfolio_facts_fill_blanks_preserve_own_result_and_are_idempotent(tmp_path):
    store = PortfolioAnnouncementFacts(tmp_path / 'facts.json')
    row = result_row()
    assert store.record(row, award())
    assert not store.record(row, award())
    store.apply([row])
    assert row['winner'] == '江苏百得服务外包有限公司'
    assert row['amount'] == '42000元'
    assert row['publishedAt'] == '2026-09-10'
    assert row['ownMark'] == '' and row['result'] == '待公布'
    assert row['announcementEvidence']['url'] == 'https://example.test/award'


def test_portfolio_facts_reject_prior_year_and_keep_source_values(tmp_path):
    store = PortfolioAnnouncementFacts(tmp_path / 'facts.json')
    row = result_row()
    assert not store.record(row, replace(award(), result_date=date(2025, 9, 10)))
    assert store.record(row, award())
    row['winner'] = '人员已填写供应商'
    row['sourceFields']['中标金额（万元）'] = '/'
    store.apply([row])
    assert row['winner'] == '人员已填写供应商' and row['amount'] == ''


def test_other_channels_added_to_backfill_with_date_boundary():
    row = {**result_row(), 'channel': '续签'}
    future = {**row, 'id': 'result_future', 'date': '2026-10-09'}
    sf = MagicMock()
    sf.return_value.__enter__.return_value.scalars.return_value = []
    service = ResultAnnouncementBackfill(session_factory=sf, clock=lambda: NOW)
    with patch('app.services.blind_workspace.read_state', return_value={}), \
         patch('app.services.portfolio_workspace.PortfolioWorkspace.snapshot', return_value={'blindRecords': [], 'results': [row, future]}):
        targets, _, _ = service._load_targets()
    assert len(targets) == 1 and targets[0]['project_id'] == row['id']
    assert targets[0]['portfolio_record']['ownMark'] == ''


def test_backfill_file_lock_prevents_second_host():
    import fcntl
    svc = MagicMock()
    host = SchedulerHost(result_backfill=svc)
    with open('/tmp/bidos_result_backfill.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert host.run_result_backfill().status == 'skipped'
    svc.run.assert_not_called()


def test_other_package_is_not_same_result():
    match = ResultAnnouncementBackfill._title_matches_project
    assert not match(NAME + '包1', NAME + '包2中标公告')
    assert match(NAME + '包1', NAME + '包1中标公告')


def test_unchanged_announcement_is_not_reported_as_updated():
    service = ResultAnnouncementBackfill(session_factory=None)
    service._find_announcement = MagicMock(return_value=award())
    service._write_joined = MagicMock(return_value=False)
    outcome = BackfillOutcome()
    service._process_joined({'project_id': 'p1'}, outcome, gate=None)
    assert outcome.backfilled_joined == 0 and outcome.skipped['already_done'] == 1


def make_failing_gate(now, error):
    from app.connectors.official_http import FetchError
    from app.services.result_announcement_backfill import ConnectorSearchGate
    gate = ConnectorSearchGate(registry_configs=[], clock=lambda: now[0])
    gate._json_conn = MagicMock()
    gate._json_conn._get_json.side_effect = error
    gate._collect_json = lambda payload: []
    return gate


def no_wait_budget():
    from app.services.result_announcement_backfill import SearchBudget
    return SearchBudget(max_requests=2, gap_seconds=0, sleeper=lambda _: None)


def test_network_timeout_cools_source_and_recovers_after_ten_minutes():
    from app.connectors.official_http import FetchError
    now = [NOW]
    gate = make_failing_gate(now, FetchError('timeout', 'TLS handshake timed out'))
    assert gate.search('保洁', no_wait_budget()) == []
    assert not gate.available
    assert gate.source_status()[0]['status'] == '冷却中'
    assert gate.source_status()[0]['nextRetryAt'] == (NOW + timedelta(minutes=10)).isoformat()
    gate.search('物业', no_wait_budget())
    assert gate._json_conn._get_json.call_count == 1
    now[0] += timedelta(minutes=10)
    assert gate.available
    gate._json_conn._get_json.side_effect = None
    gate._json_conn._get_json.return_value = {}
    gate.search('物业', no_wait_budget())
    assert gate._json_conn._get_json.call_count == 2
    assert gate.source_status()[0]['status'] == '可用'


def test_rate_limit_cools_but_query_parameter_error_does_not():
    from app.connectors.official_http import FetchError
    now = [NOW]
    limited = make_failing_gate(now, FetchError('http_error', 'HTTP 429'))
    limited.search('保洁', no_wait_budget())
    assert limited.source_status()[0]['errorKind'] == 'http_429' and not limited.available
    invalid = make_failing_gate(now, FetchError('http_error', 'HTTP 400 bad query'))
    invalid.search('保洁', no_wait_budget())
    assert invalid.available and invalid.source_status()[0]['nextRetryAt'] is None
    assert invalid.source_status()[0]['status'] == '查询失败'


def test_eight_projects_do_not_repeat_failed_source_or_report_no_results():
    from app.connectors.official_http import FetchError
    now = [NOW]
    gate = make_failing_gate(now, FetchError('connection_error', 'TLS handshake failed'))
    targets = [{'project_id': 'p' + str(i), 'name': NAME, 'client_name': CLIENT,
                'opening_date': '2026-09-01'} for i in range(8)]
    service = ResultAnnouncementBackfill(session_factory=None, targets_source=lambda: (targets, [], []),
                                        pointer_source=lambda _: [], search_gate=gate, clock=lambda: NOW)
    with patch('app.services.blind_workspace.write_state') as write:
        outcome = service.run()
    assert gate._json_conn._get_json.call_count == 1 and outcome.searched_projects == 1
    assert outcome.errors and outcome.skipped['source_unavailable'] == 8
    assert outcome.skipped['no_announcement'] == 0
    state = write.call_args.args[0]
    assert state['resultMonitor']['status'] == '部分失败'
    assert state['resultSourceHealth']['sichuan_gov_procurement']['nextRetryAt']
    with patch('app.services.blind_workspace.write_state') as write:
        outcome = service.run()
    assert outcome.searched_projects == 0 and outcome.errors
    assert write.call_args.args[0]['resultMonitor']['status'] == '部分失败'
