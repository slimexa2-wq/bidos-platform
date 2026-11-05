"""盲投工作台：桌面 Excel 是工作正本，数据库保存同步快照与待写请求。"""
from __future__ import annotations
from app.services.file_lock import file_lock

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete
from app.repositories.models import Project, SourcePointer, SourceObject, ProjectResult, Event
from app.services import workdays

ROOT = Path(__file__).resolve().parents[4]

#: 工作正本所在目录（桌面 Excel 是唯一事实源）。
#:
#: 2026-10-10 起默认指向 **团队共享盘**：多人各自电脑都挂载同一份文件，
#: 谁改表其他人立即看到，避免「各自一份正本」的分裂。
#:
#: 跨平台候选路径（按顺序取第一个存在的）：
#:   - BIDOS_WORKBOOK_DIR 环境变量（优先级最高，装的时候由脚本写死）；
#:   - macOS   /Volumes/市场共享文件/AI小程序收集汇总资料
#:   - Windows 映射盘 \\192.168.1.228\市场共享文件\AI小程序收集汇总资料
#:            以及 Z:\AI小程序收集汇总资料（常见映射盘符）
#:   - 本机桌面 AI小程序收集汇总资料（最后兜底）
#:
#: 注意：**先选第一个「存在」的目录**，而不是第一个非空字符串；
#: Windows 上共享盘常映射成不同盘符，写死盘符会让别人找不到文件。
import platform as _platform

_WIN_SHARE_ROOTS = (
    r'\\192.168.1.228\市场共享文件\AI小程序收集汇总资料',
    r'Z:\AI小程序收集汇总资料',
    r'Y:\AI小程序收集汇总资料',
    r'X:\AI小程序收集汇总资料',
    'AI小程序收集汇总资料',              # 映射到「网络位置」时的相对形式
)
_MAC_SHARE_ROOTS = (
    '/Volumes/市场共享文件/AI小程序收集汇总资料',
)


def _pick_workbook_dir():
    """挑一个真正能用的正本目录：环境变量 → 共享盘 → 本机桌面。"""
    override = os.getenv('BIDOS_WORKBOOK_DIR')
    if override:
        return Path(override)              # 显式指定，不做存在性判断（报错信息更明确）
    share = _WIN_SHARE_ROOTS if _platform.system() == 'Windows' else _MAC_SHARE_ROOTS
    home = Path.home()
    roots = share + (
        str(home / 'Desktop' / 'AI小程序收集汇总资料'),
        str(home / 'OneDrive' / 'Desktop' / 'AI小程序收集汇总资料'),
        str(home / 'AI小程序收集汇总资料'),
    )
    for r in roots:
        try:
            if Path(r).is_dir():
                return Path(r)
        except OSError:
            continue
    return Path(roots[-1])                 # 全都不存在时给一个可预期的路径用于报错


DESKTOP = _pick_workbook_dir()
STATE = ROOT / 'data/blind_workspace_state.json'
TZ = ZoneInfo('Asia/Shanghai')
STATUSES = ('待评估', '未报名', '报名未参与', '报名参与')

#: 每日采集时刻（北京时间）。**必须与 main.py 里 blind_daily_collection 的 cron 一致**，
#: 所以在这里定义一次、两处共用，避免各写各的导致口径漂移。
COLLECTION_HOUR = 16
COLLECTION_MINUTE = 30

#: 候选「未进待评估」的目标保留时长（仅作为回退与自述口径）。
CANDIDATE_TTL_HOURS = 24


def next_collection_at(after):
    """严格晚于 ``after`` 的下一个采集时刻（工作日的 16:30）。

    非工作日不采集，所以「下一个采集时刻」要认法定节假日与调休补班日
    （:mod:`app.services.workdays`）。
    """
    local = after.astimezone(TZ) if after.tzinfo else after.replace(tzinfo=TZ)
    cursor = local.date()
    for _ in range(400):  # 足够跨过任何假期，同时防死循环
        moment = datetime(cursor.year, cursor.month, cursor.day,
                          COLLECTION_HOUR, COLLECTION_MINUTE, tzinfo=TZ)
        if moment > local and workdays.is_working_day(cursor):
            return moment
        cursor += timedelta(days=1)
    return local + timedelta(hours=CANDIDATE_TTL_HOURS)  # pragma: no cover


def candidate_deadline(first):
    """候选项目的清理期限 = **下一轮采集时刻**（工作日的 16:30）。

    用户口径（2026-10-09 / 2026-10-10）：「没有进入待评估的 24h 后自动删除，
    **然后**收集新一轮」—— 删除与下一轮采集是同一节奏的两半，所以期限取
    「下一轮采集时刻」，而不是机械的 ``first + 24h``：

    * 正常情况恰好 24 小时：第 N 个工作日 16:30 收的，到第 N+1 个工作日 16:30 清掉，
      清完立刻采新一轮（:meth:`expire_candidates` 就在采集开始时先跑）；
    * **非工作日不采集**，所以周末/假期收集的批次自然活到下一个工作日：
      周五 16:30 收的活到周一 16:30（72 小时），国庆前 9-30 收的活到 10-08（8 天）；
    * **调休自动算对**：2026-10-10（周六补班）16:30 收的批次，下一轮是 10-12（周一），
      活 48 小时；而 10-09 16:30 收的批次下一轮就是 10-10（周六补班），只活 24 小时；
    * 运维手动在非采集时刻补采（例如 19:44 跑一轮），期限仍取下一个 16:30，
      不会出现在「工作时间之外悄悄过期」的情况。

    「新信息」队列的可见性判定与 :meth:`expire_candidates` 的删除判定**共用本函数**，
    避免「界面还显示、后台已删除」这类两套口径的问题。
    """
    return next_collection_at(first)


