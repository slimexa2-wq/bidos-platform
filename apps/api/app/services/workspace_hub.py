"""Project union plus an Excel-owned work log; historical source sheets stay intact.

New work is written only to the plan workbook's fixed work-log sheet. JSON keeps
recovery history/cache. The channel/credential workbook is never opened here.
"""
from __future__ import annotations
from app.services.file_lock import file_lock

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
import posixpath
from lxml import etree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import openpyxl

from app.services.blind_workspace import ROOT, TZ, normalized, text_value, workbook_path, workbook_is_open
from app.services.portfolio_workspace import clean, field, fiscal_year, iso, read_tables, source_key, source_record
from app.services.excel_formula_values import preserve_formula_caches


WORK_SHEET = '系统工作记录'
WORK_COLUMNS = {'name': '项目名称', 'owner': '负责人', 'nextAction': '下一步', 'dueAt': '跟进日期',
                'notes': '工作备注', 'decision': '评估意见', 'materialsText': '材料清单',
                'documentText': '标书正文', 'status': '处理状态', 'updatedAt': '更新日期',
                'projectId': '系统项目ID', 'version': '系统版本', 'materialsJson': '材料结构',
                'attachments': '资料附件'}
WORK_VALUE_KEYS = ('notes', 'decision', 'materials', 'documentText', 'attachments',
                   'owner', 'nextAction', 'dueAt', 'status', 'updatedAt', 'version')


class WorkConflict(ValueError):
    pass


def stable_id(prefix, *parts):
    return prefix + hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:24]


def blank_work():
    return {'version': 0, 'notes': '', 'decision': '', 'materials': [],
            'documentText': '', 'attachments': [], 'updatedAt': ''}


def work_digest(work):
    return hashlib.sha256(json.dumps({k: work.get(k, '' if k not in ('materials', 'attachments') else [])
                                     for k in WORK_VALUE_KEYS}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def materials_text(materials):
    return '\n'.join(('[✓] ' if m.get('done') else '[ ] ') + m.get('label', '') for m in materials)


def read_materials(project_id, visible, structure):
    try:
        stored = json.loads(structure or '[]')
        if not isinstance(stored, list) or not all(isinstance(m, dict) for m in stored): stored = []
    except (ValueError, TypeError):
        stored = []
    if materials_text(stored).strip() == visible.strip(): return stored
    previous = {str(m.get('label', '')).strip(): m for m in stored}
    items = []
    for index, raw in enumerate(visible.splitlines()):
        raw = raw.strip()
        if not raw: continue
        done = bool(re.match(r'^(?:\[(?:✓|√|x|X)\]|☑|✓|√)', raw))
        label = re.sub(r'^(?:\[(?:✓|√|x|X|\s*)\]|☑|☐|✓|√)\s*', '', raw).strip()
        if not label: continue
        old = previous.get(label, {})
        items.append({'id': old.get('id') or stable_id('material_', project_id, label, index),
                      'label': label, 'done': done})
    return items


def patch_work_sheet_package(original, target, ws, changed_cells):
    """Replace only work-log cells, preserving every historical XLSX part bytewise.

Saving the full real plan workbook through openpyxl changes embedded media. The
workbook is therefore only an in-memory cell model; its serialization is never
used. Existing work-sheet formatting/formulas outside edited cells also survive.
    """
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    doc_rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    content_ns = 'http://schemas.openxmlformats.org/package/2006/content-types'
    def tag(name): return '{' + ns + '}' + name
    def encode(root): return ET.tostring(root, encoding='utf-8', xml_declaration=True)
    replacements = {}
    with zipfile.ZipFile(original) as source:
        names = set(source.namelist())
        workbook = ET.fromstring(source.read('xl/workbook.xml'))
        relations = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
        sheets = workbook.find(tag('sheets'))
        existing = next((s for s in sheets if s.get('name') == WORK_SHEET), None)
        if existing is not None:
            relationship_id = existing.get('{' + doc_rel + '}id')
            relationship = next((r for r in relations if r.get('Id') == relationship_id), None)
            if relationship is None: raise RuntimeError('工作记录工作表关系缺失，未修改正本')
            rel_target = relationship.get('Target', '')
            sheet_path = rel_target.lstrip('/') if rel_target.startswith('/') else posixpath.normpath('xl/' + rel_target)
            document = ET.fromstring(source.read(sheet_path))
        else:
            number = max([int(s.get('sheetId', '0')) for s in sheets] or [0]) + 1
            part_number = number
            while f'xl/worksheets/sheet{part_number}.xml' in names: part_number += 1
            sheet_path = f'xl/worksheets/sheet{part_number}.xml'
            used_ids = {r.get('Id') for r in relations}
            next_id = 1
            while f'rId{next_id}' in used_ids: next_id += 1
            relationship_id = f'rId{next_id}'
            ET.SubElement(sheets, tag('sheet'), {'name': WORK_SHEET, 'sheetId': str(number), '{' + doc_rel + '}id': relationship_id})
            ET.SubElement(relations, '{' + rel_ns + '}Relationship', {'Id': relationship_id,
                          'Type': doc_rel + '/worksheet', 'Target': f'worksheets/sheet{part_number}.xml'})
            content_types = ET.fromstring(source.read('[Content_Types].xml'))
            ET.SubElement(content_types, '{' + content_ns + '}Override', {'PartName': '/' + sheet_path,
                          'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})
            replacements['xl/workbook.xml'] = encode(workbook)
            replacements['xl/_rels/workbook.xml.rels'] = encode(relations)
            replacements['[Content_Types].xml'] = encode(content_types)
            document = ET.Element(tag('worksheet'))
            ET.SubElement(document, tag('dimension'), {'ref': f'A1:N{max(2, ws.max_row)}'})
            views = ET.SubElement(document, tag('sheetViews'))
            view = ET.SubElement(views, tag('sheetView'), {'workbookViewId': '0', 'showGridLines': '0'})
            ET.SubElement(view, tag('pane'), {'xSplit': '1', 'ySplit': '1', 'topLeftCell': 'B2', 'state': 'frozen', 'activePane': 'bottomRight'})
            ET.SubElement(document, tag('sheetFormatPr'), {'defaultRowHeight': '18'})
            columns = ET.SubElement(document, tag('cols'))
            for col in range(1, len(WORK_COLUMNS) + 1):
                attributes = {'min': str(col), 'max': str(col), 'width': '48' if col == 1 else '28', 'customWidth': '1'}
                if col > 10: attributes['hidden'] = '1'
                ET.SubElement(columns, tag('col'), attributes)
            ET.SubElement(document, tag('sheetData'))
            changed_cells = set(changed_cells) | {c.coordinate for c in ws[1]}
        sheet_data = document.find(tag('sheetData'))
        if sheet_data is None: raise RuntimeError('工作记录工作表数据区缺失，未修改正本')
        for coordinate in sorted(changed_cells, key=lambda c: (ws[c].row, ws[c].column)):
            cell = ws[coordinate]
            row = next((r for r in sheet_data if r.get('r') == str(cell.row)), None)
            if row is None:
                row = ET.Element(tag('row'), {'r': str(cell.row)})
                position = next((i for i, r in enumerate(sheet_data) if int(r.get('r', '0')) > cell.row), len(sheet_data))
                sheet_data.insert(position, row)
            node = next((c for c in row if c.get('r') == coordinate), None)
            if node is None:
                node = ET.Element(tag('c'), {'r': coordinate})
                position = next((i for i, c in enumerate(row) if ws[c.get('r')].column > cell.column), len(row))
                row.insert(position, node)
            for child in list(node): node.remove(child)
            value = cell.value
            if value is None:
                node.attrib.pop('t', None)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                node.set('t', 'n'); ET.SubElement(node, tag('v')).text = str(value)
            else:
                # Inline text avoids changing sharedStrings or letting '=' text
                # become a formula. Dates are sortable ISO text in this work log.
                node.set('t', 'inlineStr')
                inline = ET.SubElement(node, tag('is'))
                text = ET.SubElement(inline, tag('t'), {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
                text.text = text_value(value)
        dimension = document.find(tag('dimension'))
        if dimension is not None: dimension.set('ref', f'A1:N{max(2, ws.max_row)}')
        auto_filter = document.find(tag('autoFilter'))
        if auto_filter is None:
            auto_filter = ET.Element(tag('autoFilter'))
            document.insert(list(document).index(sheet_data) + 1, auto_filter)
        auto_filter.set('ref', f'A1:J{max(2, ws.max_row)}')
        replacements[sheet_path] = encode(document)
        with zipfile.ZipFile(target, 'w') as destination:
            for info in source.infolist():
                destination.writestr(info, replacements.pop(info.filename, source.read(info.filename)))
            for name, data in replacements.items():
                destination.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


def date_distance(a, b):
    if not a or not b:
        return None
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def attach_sources(project, rows):
    known = {source_key(r['source']) for r in project['sourceRows']}
    for row in rows:
        key = source_key(row['source'])
        if key not in known:
            project['sourceRows'].append(copy.deepcopy(row))
            known.add(key)
        for k, v in row.get('fields', {}).items():
            if v and not project['fields'].get(k):
                project['fields'][k] = v
    project['sources'] = [row['source'] for row in project['sourceRows']]


class WorkspaceHub:
    def __init__(self, portfolio, work_path=None, plan_path=None):
        self.portfolio = portfolio
        self.work_path = Path(work_path) if work_path else ROOT / 'data/workspace_hub_work.json'
        # A custom JSON path alone retains the original disposable test mode.
        self.excel_mode = plan_path is not None or work_path is None
        self.plan_path = Path(plan_path) if plan_path else None
        self.sync_lock = self.plan_path.parent / '.workspace_sync.lock' if self.plan_path else Path('/tmp/bidos_nas_sync.lock')
        self._guard = threading.RLock()
        self._cache = None
        self._stamp = None

    def _read_cache(self):
        if not self.work_path.exists():
            return {}
        try:
            body = json.loads(self.work_path.read_text())
            if not isinstance(body, dict) or not isinstance(body.get('projects'), dict):
                raise ValueError('invalid work store')
            return body['projects']
        except (OSError, ValueError) as exc:
            raise RuntimeError('工作记录文件读取失败，已停止保存以保护现有记录') from exc

    def _workbook_target(self):
        path = self.plan_path or workbook_path('*投标项目计划*.xlsx')
        if not path or not path.is_file(): raise RuntimeError('未找到投标项目计划正本，工作记录尚未同步')
        return path

    def _read_excel_work(self, target):
        wb = openpyxl.load_workbook(target, read_only=True, data_only=False)
        try:
            if WORK_SHEET not in wb.sheetnames: return None
            ws = wb[WORK_SHEET]
            headers = {text_value(cell.value): cell.column for cell in next(ws.iter_rows(min_row=1, max_row=1)) if cell.value}
            missing = set(WORK_COLUMNS.values()) - set(headers)
            if missing: raise WorkConflict('系统工作记录缺少表头：' + '、'.join(sorted(missing)))
            records = {}
            for row in ws.iter_rows(min_row=2):
                values = {key: text_value(row[headers[label] - 1].value) for key, label in WORK_COLUMNS.items()}
                pid = values['projectId']
                if not pid:
                    if any(values.values()): raise WorkConflict('系统工作记录存在缺少项目ID的行，请保留隐藏ID后再同步')
                    continue
                if pid in records: raise WorkConflict('系统工作记录出现重复项目ID，请核对复制的行')
                try: version = int(values['version'] or '0')
                except ValueError as exc: raise WorkConflict('系统工作记录的版本号无效') from exc
                try: attachments = json.loads(values['attachments'] or '[]')
                except ValueError: attachments = []
                work = {**blank_work(), **{k: values[k] for k in ('notes', 'decision', 'documentText', 'owner', 'nextAction', 'status', 'updatedAt')},
                        'dueAt': iso(values['dueAt']) or values['dueAt'], 'version': version,
                        'materials': read_materials(pid, values['materialsText'], values['materialsJson']),
                        'attachments': attachments if isinstance(attachments, list) else []}
                work['digest'] = work_digest(work)
                work['sync'] = '已同步到Excel'
                work['source'] = {'file': target.name, 'sheet': WORK_SHEET, 'row': row[0].row,
                                  'fingerprint': work['digest'], 'headerRow': 1,
                                  'columns': headers, 'width': len(headers)}
                work['sourceFields'] = {label: values[k] for k, label in WORK_COLUMNS.items()
                                        if k not in ('projectId', 'version', 'materialsJson', 'attachments')}
                records[pid] = work
            return records
        finally:
            wb.close()

    def _read_work(self):
        if self.excel_mode:
            records = self._read_excel_work(self._workbook_target())
            if records is not None: return records
        # Before the first sheet creation, existing local notes remain visible and
        # explicitly unsynced, so upgrading never silently discards earlier work.
        records = self._read_cache()
        return {pid: {**record, 'digest': work_digest(record), 'sync': '仅本地记录，尚未写入Excel' if self.excel_mode else '本地工作记录'}
                for pid, record in records.items()}

    def _plans(self):
        path = workbook_path('*投标项目计划*.xlsx')
        if not path:
            return [], [], ['未找到投标项目计划工作簿，计划信息暂不可用']
        rows = read_tables(path, lambda name: True, include_comments=True)
        # Actual Excel dates in this workbook establish the fiscal-year context.
        # Partial dates retain their original text and an explicit inference basis.
        years = Counter(fiscal_year(d) for row in rows
                        if (d := iso(field(row['fields'], '计划招采时间', '开标时间'))))
        context_year = years.most_common(1)[0][0] if years else ''
        output = []
        for row in rows:
            f, src = row['fields'], copy.deepcopy(row['source'])
            if not ('周' in src['sheet'] or '中标项目明细' in src['sheet']):
                continue
            planned_raw = field(f, '计划招采时间', '开标时间')
            planned_at = iso(planned_raw)
            basis = '原表完整日期' if planned_at else ''
            if not planned_at and context_year:
                match = re.search(r'(\d{1,2})月(\d{1,2})[日号]?', planned_raw)
                if match:
                    month, day = map(int, match.groups())
                    try:
                        planned_at = date(int(context_year) - (month == 12), month, day).isoformat()
                        basis = f'原文月日；年份按本工作簿真实日期对应{context_year}财年'
                    except ValueError:
                        pass
            sheet_month = re.match(r'(\d{1,2})月', src['sheet'])
            period = ''
            if sheet_month and context_year:
                month = int(sheet_month.group(1))
                period = f'{int(context_year) - (month == 12):04d}-{month:02d}'
            anchor = stable_id('plan_source_', src['file'], src['sheet'], normalized(f.get('项目名称')),
                               normalized(f.get('甲方名称')), planned_raw)
            src['anchorId'] = anchor
            item = {'id': anchor, 'projectId': '', 'name': f.get('项目名称', ''),
                    'client': f.get('甲方名称', ''), 'period': period, 'week': src['sheet'],
                    'year': fiscal_year(planned_at, context_year), 'plannedAt': planned_at,
                    'plannedAtRaw': planned_raw, 'plannedAtBasis': basis,
                    'owner': field(f, '项目负责人', '标书负责情况'),
                    'progress': field(f, '目前进度', '实际进度'), 'plan': f.get('计划', ''),
                    'priority': f.get('优先级', ''), 'kind': 'result' if '中标项目明细' in src['sheet'] else 'plan',
                    'fields': f.copy(), 'source': src, 'comments': row.get('comments', {})}
            output.append(item)
        # Preserve non-tabular goals, quotations and watch lists, including cell
        # positions. These are source documents, not invented project records.
        documents = []
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb:
                if ws.title == WORK_SHEET: continue
                if '周' in ws.title and '复盘' not in ws.title or '中标项目明细' in ws.title:
                    continue
                cells = [{'cell': cell.coordinate, 'value': text_value(cell.value)}
                         for row in ws.iter_rows() for cell in row if cell.value is not None]
                documents.append({'id': stable_id('plan_document_', path.name, ws.title),
                                  'title': ws.title, 'source': {'file': path.name, 'sheet': ws.title, 'row': 1,
                                      'fingerprint': hashlib.sha256(json.dumps(cells, ensure_ascii=False, sort_keys=True).encode()).hexdigest()},
                                  'kind': '复盘资料' if '复盘' in ws.title else '目标进度' if '目标' in ws.title else '关注清单',
                                  'cells': cells, 'empty': not cells})
        finally:
            wb.close()
        return output, documents, []

    @staticmethod
    def _base_project(row, result=False):
        fields = copy.deepcopy(row.get('sourceFields') if result else row.get('fields')) or {}
        sources = copy.deepcopy(row.get('sourceRows') or [{'source': row['source'], 'fields': fields}])
        project = {'id': row['id'], 'name': row.get('name', ''), 'client': row.get('client', ''),
                   'channel': row.get('channel') or field(fields, '项目情况', '项目渠道')
                              or ('盲投' if '盲投' in row['source']['file'] else ''),
                   'partStatus': '报名参与' if result else row.get('status', ''),
                   'resultStatus': row.get('result', ''), 'deadline': row.get('deadline') or iso(fields.get('报名截止时间')),
                   'openingAt': row.get('date', ''), 'year': row.get('year', ''),
                   'owner': field(fields, '项目负责人', 'BidOS跟进人', '跟进人', '标书负责情况'),
                   'progress': field(fields, '目前进度', '实际进度'), 'plan': fields.get('计划', ''),
                   'priority': fields.get('优先级', ''), 'limit': row.get('limit', ''),
                   'url': row.get('url') or field(fields, '招标公告网址', '公告链接'),
                   'winner': row.get('winner', ''), 'winningPrice': row.get('amount', ''),
                   'amount': row.get('amount', ''), 'ownMark': row.get('ownMark', ''),
                   'publishedAt': row.get('publishedAt', ''), 'resultUrl': row.get('resultUrl', ''),
                   'business': row.get('business') or row.get('type') or field(fields, '业务类型', '项目类型'),
                   'region': row.get('region') or field(fields, '项目区域', '项目地点'),
                   'agency': row.get('agency') or fields.get('代理机构', ''),
                   'discountRate': field(fields, '折扣率', '报价折扣率'),
                   'blindId': '' if result else row['id'],
                   'blindPath': '' if result else '/blind?tab=%E5%8F%B0%E8%B4%A6&select=' + quote(row['id']),
                   'source': row['source'], 'sources': [], 'sourceRows': [], 'fields': fields,
                   'planHistory': [], 'hasPlan': False, 'work': blank_work()}
        attach_sources(project, sources)
        return project

    def _assemble(self):
        data = self.portfolio.snapshot()
        projects, by_source = [], {}
        for row in data.get('results', []):
            project = self._base_project(row, True)
            projects.append(project)
            for source in project['sources']:
                by_source[source_key(source)] = project
        for row in data.get('blindRecords', []):
            matches = {by_source[source_key(s)]['id']: by_source[source_key(s)]
                       for s in row.get('sources', [row['source']]) if source_key(s) in by_source}
            project = next(iter(matches.values())) if len(matches) == 1 else None
            if project:
                project['blindId'] = row['id']
                project['blindPath'] = '/blind?tab=%E5%8F%B0%E8%B4%A6&select=' + quote(row['id'])
                attach_sources(project, row.get('sourceRows', []))
                for attr, value in [('deadline', row.get('deadline')), ('limit', row.get('limit')),
                                    ('url', field(row.get('fields', {}), '招标公告网址'))]:
                    if value and not project[attr]: project[attr] = value
            else:
                project = self._base_project(row)
                projects.append(project)
            for source in project['sources']:
                by_source[source_key(source)] = project
        # Non-blind projects that people declined still belong in the department's
        # directory. They remain excluded from actual participation/win statistics.
        excluded_keys = {source_key(r['source']) for r in data.get('reconciliation', {}).get('excludedResults', [])}
        for row in data.get('reconciliation', {}).get('readRows', []):
            if source_key(row['source']) in by_source:
                continue
            f = row['fields']
            if source_key(row['source']) not in excluded_keys and '未参与' not in row['source']['sheet']:
                continue
            name, client = field(f, '项目名称'), field(f, '甲方名称')
            if not name: continue
            dt = iso(f.get('开标时间'))
            matches = [p for p in projects if normalized(p['name']) == normalized(name)
                       and client and normalized(p['client']) == normalized(client)
                       and dt and p['openingAt'] == dt]
            if len(matches) == 1:
                project = matches[0]
                attach_sources(project, [row])
            else:
                row_data = {'id': stable_id('declined_project_', normalized(name), normalized(client), dt or source_key(row['source'])),
                            'name': name, 'client': client, 'fields': f, 'source': row['source'],
                            'sourceRows': [row], 'status': '报名未参与', 'date': dt,
                            'year': fiscal_year(dt), 'channel': field(f, '项目情况'),
                            'result': '未中标' if field(f, '中标单位', '中标价格') else '',
                            'winner': field(f, '中标单位'), 'amount': field(f, '中标价格'),
                            'limit': field(f, '限价')}
                project = self._base_project(row_data)
                projects.append(project)
            for source in project['sources']:
                by_source[source_key(source)] = project
        history, documents, notes = self._plans()
        supplemental_rows = []
        competitor_path = workbook_path('*主要竞争*.xlsx')
        if competitor_path:
            supplemental_rows = read_tables(competitor_path, lambda s: '分析' in s or '汇总' in s, include_comments=True)
            wb = openpyxl.load_workbook(competitor_path, read_only=True, data_only=True)
            try:
                for ws in wb:
                    if '分析' not in ws.title and '汇总' not in ws.title: continue
                    cells = [{'cell': cell.coordinate, 'value': text_value(cell.value)}
                             for row in ws.iter_rows() for cell in row if cell.value is not None]
                    documents.append({'id': stable_id('source_document_', competitor_path.name, ws.title),
                                      'title': ws.title, 'kind': '竞对分析资料', 'empty': not cells, 'cells': cells,
                                      'source': {'file': competitor_path.name, 'sheet': ws.title, 'row': 1,
                                          'fingerprint': hashlib.sha256(json.dumps(cells, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}})
            finally:
                wb.close()
        index = defaultdict(list)
        for p in projects:
            index[(normalized(p['name']), normalized(p['client']))].append(p)
        for h in sorted(history, key=lambda r: (r['plannedAt'] or r['period'], r['week'], r['source']['row'])):
            key = normalized(h['name']), normalized(h['client'])
            candidates = index[key]
            # A matching name alone never joins different procurement years.
            dated = [p for p in candidates if h['plannedAt'] and p.get('openingAt') == h['plannedAt']]
            contextual = [p for p in candidates if h['year'] and p.get('year') == h['year']
                          and h['client'] and (not (p.get('openingAt') or p.get('plannedAt')) or not h['plannedAt']
                          or (date_distance(p.get('openingAt') or p.get('plannedAt'), h['plannedAt']) or 0) <= 45)]
            choices = dated if dated else contextual
            project = choices[0] if len(choices) == 1 else None
            if project is None:
                anchor_date = h['plannedAt'] or h['period'] or h['week']
                pid = stable_id('work_project_', *key, h['year'], anchor_date)
                project = next((p for p in candidates if p['id'] == pid), None)
                if project is None:
                    f = h['fields']
                    project = {'id': pid, 'name': h['name'], 'client': h['client'],
                               'channel': field(f, '项目情况', '项目类型'), 'partStatus': '', 'resultStatus': '',
                               'deadline': '', 'openingAt': iso(f.get('开标时间')), 'year': h['year'],
                               'plannedAt': h['plannedAt'],
                               'owner': '', 'progress': '', 'plan': '', 'priority': '',
                               'limit': (f['限价（万元）'] + '万元') if f.get('限价（万元）') and re.fullmatch(r'\d+(?:\.\d+)?', f['限价（万元）']) else field(f, '限价（万元）', '限价'),
                               'requirementsRaw': field(f, '人数需求，限价', '标的（中标金额）\n人数需求'),
                               'url': field(f, '招标公告网址', '公告链接'), 'winner': field(f, '中标单位'),
                               'winningPrice': field(f, '中标金额（万元）'), 'source': h['source'],
                               'amount': field(f, '中标金额（万元）'), 'ownMark': '', 'publishedAt': '',
                               'resultUrl': '', 'business': field(f, '业务类型'), 'region': field(f, '项目地址'),
                               'agency': field(f, '代理机构'), 'discountRate': field(f, '折扣率', '报价折扣率'),
                               'blindId': '', 'blindPath': '',
                               'sources': [], 'sourceRows': [], 'fields': {}, 'planHistory': [],
                               'hasPlan': False, 'work': blank_work()}
                    projects.append(project)
                    index[key].append(project)
                if len(choices) > 1:
                    h['matchNote'] = '存在多次同名招采，保留为独立计划记录；未借用其他轮次结果。'
            h['projectId'] = project['id']
            project['planHistory'].append(h)
            project['hasPlan'] = project['hasPlan'] or h['kind'] == 'plan'
            attach_sources(project, [{'source': h['source'], 'fields': h['fields'], 'comments': h['comments']}])
        for project in projects:
            weekly = [h for h in project['planHistory'] if h['kind'] == 'plan']
            # Sheet period is the update period; a delayed procurement date is not
            # evidence that an older weekly update is newer than a later week.
            def week_order(h):
                number = re.search(r'第([一二三四五六七八九十\d]+)周', h['week'])
                raw = number.group(1) if number else ''
                ordinal = int(raw) if raw.isdigit() else {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5}.get(raw, 0)
                return h['period'], ordinal, h['source']['row']
            weekly.sort(key=week_order, reverse=True)
            project['planHistory'].sort(key=week_order, reverse=True)
            if weekly:
                for attr in ('owner', 'progress', 'plan', 'priority'):
                    project[attr] = next((h[attr] for h in weekly if h[attr]), project[attr])
                project['plannedAt'] = weekly[0]['plannedAt']
                project['plannedAtRaw'] = weekly[0]['plannedAtRaw']
            past = [p for p in [project.get('openingAt'), project.get('deadline'), project.get('plannedAt')] if p]
            project['isHistorical'] = (project['resultStatus'] in ('中标', '未中标')
                                       or project['partStatus'] in ('未报名', '报名未参与')
                                       or bool(past and max(past) < (datetime.now(TZ).date() - timedelta(days=30)).isoformat()))
        return {'projects': projects, 'planHistory': history, 'sourceDocuments': documents,
                'notes': notes, 'portfolio': data, 'supplementalRows': supplemental_rows}

    def invalidate(self):
        with self._guard:
            self._cache = None

    def snapshot(self):
        with self._guard:
            paths = [workbook_path(pattern) for pattern in ('*投标项目计划*.xlsx', '*投标项目汇总*.xlsx',
                                                           '*主要竞争*.xlsx', '*盲投项目信息*汇总表*.xlsx')]
            stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) if p else None for p in paths)
            now = datetime.now(TZ)
            if not self._cache or stamp != self._stamp or (now - self._cache[0]).total_seconds() > 30:
                result = self._assemble()
                self._cache, self._stamp = (datetime.now(TZ), result), stamp
            assembled = copy.deepcopy(self._cache[1])
        work = self._read_work()
        tasks = []
        today = now.date().isoformat()
        for project in assembled['projects']:
            project['work'] = {**blank_work(), **{k: v for k, v in work.get(project['id'], {}).items() if k != '_history'}}
            project['work'].setdefault('digest', work_digest(project['work']))
            project['work'].setdefault('sync', '尚无工作记录')
            if project['work'].get('source'):
                attach_sources(project, [{'source': project['work']['source'], 'fields': project['work']['sourceFields']}])
            current_work = project['work']
            for project_key, work_key in (('owner', 'owner'), ('progress', 'status'), ('plan', 'nextAction')):
                if current_work.get(work_key): project[project_key] = current_work[work_key]
            completed_work = current_work.get('status') in ('完成', '已完成', '已结束')
            if completed_work:
                project['isHistorical'] = True
            if current_work.get('nextAction') and not completed_work:
                tasks.append({'id': 'work_' + project['id'], 'projectId': project['id'], 'kind': 'work',
                              'title': project['name'], 'detail': current_work['nextAction'],
                              'at': current_work.get('dueAt', ''), 'severity': 'warning',
                              'path': '/registration?select=' + quote(project['id'])})
            if completed_work or project['resultStatus'] in ('中标', '未中标') or project['partStatus'] == '未报名':
                continue
            for kind, at, label, page in [('deadline', project['deadline'], '报名截止', 'registration'),
                                         ('opening', project['openingAt'], '开标', 'document')]:
                # No reminders from inferred dates or long-completed old sheets.
                if not at or at < today or at > (now.date() + timedelta(days=14)).isoformat():
                    continue
                if project['partStatus'] == '报名未参与': continue
                tasks.append({'id': kind + '_' + project['id'], 'projectId': project['id'], 'kind': kind,
                              'title': project['name'], 'detail': label + ' · ' + at,
                              'at': at, 'severity': 'critical' if at <= (now.date() + timedelta(days=2)).isoformat() else 'warning',
                              'path': '/' + page + '?select=' + quote(project['id'])})
        for reminder in assembled['portfolio'].get('reminders', []):
            tasks.append({**reminder, 'projectId': reminder.get('projectId', '')})
        tasks.sort(key=lambda r: (r.get('severity') != 'critical', r.get('at') or '9999'))
        return {'updatedAt': now.isoformat(), 'projects': assembled['projects'], 'tasks': tasks,
                'planHistory': assembled['planHistory'], 'sourceDocuments': assembled['sourceDocuments'],
                'supplementalSourceRows': assembled['supplementalRows'],
                'notes': assembled['notes'], 'sourcePolicy': '项目资料读取原表；工作记录同步到计划正本的“系统工作记录”子表，历史周计划保持原貌。',
                'planStats': {'weeklyRows': sum(h['kind'] == 'plan' for h in assembled['planHistory']),
                              'resultRows': sum(h['kind'] == 'result' for h in assembled['planHistory']),
                              'sourceDocuments': sum('投标项目计划' in d['source']['file'] for d in assembled['sourceDocuments'])}}

    def get_work(self, project_id):
        if not any(p['id'] == project_id for p in self.snapshot()['projects']):
            raise KeyError('项目不存在或来源已变更，请刷新')
        work = {**blank_work(), **{k: v for k, v in self._read_work().get(project_id, {}).items() if k != '_history'}}
        work.setdefault('digest', work_digest(work))
        work.setdefault('sync', '尚无工作记录')
        return work

    def _store_cache(self, project_id, updated):
        self.work_path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(str(self.work_path) + '.lock'):
            records = self._read_cache()
            previous = records.get(project_id, {})
            history = previous.pop('_history', [])
            records[project_id] = {**updated, '_history': (history + [previous])[-30:]}
            fd, name = tempfile.mkstemp(prefix='.workspace-work-', suffix='.json', dir=self.work_path.parent)
            try:
                with os.fdopen(fd, 'w') as out:
                    json.dump({'projects': records}, out, ensure_ascii=False, indent=2)
                    out.flush(); os.fsync(out.fileno())
                os.replace(name, self.work_path)
            finally:
                Path(name).unlink(missing_ok=True)

    def _save_excel_work(self, project_id, body):
        target = self._workbook_target()
        if not isinstance(body.get('baseDigest'), str):
            raise ValueError('缺少上次读取的内容摘要，请刷新后保存')
        with file_lock(self.sync_lock, blocking=False) as _lk:
            if not _lk.acquired: raise WorkConflict('Excel正在同步，请稍后保存；本次尚未写入')
            if (target.parent / ('~$' + target.name)).exists() or workbook_is_open(target):
                raise WorkConflict('计划Excel正在打开，请关闭文件后再保存；本次尚未同步')
            stamp = target.stat().st_mtime_ns, target.stat().st_size
            records = self._read_excel_work(target)
            if records is None: records = self._read_cache()
            old = {**blank_work(), **records.get(project_id, {})}
            if body['baseVersion'] != old['version'] or body['baseDigest'] != work_digest(old):
                raise WorkConflict('Excel或系统工作记录已被更新，请刷新后再保存')
            changes = {k: v for k, v in body.items() if k not in ('baseVersion', 'baseDigest')}
            updated = {**old, **changes, 'version': old['version'] + 1,
                       'updatedAt': datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
            project = next(p for p in self.snapshot()['projects'] if p['id'] == project_id)
            wb = openpyxl.load_workbook(target, rich_text=True)
            fd, filename = tempfile.mkstemp(prefix='.' + target.stem + '.work-', suffix='.xlsx', dir=target.parent)
            os.close(fd)
            tmp = Path(filename)
            try:
                if WORK_SHEET not in wb.sheetnames:
                    from openpyxl.styles import Font, PatternFill, Alignment
                    from openpyxl.utils import get_column_letter
                    ws = wb.create_sheet(WORK_SHEET)
                    for col, label in enumerate(WORK_COLUMNS.values(), 1):
                        cell = ws.cell(1, col, label)
                        cell.font = Font(name='微软雅黑', bold=True, color='FFFFFF')
                        cell.fill = PatternFill('solid', fgColor='244A72')
                        cell.alignment = Alignment(vertical='center')
                        ws.column_dimensions[get_column_letter(col)].width = 24 if col != 1 else 48
                        if col > 10: ws.column_dimensions[get_column_letter(col)].hidden = True
                    ws.freeze_panes = 'B2'
                    ws.sheet_view.showGridLines = False
                else: ws = wb[WORK_SHEET]
                headers = {text_value(c.value): c.column for c in ws[1] if c.value}
                id_col = headers[WORK_COLUMNS['projectId']]
                matches = [r for r in range(2, ws.max_row + 1) if text_value(ws.cell(r, id_col).value) == project_id]
                if len(matches) > 1: raise WorkConflict('系统工作记录存在重复项目ID，未写入')
                row = matches[0] if matches else max(2, ws.max_row + 1)
                values = {'projectId': project_id, 'version': updated['version'], 'updatedAt': updated['updatedAt']}
                # When creating the first row, carry any pre-upgrade local notes
                # into Excel so partial edits do not discard previously saved work.
                actual_changes = changes if matches else {k: updated.get(k, '' if k not in ('materials', 'attachments') else [])
                                                         for k in WORK_VALUE_KEYS if k not in ('version', 'updatedAt')}
                if not matches: values['name'] = project['name']
                for key, value in actual_changes.items():
                    if key == 'materials':
                        values['materialsText'] = materials_text(value)
                        values['materialsJson'] = json.dumps(value, ensure_ascii=False)
                    elif key == 'attachments': values[key] = json.dumps(value, ensure_ascii=False)
                    else: values[key] = value
                changed_cells = set()
                for key, value in values.items():
                    col = headers[WORK_COLUMNS[key]]
                    cell = ws.cell(row, col)
                    if cell.data_type == 'f': raise WorkConflict('工作记录的“' + WORK_COLUMNS[key] + '”为Excel公式，请在Excel修改')
                    if isinstance(value, str) and len(value) > 32767:
                        raise ValueError(WORK_COLUMNS[key] + '超过Excel单元格32767字限制，请保留需处理的节选或拆分清单')
                    if key == 'dueAt' and value:
                        cell.value = datetime.fromisoformat(iso(value))
                        cell.number_format = 'yyyy-mm-dd'
                    elif key == 'updatedAt':
                        cell.value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                        cell.number_format = 'yyyy-mm-dd hh:mm:ss'
                    else: cell.value = value if value != '' else None
                    from openpyxl.styles import Alignment
                    cell.alignment = Alignment(vertical='top', wrap_text=True)
                    changed_cells.add(cell.coordinate)
                ws.auto_filter.ref = f'A1:J{max(2, ws.max_row)}'
                patch_work_sheet_package(target, tmp, ws, changed_cells)
                verified = self._read_excel_work(tmp).get(project_id)
                if not verified or verified['version'] != updated['version']:
                    raise RuntimeError('Excel写入读回核验失败，原文件未替换')
                for key in actual_changes:
                    expected = updated.get(key, '')
                    actual = verified.get(key, '')
                    if key == 'dueAt': expected = iso(expected) or expected
                    if actual != expected: raise RuntimeError('Excel字段读回不一致：' + key + '；原文件未替换')
                if stamp != (target.stat().st_mtime_ns, target.stat().st_size) or (target.parent / ('~$' + target.name)).exists() or workbook_is_open(target):
                    raise WorkConflict('保存期间计划Excel发生变化或被打开，原文件已保留，请刷新')
                backups = self.work_path.parent / 'workspace_excel_backups'
                backups.mkdir(parents=True, exist_ok=True)
                backup = backups / (target.stem + '.' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f') + '.xlsx')
                shutil.copy2(target, backup)
                os.replace(tmp, target)
                verified['source']['file'] = target.name
                verified['sync'] = '已同步到Excel'
                self._cache = None
                try: self._store_cache(project_id, verified)
                except (OSError, RuntimeError):
                    # Excel is already authoritative and confirmed. A cache error
                    # must not claim that the business save failed or was rolled back.
                    verified['cacheWarning'] = 'Excel已保存，本地历史缓存暂未更新'
                return verified
            finally:
                wb.close()
                tmp.unlink(missing_ok=True)

    def save_work(self, project_id, body):
        if not isinstance(body, dict): raise ValueError('工作记录必须为对象')
        allowed = {'baseVersion', 'baseDigest', 'notes', 'decision', 'materials', 'documentText', 'attachments',
                   'owner', 'nextAction', 'dueAt', 'status'}
        if set(body) - allowed: raise ValueError('含有不支持的字段；源表业务字段需在原表或对应编辑入口修改')
        if type(body.get('baseVersion')) is not int: raise ValueError('缺少工作记录版本，请刷新后保存')
        self.get_work(project_id)
        for key in allowed - {'baseVersion', 'materials', 'attachments'}:
            if key in body and (not isinstance(body[key], str) or len(body[key]) > (200000 if key == 'documentText' else 20000)):
                raise ValueError('工作记录文字格式或长度无效：' + key)
        if body.get('dueAt') and not iso(body['dueAt']): raise ValueError('工作截止日期格式无效')
        for key in ('materials', 'attachments'):
            if key in body and (not isinstance(body[key], list) or len(body[key]) > 200): raise ValueError(key + '格式无效')
            if key in body:
                for item in body[key]:
                    if not isinstance(item, dict) or len(json.dumps(item, ensure_ascii=False)) > 10000:
                        raise ValueError(key + '条目格式无效')
        if 'materials' in body:
            for item in body['materials']:
                if not isinstance(item.get('id'), str) or not isinstance(item.get('label'), str) or type(item.get('done')) is not bool:
                    raise ValueError('资料清单须包含 id、label 和 done')
        if self.excel_mode:
            return self._save_excel_work(project_id, body)
        self.work_path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(str(self.work_path) + '.lock'):
            records = self._read_cache()
            old = {**blank_work(), **records.get(project_id, {})}
            if body['baseVersion'] != old['version']: raise WorkConflict('工作记录已被更新，请刷新后再保存')
            updated = {**old, **{k: v for k, v in body.items() if k not in ('baseVersion', 'baseDigest')},
                       'version': old['version'] + 1, 'updatedAt': datetime.now(TZ).isoformat()}
            # Keep previous states in the same store for recovery, not in UI data.
            history = old.pop('_history', [])
            updated['_history'] = (history + [old])[-30:]
            records[project_id] = updated
            fd, name = tempfile.mkstemp(prefix='.workspace-work-', suffix='.json', dir=self.work_path.parent)
            try:
                with os.fdopen(fd, 'w') as out:
                    json.dump({'projects': records}, out, ensure_ascii=False, indent=2)
                    out.flush(); os.fsync(out.fileno())
                os.replace(name, self.work_path)
            finally:
                Path(name).unlink(missing_ok=True)
            return {**{k: v for k, v in updated.items() if k != '_history'}, 'digest': work_digest(updated), 'sync': '本地工作记录'}

    def export_data(self):
        snapshot = self.snapshot()
        with self._guard:
            portfolio = copy.deepcopy(self._cache[1]['portfolio'])
            supplemental_rows = copy.deepcopy(self._cache[1]['supplementalRows'])
        projects, origins, work_rows = [], [], []
        seen = set()
        for p in snapshot['projects']:
            projects.append({'项目ID': p['id'], '项目名称': p['name'], '甲方名称': p['client'], '项目渠道': p['channel'],
                             '参与状态': p['partStatus'], '是否中标': p['resultStatus'], '报名截止时间': p['deadline'],
                             '开标时间': p['openingAt'], '财年': p['year'], '项目负责人': p['owner'],
                             '目前进度': p['progress'], '计划': p['plan'], '优先级': p['priority'],
                             '限价/招标金额原文': p['limit'], '中标单位': p['winner'], '中标金额原文': p['winningPrice'],
                             '原表是否中标': p['ownMark'], '结果公布时间': p['publishedAt'], '结果公告网址': p['resultUrl'],
                             '业务类型': p['business'], '项目区域': p['region'], '代理机构': p['agency'],
                             '折扣率原文': p['discountRate'] or field(p['fields'], '折扣率', '报价折扣率'),
                             '招标公告网址': p['url'], '全部源字段': p['fields'],
                             **{k: p[k] for k in ('winner', 'amount', 'ownMark', 'publishedAt', 'resultUrl', 'business', 'region', 'agency', 'year')}})
            if p['work']['version']:
                work_rows.append({'项目ID': p['id'], '项目名称': p['name'], '记录类型': '系统工作记录',
                                  **{k: v for k, v in p['work'].items() if k != '_history'}})
            for row in p['sourceRows']:
                key = (p['id'], *source_key(row['source']))
                if key in seen: continue
                seen.add(key)
                origins.append({'项目ID': p['id'], '项目名称': p['name'], '原文件': row['source']['file'],
                                '子表': row['source']['sheet'], '原行': row['source']['row'],
                                '字段': row['fields'], '批注': row.get('comments', {})})
        followups = [{'项目ID': h['projectId'], '项目名称': h['name'], '记录类型': '源表周计划' if h['kind'] == 'plan' else '源表中标明细',
                      '所属周': h['week'], '计划招采时间原文': h['plannedAtRaw'], '项目负责人': h['owner'],
                      '目前进度': h['progress'], '计划': h['plan'], '优先级': h['priority'], '源字段': h['fields']}
                     for h in snapshot['planHistory']]
        followups.extend(work_rows)
        bonds = [{k: v for k, v in b.items() if k != '_rows'} for b in portfolio.get('bonds', [])]
        competitors = [{**p, 'competitor': c['name']} for c in portfolio.get('competitors', []) for p in c['projects']]
        covered_origins = {(r['原文件'], r['子表'], r['原行']) for r in origins}
        extra_sources = []
        for item in bonds + competitors:
            extra_sources.extend(item.get('sourceRows', []))
        extra_sources.extend(portfolio.get('reconciliation', {}).get('unlinkedAuxiliary', []))
        extra_sources.extend(portfolio.get('reconciliation', {}).get('readRows', []))
        extra_sources.extend(supplemental_rows)
        for row in extra_sources:
            if source_key(row['source']) in covered_origins: continue
            covered_origins.add(source_key(row['source']))
            origins.append({'项目ID': '', '项目名称': field(row['fields'], '项目名称', '投递项目名称'),
                            '原文件': row['source']['file'], '子表': row['source']['sheet'],
                            '原行': row['source']['row'], '字段': row['fields'], '批注': row.get('comments', {})})
        return {'updatedAt': snapshot['updatedAt'], 'mode': '整理核对版；未替换原Excel正本',
                'projects': projects, 'followups': followups, 'bonds': bonds, 'competitors': competitors,
                'sourceRows': origins, 'sourceDocuments': snapshot['sourceDocuments'],
                'sheets': {'项目总台账': projects, '跟进记录': followups, '保证金台账': bonds,
                           '竞对记录': competitors, '来源明细': origins},
                'notes': ['业务值取自原表；工作记录读写计划正本的“系统工作记录”子表。',
                          '同名项目的不同招采轮次分别保留；来源坐标随原工作簿保留。']}