def workbook_is_open(path):
    """WPS/Excel 持有正本时暂停写入，避免覆盖尚未保存的人工编辑。

    跨平台实现（同一套逻辑在 macOS 与 Windows 上都要成立）：
    - macOS：用 lsof 查谁打开了这个文件；
    - Windows：直接调用系统的 CreateFile 以**独占方式**试开一次。
      打得开 = 没人占着；报「 sharing violation」= 有人（Excel/WPS）正开着。
    - 判不出来时一律按「已打开」处理（宁可暂停同步，也不要覆盖人工编辑）。
    """
    if sys.platform == 'win32':
        return _windows_file_locked(path)
    lsof = shutil.which('lsof')
    if not lsof:
        return False
    try:
        result = subprocess.run([lsof, '-t', str(path)], capture_output=True, text=True, timeout=2, check=False)
        if result.returncode not in (0, 1):
            return True
        return bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return True


def _windows_file_locked(path):
    """Windows 上判断文件是否被占用（Excel/WPS 打开时不允许写）。

    用系统 CreateFile 以「只允许别人读、不允许别人写」的方式试开：
      开得了 → 没人占用（Excel 会以读写方式持有文件，会被拒绝）→ False
      开不了 → 有人占着，或无权限 → 保守按占用处理 → True
    """
    import ctypes
    from ctypes import wintypes
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001          # 故意不给 WRITE / DELETE 共享
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel32.CreateFileW(
        str(path), GENERIC_READ, FILE_SHARE_READ, None,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    invalid = wintypes.HANDLE(-1).value
    if handle and handle != invalid:
        kernel32.CloseHandle(handle)
        return False
    # 打不开。区分「被占用」(32)与「文件不存在/无权限」(2/5)：
    # 后者说明表根本没开，返回 True 会让同步永远暂停，所以按文件存在性再判一次。
    code = ctypes.get_last_error()
    if code == 2:            # ERROR_FILE_NOT_FOUND
        return False
    return True


def text_value(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S') if value.time().isoformat() != '00:00:00' else value.strftime('%Y-%m-%d')
    return str(value).strip()


def normalized(value):
    return re.sub(r'[\W_]', '', unicodedata.normalize('NFKC', str(value or ''))).lower()


def participation(fields):
    raw = text_value(fields.get('报名参与情况') or fields.get('报名参与情况统计'))
    if not raw:
        return '未报名'
    if raw in STATUSES:
        return raw
    if '未参与' in raw or '未通过' in raw:
        return '报名未参与'
    if raw == '入库':
        return '报名参与'
    if any(t in raw for t in ('评估', '审核', '待踏勘', '注册平台', '报名', '人资', '保安')):
        return '待评估'
    return '未报名'


def parse_time(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value
    try:
        v = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return v.replace(tzinfo=TZ) if v.tzinfo is None else v
    except (ValueError, TypeError):
        return None


def writer_module():
    spec = importlib.util.spec_from_file_location('blind_excel_writer', ROOT / 'scripts/excel_writeback.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def workbook_path(pattern):
    # 正本显式登记：整理版、备份和下载副本不得因修改时间较新抢占正本。
    manifest = ROOT / 'config/workbook_sources.json'
    if manifest.exists():
        try:
            filenames = json.loads(manifest.read_text()).get('files', {})
        except (OSError, ValueError) as exc:
            raise RuntimeError('工作正本清单读取失败，请检查来源配置') from exc
        if pattern in filenames:
            filename = filenames[pattern]
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise RuntimeError('工作正本清单包含无效文件名')
            selected = DESKTOP / filename
            if not selected.is_file():
                # 共享盘没挂载是最常见原因，单独说清楚，别让人以为表被删了。
                if not DESKTOP.is_dir():
                    raise RuntimeError(
                        f'工作正本目录不存在：{DESKTOP}。'
                        f'如果是团队共享盘，请先在「访达 → 前往 → 连接服务器」里挂载，'
                        f'挂载后系统会在下一轮同步自动恢复；'
                        f'也可以设置环境变量 BIDOS_WORKBOOK_DIR 指向本机的资料目录。'
                    )
                raise RuntimeError(f'已登记的工作正本不存在：{selected}，请在 config/workbook_sources.json 中更新')
            return selected
    paths = [p for p in DESKTOP.glob(pattern) if not p.name.startswith(('~$', '.'))
             and not any(tag in p.name for tag in ('.bidos-tmp', '.wbtmp-', '.tmp.'))]
    if len(paths) > 1:
        raise RuntimeError('发现多份同类源表，请在工作正本清单中指定文件')
    return paths[0] if paths else None


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def write_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(str(STATE) + '.lock'):
        state = {**read_state(), **state}
        tmp = STATE.with_name(STATE.name + '.' + uuid4().hex + '.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        tmp.replace(STATE)


class BlindWorkspace:
    def __init__(self, session_factory):
        self.sessions = session_factory

    def expire_candidates(self):
        now = datetime.now(timezone.utc)
        count = 0
        with self.sessions() as session:
            rows = session.scalars(select(Project).where(Project.workflow_stage == '公告候选').with_for_update()).all()
            for p in rows:
                meta = p.metadata_ or {}
                if meta.get('candidate_promotion'):
                    continue
                first = parse_time((meta.get('landing') or {}).get('created_at')) or p.created_at
                if first and now >= candidate_deadline(first):
                    # 仅留散列去重标记；公告正文、候选项目与事件正文均清除。
                    pointers = session.scalars(select(SourcePointer).where(SourcePointer.project_id == p.id)).all()
                    for sp in pointers:
                        pointer_meta = sp.metadata_ or {}
                        key = pointer_meta.get('dedup_key')
                        source_id, external_id = pointer_meta.get('source_id'), pointer_meta.get('external_id')
                        if source_id and external_id:
                            obj = session.scalar(select(SourceObject).where(SourceObject.source_id == source_id, SourceObject.external_id == external_id))
                            if obj:
                                obj.title = obj.source_url = obj.normalized_text = None
                                obj.raw_metadata = {'expired_at': now.isoformat()}
                            session.execute(delete(Event).where(Event.project_id.is_(None), Event.metadata_['source_id'].astext == source_id, Event.metadata_['external_id'].astext == external_id))
                        sp.project_id = None
                        sp.original_text = None
                        sp.source_url = None
                        sp.metadata_ = {'dedup_key': key, 'expired_at': now.isoformat(), 'match_mode': 'expired'}
                    session.execute(delete(Event).where(Event.project_id == p.id))
                    session.delete(p)
                    count += 1
            session.commit()
        return count

    def sync(self):
        with file_lock('/tmp/bidos_nas_sync.lock', blocking=False) as _lk:
            if not _lk.acquired:
                return {'status': 'busy', 'message': '正在同步，稍后自动更新'}
        try:
            source = workbook_path('*盲投项目信息*汇总表*.xlsx')
            if not source:
                raise RuntimeError('桌面资料库未找到盲投汇总表')
            if workbook_is_open(source):
                return {'status': 'busy', 'message': '盲投 Excel 正本正在打开，关闭后自动继续同步'}
            w = writer_module()
            cfg = w.load_config(ROOT / 'scripts/writeback_columns.yaml')
            with self.sessions() as session:
                dsn = session.get_bind().url.render_as_string(hide_password=False)
            jobs = w.collect_db_pending(cfg, dsn)
            result = w.run_pass(source, cfg, jobs, dsn, None, False, False)
            if result:
                raise RuntimeError('Excel写入后未确认，请查看同步状态')
            imported = self._read_master(source, w, cfg)
            self._read_own_ledger()
            self._apply_summary_results(w, cfg)
            self._read_own_ledger()
            expired = self.expire_candidates()
            state = {'status': 'ok', 'lastSync': datetime.now(TZ).isoformat(), 'workbook': str(source),
                     'rows': imported, 'expired': expired, 'intervalSeconds': 60}
            write_state(state)
            return state
        except Exception as exc:
            state = {**read_state(), 'status': 'error', 'message': str(exc), 'attemptedAt': datetime.now(TZ).isoformat()}
            write_state(state)
            raise
        finally:
            lock.close()

    def _read_master(self, source, writer, cfg):
        import openpyxl
        before = source.stat().st_mtime_ns
        wb = openpyxl.load_workbook(source, rich_text=True, data_only=False)
        changed = False
        records = []
        seen_ids = set()
        with self.sessions() as session:
            projects = session.scalars(select(Project).with_for_update()).all()
            by_id = {p.id: p for p in projects}
            by_name = {}
            for p in projects:
                by_name.setdefault((normalized(p.name), normalized(p.client_name)), []).append(p)
            for ws in wb:
                if not re.fullmatch(r'(?:[1-9]|1[0-2])月', ws.title):
                    continue
                layout = writer.SheetLayout(ws, cfg)
                # 创建列区由回写器负责；此处仅写稳定标识，原业务值原样保留。
                id_col = layout.bidos_cols['BidOS项目ID']
                for r in range(layout.header_row + 1, layout.last_data_row + 1):
                    fields = {text_value(ws.cell(layout.header_row, c).value): text_value(ws.cell(r, c).value)
                              for c in range(1, ws.max_column + 1) if text_value(ws.cell(layout.header_row, c).value)}
                    name = fields.get('项目名称', '')
                    if not name:
                        continue
                    client = fields.get('甲方名称', '')
                    pid = fields.get('BidOS项目ID', '')
                    p = by_id.get(pid) if pid else None
                    if not pid:
                        matches = by_name.get((normalized(name), normalized(client)), [])
                        p = matches[0] if len(matches) == 1 else None
                        if p is None:
                            # 老表部分甲方未填；只允许唯一同名匹配。
                            matches = [x for x in projects if normalized(x.name) == normalized(name)]
                            p = matches[0] if len(matches) == 1 else None
                        pid = p.id if p else 'prj_blind_' + uuid4().hex[:24]
                        if writer.gate_lockfile(source) is None:
                            ws.cell(r, id_col).value = pid
                            fields['BidOS项目ID'] = pid
                            changed = True
                    if pid in seen_ids:
                        pid = 'prj_blind_' + uuid4().hex[:24]
                        p = None
                        if writer.gate_lockfile(source) is None:
                            ws.cell(r, id_col).value = pid
                            changed = True
                    seen_ids.add(pid)
                    if p is None:
                        p = Project(id=pid, organization_id='org_xiangneng', name=name, source_of_truth='desktop_excel')
                        session.add(p)
                        by_id[pid] = p
                    fields['BidOS项目ID'] = pid
                    records.append((p, ws.title, r, fields))
            if changed:
                if source.stat().st_mtime_ns != before or writer.gate_lockfile(source):
                    raise RuntimeError('Excel正在编辑，下一轮继续同步稳定标识')
                writer.gate_backup(source, cfg)
                tmp = source.with_name(source.stem + '.bidos-tmp.xlsx')
                wb.save(tmp)
                from app.services.excel_formula_values import preserve_formula_caches
                preserve_formula_caches(source, tmp)
                if source.stat().st_mtime_ns != before or writer.gate_lockfile(source):
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError('Excel同步期间已修改，保留您的更改并稍后重试')
                os.replace(tmp, source)
            for p in projects:
                meta = dict(p.metadata_ or {})
                if meta.get('blind_pointer'):
                    meta['blind_present'] = False
                    p.metadata_ = meta
            for p, sheet, row, fields in records:
                meta = dict(p.metadata_ or {})
                meta.update(blind_present=True, blind_fields=fields,
                            blind_pointer={'file': str(source), 'sheet': sheet, 'row': row})
                pid = f'desktop_blind_{p.id}_{sheet}'
                snapshot = ' | '.join(f'{k}={v}' for k, v in fields.items())
                sp = session.get(SourcePointer, pid)
                if sp is None:
                    sp = SourcePointer(id=pid, project_id=p.id, source_name=source.name, source_type='sheet', trust_level='internal')
                    session.add(sp)
                sp.file_name = source.name
                sp.file_id = str(source)
                sp.source_url = str(source)
                sp.sheet_name = sheet
                sp.row_id = str(row)
                sp.original_text = snapshot
                sp.metadata_ = {'canonical': 'desktop', 'project_id': p.id}
                meta['source_pointer'] = {'id': pid, 'source_name': source.name, 'sheet_name': sheet,
                                          'row_id': str(row), 'original_text': snapshot}
                p.name = fields.get('项目名称') or p.name
                p.client_name = fields.get('甲方名称') or None
                p.region = fields.get('项目区域') or None
                p.project_type = fields.get('项目类型') or None
                p.workflow_stage = '盲投信息'
                p.metadata_ = meta
            session.commit()
        wb.close()
        return len(records)

    def _read_own_ledger(self):
        from app.services.portfolio_workspace import PortfolioWorkspace
        # 与两个页面共用同一套按甲方/开标日/名称查证的来源；不再仅按同名关联。
        records = PortfolioWorkspace(self.sessions).snapshot()['blindRecords']
        by_id = {r['id']: r for r in records}
        with self.sessions() as session:
            for p in session.scalars(select(Project).with_for_update()):
                meta = dict(p.metadata_ or {})
                if not meta.get('blind_present'): continue
                row = by_id.get(p.id)
                meta['blind_own_ledger'] = row['ledger'] if row else {}
                meta['blind_ledger_conflict'] = bool(row and row['ledgerConflict'])
                p.metadata_ = meta
            session.commit()

    def record_announcement(self, pid, parsed):
        """只整理可靠的结果事实；供应商名称不能设置我方中标结论。"""
        def is_truly_blank(value):
            # 只补原表真正空着的格；“/”等占位是源表明确内容，保留原样。
            return value is None or not str(value).strip()

        if pid.startswith('source_blind_'):
            from app.services.portfolio_workspace import PortfolioWorkspace
            service = PortfolioWorkspace(self.sessions)
            row = next((r for r in service.snapshot()['blindRecords'] if r['id'] == pid), None)
            if not row or row['status'] not in ('报名参与', '报名未参与'): return False
            amount = '；'.join(x.get('raw', '') for x in parsed.amount_lines if x.get('raw'))
            if not amount and parsed.amount_yuan is not None: amount = f'{parsed.amount_yuan:g}元'
            total_lines = [x for x in parsed.amount_lines if x.get('kind') == 'total']
            award_yuan = parsed.amount_yuan if not parsed.amount_lines or len(total_lines) == 1 else None
            changes = []
            winner = '、'.join(parsed.winners)
            for keys, value in (
                (('中标单位',), winner),
                (('中标价格', '中标金额'), amount),
                (('BidOS中标单位',), winner),
                (('BidOS中标金额原文',), amount),
                (('BidOS结果公布',), '已公布'),
                (('BidOS结果公告网址',), parsed.source_url or ''),
            ):
                key = next((k for k in keys if k in row['fieldSources']), None)
                current = row['fields'].get(key) if key else None
                may_update_status = key == 'BidOS结果公布' and text_value(current) in ('待公布', '未公布', '暂未公布')
                if key and value and (is_truly_blank(current) or may_update_status):
                    changes.append({'field': key, 'baseValue': row['fields'].get(key, ''), 'value': value})
            if award_yuan is not None:
                key = '中标金额（万元）'
                if key in row['fieldSources'] and is_truly_blank(row['fields'].get(key)):
                    changes.append({'field': key, 'baseValue': row['fields'].get(key, ''), 'value': f'{award_yuan / 10000:g}'})
            terminal = parsed.terminal if parsed.terminal in ('流标', '废标', '重新招采') else ''
            mark = {'baseValue': row['ownMark'], 'value': terminal} if terminal and row['ledger'] and row['ownMark'] not in ('否', terminal) else None
            if changes or mark:
                service.edit_blind_source(pid, changes, mark)
                return True
            return False
        with self.sessions() as session:
            p = session.get(Project, pid, with_for_update=True)
            if not p:
                return False
            m = dict(p.metadata_ or {})
            fields = m.get('blind_fields') or {}
            if participation(fields) not in ('报名参与', '报名未参与'):
                return False
            amount = '；'.join(x.get('raw', '') for x in parsed.amount_lines if x.get('raw'))
            if not amount and parsed.amount_yuan is not None:
                amount = f'{parsed.amount_yuan:g}元'
            total_lines = [x for x in parsed.amount_lines if x.get('kind') == 'total']
            award_yuan = parsed.amount_yuan if not parsed.amount_lines or len(total_lines) == 1 else None
            terminal = parsed.terminal
            fact = {'winner': '、'.join(parsed.winners), 'amount': amount, 'url': parsed.source_url or '',
                    'title': parsed.source_title, 'date': parsed.result_date.isoformat() if parsed.result_date else '',
                    'terminal': terminal}
            old = m.get('blind_announcement') or {}
            if old.get('date') and (not fact['date'] or fact['date'] < old['date']):
                return False
            if fact == old:
                return False
            m['blind_announcement'] = fact
            history = list(m.get('system_edits') or [])
            pointer = m.get('blind_pointer') or {}
            updates = {'BidOS中标单位': fact['winner'], 'BidOS中标金额原文': amount,
                       'BidOS结果公布': '已公布', 'BidOS结果公告网址': fact['url']}
            if '中标单位' in fields and fact['winner']:
                updates['中标单位'] = fact['winner']
            if '中标金额（万元）' in fields and award_yuan is not None:
                updates['中标金额（万元）'] = f'{award_yuan / 10000:g}'
            if terminal in ('流标', '废标', '重新招采'):
                line = f'{terminal} · {fact["title"]}'
                ledger = m.get('blind_own_ledger') or {}
                if ledger:
                    m['blind_summary_result_write'] = {'status': 'pending', 'base': ledger.get('是否中标', ''),
                        'value': terminal, 'reason': line, 'row': ledger.get('_row'), 'file': ledger.get('_file'), 'sheet': ledger.get('_sheet', '2026年'),
                        'name': ledger.get('项目名称') or p.name, 'client': ledger.get('甲方名称') or p.client_name or ''}
            for key, value in updates.items():
                if key not in fields or fields.get(key, '') == value:
                    continue
                # 公告只填来源表中真正空白的事实列；已有人工值、公式或“/”一律保留。
                if key != 'BidOS结果公布' and not is_truly_blank(fields.get(key)):
                    continue
                # 人工待写优先；该格冲突需同事核对。
                if any(e.get('field') == key and (e.get('status') or (e.get('excel') or {}).get('status')) == 'pending' for e in history):
                    continue
                history.append({'id': 'edt_' + uuid4().hex[:12], 'field': key, 'value': value,
                    'base_value': fields.get(key, ''), 'base_name': p.name, 'base_client': p.client_name or '',
                    'editor': '自动结果跟进', 'at': datetime.now(TZ).isoformat(),
                    'excel': {'sheet': pointer.get('sheet'), 'row': pointer.get('row'), 'column': key, 'status': 'pending'}})
            m['system_edits'] = history
            p.metadata_ = m
            session.commit()
            return True

    def _apply_summary_results(self, writer, cfg):
        import openpyxl
        with self.sessions() as session:
            projects = session.scalars(select(Project).with_for_update()).all()
            jobs = [p for p in projects if ((p.metadata_ or {}).get('blind_summary_result_write') or {}).get('status') == 'pending']
            if not jobs:
                return
            source = workbook_path('*投标项目汇总*.xlsx')
            if not source or writer.gate_lockfile(source) or workbook_is_open(source):
                return
            before = source.stat().st_mtime_ns
            wb = openpyxl.load_workbook(source, rich_text=True)
            changed = False
            successful = []
            for p in jobs:
                m = dict(p.metadata_ or {})
                job = dict(m['blind_summary_result_write'])
                sheet = job.get('sheet', '2026年')
                if sheet not in wb.sheetnames:
                    job.update(status='failed', error='原汇总子表已变化，请核对')
                    m['blind_summary_result_write'] = job; p.metadata_ = m
                    continue
                ws = wb[sheet]
                rownum = job.get('row')
                header = next((r for r in range(1, min((rownum or 8), ws.max_row) + 1) if '是否中标' in [text_value(c.value) for c in ws[r]]), None)
                if not header:
                    job.update(status='failed', error='原表没有是否中标列')
                    m['blind_summary_result_write'] = job; p.metadata_ = m
                    continue
                cols = {text_value(c.value): c.column for c in ws[header] if c.value}
                rows = [rownum] if rownum and normalized(ws.cell(rownum, cols['项目名称']).value) == normalized(job.get('name') or p.name) and normalized(ws.cell(rownum, cols.get('甲方名称', cols['项目名称'])).value) == normalized(job.get('client') or p.client_name) else []
                if len(rows) != 1:
                    job.update(status='failed', error='项目汇总表同名记录无法唯一定位，请核对')
                elif text_value(ws.cell(rows[0], cols['是否中标']).value) not in (job['base'], job['value']):
                    job.update(status='failed', error='项目汇总表是否中标已更新，请核对')
                else:
                    ws.cell(rows[0], cols['是否中标']).value = job['value']
                    if '备注' in cols and job.get('reason'):
                        cell = ws.cell(rows[0], cols['备注'])
                        old = text_value(cell.value)
                        if job['reason'] not in old:
                            cell.value = old + ('\n' if old else '') + job['reason']
                    successful.append((p, sheet, rows[0], cols['是否中标'], job))
                    changed = True
                m['blind_summary_result_write'] = job
                p.metadata_ = m
            if changed:
                if source.stat().st_mtime_ns != before or writer.gate_lockfile(source):
                    return
                writer.gate_backup(source, cfg)
                tmp = source.with_name(source.stem + '.bidos-tmp.xlsx')
                wb.save(tmp)
                from app.services.excel_formula_values import preserve_formula_caches
                preserve_formula_caches(source, tmp)
                if source.stat().st_mtime_ns != before or writer.gate_lockfile(source):
                    tmp.unlink(missing_ok=True)
                    return
                os.replace(tmp, source)
                check = openpyxl.load_workbook(source, read_only=True)
                for p, sheet, row, col, job in successful:
                    if text_value(check[sheet].cell(row, col).value) == job['value']:
                        job['status'] = 'applied'
                        p.metadata_ = {**(p.metadata_ or {}), 'blind_summary_result_write': job}
                check.close()
            wb.close()
            session.commit()

    def save(self, pid, edits, promote=False, own_mark=None):
        self.sync()  # 先读最新正本，再判是否有并发变更。
        with self.sessions() as session:
            p = session.get(Project, pid, with_for_update=True)
            if p is None:
                raise ValueError('项目已过期或不存在，请刷新')
            meta = dict(p.metadata_ or {})
            now = datetime.now(TZ).isoformat()
            if promote:
                if p.workflow_stage != '公告候选' or meta.get('candidate_promotion') or meta.get('dismissed'):
                    raise ValueError('此信息已经处理或过期，请刷新')
                landing = meta.get('landing') or {}
                collected_at = parse_time(landing.get('created_at')) or p.created_at or datetime.now(TZ)
                meta['candidate_promotion'] = {'to_sheet': f'{collected_at.astimezone(TZ).month}月', 'editor': '', 'at': now, 'status': 'pending'}
                from app.services.tender_details import promotion_fields
                detail_fields = promotion_fields(meta.get('tender_detail'))
                meta['candidate_row'] = {**detail_fields, '项目名称': p.name,
                    '甲方名称': detail_fields.get('甲方名称') or p.client_name or '',
                    '项目区域': detail_fields.get('项目区域') or p.region or '',
                    '项目类型': detail_fields.get('项目类型') or p.project_type or '',
                    '招标公告网址': detail_fields.get('招标公告网址') or landing.get('source_url') or '',
                    '招标时间': detail_fields.get('招标时间') or landing.get('published_at') or '',
                    '报名截止时间': detail_fields.get('报名截止时间') or (p.deadline.astimezone(TZ).isoformat() if p.deadline else ''),
                    '报名参与情况': '待评估', '渠道': landing.get('channel_name') or landing.get('source_id') or ''}
                p.workflow_stage = '盲投信息'
            else:
                fields = meta.get('blind_fields') or {}
                pointer = meta.get('blind_pointer') or {}
                if not pointer:
                    raise ValueError('尚未写入Excel，稍后刷新再编辑')
                history = list(meta.get('system_edits') or [])
                if own_mark is not None:
                    ledger = meta.get('blind_own_ledger') or {}
                    if not ledger:
                        raise ValueError('项目汇总表没有唯一对应记录，请先核对项目名称')
                    value = text_value(own_mark.get('value'))
                    if value not in ('', '是', '否', '入库', '暂未公示', '流标', '废标', '重新招采'):
                        raise ValueError('请选择有效的是否中标标注')
                    if text_value(own_mark.get('baseValue')) != ledger.get('是否中标', ''):
                        raise ValueError('项目汇总表是否中标已修改，请刷新后核对')
                    meta['blind_summary_result_write'] = {'status': 'pending', 'base': ledger.get('是否中标', ''),
                        'value': value, 'reason': '', 'row': ledger.get('_row'), 'file': ledger.get('_file'), 'sheet': ledger.get('_sheet', '2026年'),
                        'name': ledger.get('项目名称') or p.name, 'client': ledger.get('甲方名称') or p.client_name or '',
                        'editor': '人工编辑', 'at': now}
                for e in edits:
                    field, value = str(e.get('field', '')).strip(), text_value(e.get('value'))
                    if field in ('BidOS项目ID', 'BidOS回写时间') or field not in fields:
                        raise ValueError('不可修改的字段：' + field)
                    if field in ('报名参与情况', '报名参与情况统计') and value not in STATUSES:
                        raise ValueError('请选择有效的参与状态')
                    if 'baseValue' not in e or text_value(e['baseValue']) != fields.get(field, ''):
                        raise ValueError(f'「{field}」已在Excel更新，请刷新后再修改')
                    for old in history:
                        if old.get('field') == field and (old.get('excel') or {}).get('status') == 'pending':
                            old['status'] = 'superseded'
                            old['excel'] = {**old['excel'], 'status': 'superseded'}
                    history.append({'id': 'edt_' + uuid4().hex[:12], 'field': field, 'value': value, 'base_value': fields.get(field, ''),
                        'base_name': p.name, 'base_client': p.client_name or '', 'editor': '人工编辑', 'at': now,
                        'excel': {'sheet': pointer['sheet'], 'row': pointer['row'], 'column': field, 'status': 'pending'}})
                meta['system_edits'] = history
            p.metadata_ = meta
            session.commit()
        self.sync()
        return self.workspace()

    def workspace(self):
        now = datetime.now(timezone.utc)
        records, candidates = [], []
        with self.sessions() as session:
            projects = session.scalars(select(Project)).all()
            for p in projects:
                m = p.metadata_ or {}
                landing = m.get('landing') or {}
                first = parse_time(landing.get('created_at')) or p.created_at
                if p.workflow_stage == '公告候选' and not m.get('dismissed') and not m.get('candidate_promotion') and first and now < candidate_deadline(first):
                    detail = m.get('tender_detail') or {}
                    fields = detail.get('fields') or {}
                    # 模型整理痕迹必须让人看得见（交接口径：AI 只整理、人复核）。
                    assist = detail.get('aiAssist') or {}
                    ai_filled = assist.get('filled') or []
                    # 「正在补充原公告信息」是废话小字（用户 2026-10-10 明确要求去掉）：
                    # 「部分读取」状态下缺失字段在界面上本来就显示为空，再补一句没人能
                    # 据以行动的说明只占地方。这里只保留两类**能据以行动**的提示：
                    # ① 正文被网站验证墙挡住（采集侧写进 detail['message']，需人工点进原文看）；
                    # ② 模型整理补充了哪几个字段（见下，交接口径：AI 只整理、人复核）。
                    message = detail.get('message') or ''
                    if ai_filled:
                        message = (message + ' ' if message else '') + '模型整理补充：' + '、'.join(ai_filled) + '（均带原文引用，待人工复核）'
                    candidates.append({'id': p.id, 'name': p.name, 'client': fields.get('甲方名称') or p.client_name or '',
                        'agency': fields.get('代理机构') or '', 'region': fields.get('项目区域') or p.region or '',
                        'type': fields.get('项目类型') or p.project_type or '', 'amount': fields.get('招标金额') or '',
                        'budget': float(p.budget) if p.budget is not None else None, 'deadline': p.deadline.astimezone(TZ).isoformat() if p.deadline else '',
                        'url': detail.get('sourceUrl') or landing.get('source_url') or '',
                        'channel': detail.get('sourceName') or landing.get('channel_name') or landing.get('source_id') or '',
                        'publishedAt': landing.get('published_at') or '', 'datePrecision': landing.get('date_precision') or '', 'expiresAt': candidate_deadline(first).isoformat(),
                        'keywords': landing.get('funnel_keywords') or [], 'fields': fields, 'highlights': detail.get('highlights') or [],
                        'detailStatus': detail.get('status') or '待补充', 'detailMessage': message,
                        'aiAssist': assist or None})
                promo = m.get('candidate_promotion') or {}
                if not m.get('blind_present') and (not promo or promo.get('status') == 'applied'):
                    continue
                fields = m.get('blind_fields') or m.get('candidate_row') or {}
                fields = dict(fields)
                # 只有待写值覆盖；应用后重新读Excel。
                for e in m.get('system_edits') or []:
                    if (e.get('status') or (e.get('excel') or {}).get('status')) == 'pending':
                        fields[e['field']] = text_value(e.get('value'))
                status = participation(fields)
                ledger = m.get('blind_own_ledger') or {}
                announcement = m.get('blind_announcement') or {}
                own = ledger.get('是否中标', '')
                winner = fields.get('中标单位') or fields.get('BidOS中标单位') or ledger.get('中标单位') or announcement.get('winner') or ''
                source_amount = fields.get('中标金额（万元）')
                ledger_amount = ledger.get('中标金额（万元）')
                def with_wan_unit(value):
                    return value + ' 万元' if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', value or '') else value
                amount = with_wan_unit(source_amount) if source_amount else fields.get('BidOS中标金额原文') or with_wan_unit(ledger_amount) or ledger.get('中标金额') or announcement.get('amount') or ''
                published = bool(winner or fields.get('BidOS结果公布') == '已公布' or announcement or own in ('是', '否', '入库', '流标', '废标', '重新招采'))
                result = ''
                if status == '报名未参与':
                    result = '未中标' if published else '待公布'
                elif status == '报名参与':
                    if own in ('是', '入库', '中标'):
                        result = '中标'
                    elif own in ('否', '流标', '废标', '重新招采', '未中标'):
                        result = '未中标'
                    elif not published:
                        result = '待公布'
                history = m.get('system_edits') or []
                latest = {}
                for edit in history:
                    latest[edit.get('field')] = edit
                pending = [e for e in latest.values() if (e.get('status') or (e.get('excel') or {}).get('status')) == 'pending']
                failed = [e for e in latest.values() if (e.get('status') or (e.get('excel') or {}).get('status')) == 'failed']
                summary_write = m.get('blind_summary_result_write') or {}
                if summary_write.get('status') == 'failed':
                    failed = failed + [{'error': summary_write.get('error')}]
                if summary_write.get('status') == 'pending':
                    pending = pending + [summary_write]
                sync = '待写入' if pending or promo.get('status') == 'pending' else '需处理' if failed or promo.get('status') == 'failed' else '已同步'
                pointer = m.get('blind_pointer') or {'sheet': promo.get('to_sheet', ''), 'row': ''}
                records.append({'id': p.id, 'name': fields.get('项目名称') or p.name, 'client': fields.get('甲方名称') or p.client_name or '',
                    'region': fields.get('项目区域') or '', 'type': fields.get('项目类型') or '', 'agency': fields.get('代理机构') or '',
                    'deadline': fields.get('报名截止时间') or '', 'month': pointer['sheet'], 'row': pointer.get('row', ''),
                    'status': status, 'result': result, 'published': published, 'winner': winner, 'amount': amount,
                    'ownMark': own, 'ownMarkMissing': status == '报名参与' and published and not result,
                    'ledgerConflict': m.get('blind_ledger_conflict', False),
                    'resultUrl': fields.get('BidOS结果公告网址') or announcement.get('url') or '',
                    'sync': sync, 'syncErrors': [e.get('error') or e.get('excel', {}).get('error') or 'Excel已更新，请核对此字段后重新保存' for e in failed] + ([promo.get('error') or '待评估写入失败，请核对原表'] if promo.get('status') == 'failed' else []),
                    'fields': fields, 'ledger': ledger})
        # 展示直接依据全部相关原表，数据库仅补待写状态/新公告；不能再依赖旧的同名缓存关联。
        from app.services.portfolio_workspace import PortfolioWorkspace
        if not hasattr(self, '_source_projection'):
            self._source_projection = PortfolioWorkspace(self.sessions)
        source_records = self._source_projection.snapshot()['blindRecords']
        cached = {r['id']: r for r in records}
        for r in source_records:
            old = cached.get(r['id'])
            if old:
                r['sync'], r['syncErrors'] = old['sync'], old['syncErrors']
                if old['sync'] == '待写入':
                    for key, value in old['fields'].items():
                        if key in r['fields'] and value != r['fields'][key]:
                            r['fields'][key] = value
        records = source_records + [r for r in records if r['id'] not in {x['id'] for x in source_records} and r['sync'] == '待写入']
        for record in records:
            record.pop('fieldSources', None)
        state = read_state()
        return {'records': records, 'candidates': sorted(candidates, key=lambda x: x['publishedAt'], reverse=True), 'automation': state,
                'rules': {'candidateHours': 24, 'collectionHours': 24, 'resultIntervalMinutes': 10}}


def cli_sync():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings
    service = BlindWorkspace(sessionmaker(create_engine(settings.database_url, pool_pre_ping=True)))
    print(json.dumps(service.sync(), ensure_ascii=False))


if __name__ == '__main__':
    cli_sync()
