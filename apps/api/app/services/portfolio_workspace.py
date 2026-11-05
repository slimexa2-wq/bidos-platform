"""结果、保证金和重点竞对：直接读取桌面正本，修改按原位置原子回写。"""
from __future__ import annotations
from app.services.file_lock import file_lock

import calendar
import copy
import hashlib
import json
import os
import re
import shutil
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

import openpyxl
from app.services.excel_formula_values import FormulaValues, preserve_formula_caches
from sqlalchemy import select
from app.repositories.models import Project, Entity, SourceObject
from app.services.blind_workspace import ROOT, TZ, normalized, participation, text_value, workbook_path, read_state, workbook_is_open


def clean(v):
    t = text_value(v)
    return '' if t in ('/', '／', '-', '—', '无', 'None') else t


def iso(v):
    if isinstance(v, (date, datetime)):
        return v.strftime('%Y-%m-%d')
    s = clean(v)
    if re.fullmatch(r'\d{5}(?:\.0)?', s):
        return (datetime(1899, 12, 30) + timedelta(days=float(s))).date().isoformat()
    m = re.search(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})', s)
    if m:
        try:
            return date(*map(int, m.groups())).isoformat()
        except ValueError:
            pass
    return ''


def month_after(s, n=1):
    d = date.fromisoformat(s)
    ym = d.year * 12 + d.month - 1 + n
    y, m = divmod(ym, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1])).isoformat()


def fiscal_year(dt, fallback=''):
    """公司财年：上年12月1日到当年11月30日；保留实际日期。"""
    return str(int(dt[:4]) + (dt[5:7] == '12')) if dt else fallback


def source_key(ptr):
    return ptr['file'], ptr['sheet'], ptr['row']


def source_record(row, match_note=''):
    record = {'source': row['source'], 'fields': row['fields'].copy(), 'comments': row.get('comments', {}).copy()}
    if match_note:
        record['matchNote'] = match_note
    return record


def merge_cell_notes(target, incoming):
    for key, value in (incoming or {}).items():
        value = clean(value)
        if not value:
            continue
        current = target.get(key, '')
        if value not in current:
            target[key] = f'{current}\n{value}'.strip() if current else value


def meaningful(v):
    return clean(v) not in ('', '未公示', '未公布', '暂未公示', '待公布', '待公示', '流标', '废标', '重新招采', '放弃中标重新招采')


def misplaced_reason(row):
    if not re.fullmatch(r'[12]月盲投未参与', row['source']['sheet']): return None
    for key in ('打印费（元）', '资料份数'):
        value = row['fields'].get(key, '')
        if len(value) >= 4 and numeric(value) is None and re.search(r'评估|不参与|不擅长|回款|业绩|垫资|税率|地址|岗位|踏勘|巡逻|耗材|限价|利润|倾向|考核|单价|标的|保安|劳务|没有|要求', value):
            return key, value
    return None


def numeric(v, factor=1):
    """只处理明确单位列中的数值或简单乘式；单价、比例、混合文本不汇总。"""
    s = clean(v).replace(',', '').replace('，', '').replace('×', '*').replace(' ', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?(?:\*\d+(?:\.\d+)?)*', s):
        return None
    try:
        value = Decimal(str(factor))
        for term in s.split('*'):
            value *= Decimal(term)
        return float(value)
    except InvalidOperation:
        return None


def money(v, header):
    s = clean(v)
    if not s:
        return ''
    if numeric(s) is not None:
        unit = '万元' if '万元' in header else '元' if '元' in header else ''
        return s + unit
    return s


_TABLE_CACHE = {}
_TABLE_CACHE_LOCK = threading.RLock()


def read_tables(path, predicate, include_comments=False):
    """Reuse unchanged source rows; return copies because reconciliation edits rows."""
    if not path:
        return []
    path = Path(path)
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    key = (str(path.resolve()), bool(include_comments))
    with _TABLE_CACHE_LOCK:
        cached = _TABLE_CACHE.get(key)
        if cached is None or cached[0] != stamp:
            rows = _read_tables_uncached(path, lambda _: True, include_comments)
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != stamp:
                raise RuntimeError('源表读取期间发生修改，请稍后刷新')
            _TABLE_CACHE[key] = (stamp, rows)
            cached = _TABLE_CACHE[key]
        return copy.deepcopy([row for row in cached[1] if predicate(row['source']['sheet'])])


def _read_tables_uncached(path, predicate, include_comments=False):
    if not path:
        return []
    wb = openpyxl.load_workbook(path, read_only=not include_comments, data_only=True)
    formula_reader = FormulaValues(path)
    formula_values, formula_warnings = formula_reader.missing()
    tables = []
    try:
        for ws in wb:
            if not predicate(ws.title):
                continue
            headers, cols, year, section, header_row = [], {}, '', '', 0
            for rownum, row in enumerate(ws.iter_rows(max_col=min(ws.max_column, 40), values_only=True), 1):
                row = tuple(formula_values.get((ws.title, openpyxl.utils.get_column_letter(c) + str(rownum)), value) for c, value in enumerate(row, 1))
                vals = [text_value(v) for v in row]
                title = ' '.join(vals[:3])
                if re.search(r'20\d{2}年.*保证金', title):
                    year = re.search(r'20\d{2}', title).group()
                if '项目名称' in vals or '投递项目名称' in vals:
                    header_row = rownum
                    headers, cols = vals, {}
                    for i, h in enumerate(headers, 1):
                        if h:
                            key = h if h not in cols else h + '（采购方式）'
                            cols[key] = i
                    continue
                if not headers:
                    if any(vals):
                        section = ' | '.join(v for v in vals[:4] if v)
                    continue
                fields = {k: clean(row[c - 1]) for k, c in cols.items()}
                name = fields.get('项目名称') or fields.get('投递项目名称')
                if not name or name in ('合计', '总计') or '保证金统计' in name:
                    if sum(bool(v) for v in vals) <= 2 and any(vals):
                        section = ' | '.join(v for v in vals if v)
                    continue
                fp = hashlib.sha256(json.dumps(vals, ensure_ascii=False).encode()).hexdigest()
                ptr = {'file': path.name, 'sheet': ws.title, 'row': rownum, 'fingerprint': fp, 'width': len(vals), 'headerRow': header_row, 'columns': cols.copy(), 'section': section}
                comments = {}
                if include_comments:
                    for key, col in cols.items():
                        comment = ws.cell(rownum, col).comment
                        if comment and comment.text.strip():
                            text = comment.text.strip()
                            prefix = (comment.author or '') + ':'
                            if comment.author and text.startswith(prefix):
                                text = text[len(prefix):].strip()
                            comments[key] = text
                formulas = {key: formula_reader.formulas[(ws.title, openpyxl.utils.get_column_letter(col) + str(rownum))] for key,col in cols.items() if (ws.title,openpyxl.utils.get_column_letter(col)+str(rownum)) in formula_reader.formulas}
                ptr['formulaFields'] = list(formulas)
                tables.append({'fields': fields, 'columns': cols.copy(), 'source': ptr, 'sectionYear': year, 'comments': comments, 'formulas': formulas})
    finally:
        wb.close()
    return tables


def field(f, *keys):
    return next((f[k] for k in keys if f.get(k)), '')


def identity(name, client):
    return normalized(name), normalized(client)


def unique_match(name, client, dt, records, name_key='name', client_key='client', date_key='date'):
    """正名优先；简称仅在同甲方、同开标日且业务名称可辨识时唯一关联。"""
    exact = [r for r in records if identity(r.get(name_key), r.get(client_key)) == identity(name, client)
             and (not dt or not r.get(date_key) or dt == r[date_key])]
    dated = [r for r in exact if dt and r.get(date_key) == dt]
    if len(dated) == 1:
        return dated[0]
    if len(exact) == 1:
        return exact[0]
    if not client or not dt:
        return None
    def core(n):
        s = normalized(n).replace(normalized(client), '')
        return re.sub(r'竞争性磋商|公开招选|公开征集|框架协议|询价|比选|采购|招标|项目|公告|服务|需求', '', s)
    c = core(name)
    choices = []
    for r in records:
        rd = r.get(date_key)
        if not rd or normalized(r.get(client_key)) != normalized(client) or dt != rd:
            continue
        rc = core(r.get(name_key, ''))
        similar = bool(c and rc and (min(len(c), len(rc)) >= 5 and (c in rc or rc in c) or SequenceMatcher(None, c, rc).ratio() >= .72))
        if similar:
            choices.append(r)
    return choices[0] if len(choices) == 1 else None


def own_result(mark):
    if mark in ('是', '中标', '入库'):
        return '中标'
    if mark in ('否', '未中标', '流标', '废标', '重新招采', '放弃'):
        return '未中标'
    return '待公布' if mark in ('暂未公示', '待公布', '未公布') else '待确认'


def note_fields(f):
    return {k: v for k, v in f.items() if v and any(x in k for x in ('备注', '原因', '改进', '经验', '建议', '结果说明'))}


def result_notes(mark, primary, related=None):
    notes = {'结果说明': mark} if mark in ('流标', '废标', '重新招采', '放弃', '入库') else {}
    for origin, fields in (('原表', primary), ('盲投', related or {})):
        for key, value in note_fields(fields).items():
            if value.startswith('BidOS回写引擎接入验证'):
                continue
            label = re.sub(r'^BidOS', '', key).strip()
            if label == '未参与原因':
                label = '原表说明（未参与原因栏）'
            if label in notes:
                if notes[label] == value:
                    continue
                label += f'（{origin}记录）'
            if label in notes and notes[label] != value:
                label += '（补充）'
            notes[label] = value
    return notes


def business_type(name, *raw_values):
    non_business = {'企业', '机关', '酒店', '事业单位', '事业', '政府', '公开', '公开招标', '询价', '比选', '磋商', '谈判', '竞标', '入库', '公开比质比价', '流标', '废标', '重新招采', '放弃', '中标', '未中标', '待公布', '待确认', '是', '否'}
    for v in raw_values:
        if v and v not in non_business:
            return v
    # 只用项目名称里的明示业务词归类；其他缺失类型留空。
    for word, label in (('劳务派遣', '劳务派遣'), ('劳务外包', '劳务外包'), ('业务外包', '业务外包'),
                        ('岗位外包', '岗位外包'), ('服务外包', '服务外包'), ('人力资源', '人力资源'),
                        ('安保', '安保服务'), ('保安', '安保服务'), ('保洁', '保洁服务'),
                        ('物业', '物业服务'), ('招聘', '招聘服务'), ('劳务服务', '劳务服务')):
        if word in name:
            return label
    return ''


class PortfolioWorkspace:
    def __init__(self, sessions):
        self.sessions = sessions
        self._cache = None
        self._stamp = None
        self._guard = threading.RLock()

    def snapshot(self):
        with self._guard:
            paths = [workbook_path('*投标项目汇总*.xlsx'), workbook_path('*主要竞争*.xlsx'), workbook_path('*盲投项目信息*汇总表*.xlsx')]
            stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) if p else () for p in paths)
            now = datetime.now(TZ)
            if self._cache and stamp == self._stamp and (now - self._cache[0]).total_seconds() < 30:
                return copy.deepcopy(self._cache[1])
            if not paths[0]:
                raise RuntimeError('桌面资料库未找到投标项目汇总表')
            result = self._read(paths, now)
            self._stamp, self._cache = stamp, (datetime.now(TZ), result)
            return copy.deepcopy(result)

    def _read(self, paths, now):
        today = now.date().isoformat()
        ledger, compfile, blindfile = paths
        ledger_rows = read_tables(ledger, lambda s: True, include_comments=True)
        ownrows = [r for r in ledger_rows if re.fullmatch(r'20\d{2}年', r['source']['sheet'].strip())]
        all_blindrows = read_tables(blindfile, lambda s: True, include_comments=True)
        blindrows = [r for r in all_blindrows if re.fullmatch(r'\d{1,2}月', r['source']['sheet'].strip())]
        blind_map = {}
        for r in blindrows:
            f = r['fields']
            blind_map.setdefault(identity(f.get('项目名称'), f.get('甲方名称')), []).append(r)
        dbblind, entities, public = {}, {}, []
        with self.sessions() as session:
            for p in session.scalars(select(Project).where(Project.metadata_['blind_fields'].is_not(None))):
                f = (p.metadata_ or {}).get('blind_fields') or {}
                dbblind.setdefault(identity(f.get('项目名称'), f.get('甲方名称')), []).append(p.metadata_ or {})
            for e in session.scalars(select(Entity)):
                if e.metadata_:
                    entities[normalized(e.canonical_name)] = e.metadata_
            # 公共公告与历史项目共用来源；只收录已辨识的重点竞对结果，不用采集时间冒充发布日期。
        results, excluded, seen = [], 0, set()
        excluded_rows, duplicate_rows = [], []
        # 历史跨表结转以同甲方、同项目、同开标日期、同标段合并；保留每处原位置。
        # 财年对应的子表优先（例如2023年12月结转到2024年），再用较完整的原记录。
        grouped = {}
        for row in ownrows:
            f = row['fields']; dt = iso(f.get('开标时间'))
            key = (*identity(f['项目名称'], f.get('甲方名称', '')), dt, field(f, '标段', '包号'))
            grouped.setdefault(key, []).append(row)
        canonical_own = []
        for group in grouped.values():
            row = max(group, key=lambda r: (r['source']['sheet'][:4] == fiscal_year(iso(r['fields'].get('开标时间'))), bool(r['fields'].get('是否中标')), sum(bool(v) for v in r['fields'].values())))
            row['aliases'] = [r['source'] for r in group]
            row['aliasRows'] = [r for r in group if r is not row]
            for other in group:
                if other is not row:
                    duplicate_rows.append({'source': other['source'], 'canonical': row['source'], 'reason': '跨表同甲方、同项目、同开标日期、同标段结转'})
            canonical_own.append(row)
        blind_refs = [{'name': r['fields']['项目名称'], 'client': r['fields'].get('甲方名称', ''), 'date': iso(r['fields'].get('开标时间')), 'row': r} for r in blindrows]
        for row in canonical_own:
            f, ptr = row['fields'], row['source']
            name, client = f['项目名称'], f.get('甲方名称', '')
            key = identity(name, client)
            registered = field(f, '参与单位')
            link = unique_match(name, client, iso(f.get('开标时间')), blind_refs)
            b = link['row']['fields'] if link else {}
            # 汇总表记录了实际投标单位；不得用月表早期评估状态覆盖最终参与事实。
            if '未参与' in registered or '不参与' in registered or f.get('是否中标') == '放弃':
                excluded += 1
                excluded_rows.append({'source': ptr, 'reason': '原参与单位明确未参与' if '未参与' in registered or '不参与' in registered else '原是否中标标注放弃'})
                continue
            if not registered and not (b and participation(b) == '报名参与'):
                excluded += 1
                excluded_rows.append({'source': ptr, 'reason': '原表没有参与单位，且无唯一报名参与月表记录'})
                continue
            biddate = iso(f.get('开标时间'))
            dedup = (*key, biddate, field(f, '标段', '包号'))
            if dedup in seen:
                continue
            seen.add(dedup)
            meta = dbblind.get(identity(b.get('项目名称'), b.get('甲方名称')) if b else key, [])
            fact = (meta[0].get('blind_announcement') or {}) if len(meta) == 1 else {}
            mark = field(f, '是否中标')
            result = own_result(mark)
            published = iso(field(f, '结果公布时间', '结果公布日期', '中标公示时间', '公示日期') or fact.get('date'))
            sourceurl = field(b, '招标公告网址', '公告网址', '原文链接')
            resulturl = field(f, '结果公告网址') or fact.get('sourceUrl') or fact.get('url') or ''
            notes = result_notes(mark, f, b)
            extra = {}
            # 每一项只出现一次；标记结果、限价、成本的原字段保持可回溯。
            for k, v in f.items():
                if (v or row['comments'].get(k)) and k not in ('项目名称', '甲方名称', '序号', '开标时间', '是否中标', '项目情况', '中标单位', '系统录入'):
                    label = '甲方类型' if k == '项目类型' and v in ('企业', '机关', '酒店', '事业单位', '政府') else '采购方式' if k == '项目类型（采购方式）' or (k == '项目类型' and v in ('询价', '比选', '磋商', '谈判', '公开', '公开招标', '竞标', '入库')) else k
                    extra[label] = iso(v) if '时间' in k and iso(v) else v
            # 已唯一匹配的盲投记录补充项目资料，主汇总表已有内容保持优先。
            for key in ('项目区域', '招标时间', '报名截止时间', '服务期限', '报名费用', '项目编号', '采购方式',
                        '资质要求', '业绩要求', '联合体投标', '其他要求', '基本要求', '项目实施地点', '项目情况简述', '信息筛选人', 'BidOS跟进人'):
                if not extra.get(key) and b.get(key):
                    extra[key] = iso(b[key]) if '时间' in key and iso(b[key]) else b[key]
            results.append({'id': 'result_' + hashlib.sha1('|'.join(map(str, dedup)).encode()).hexdigest()[:16],
                'name': name, 'client': client, 'date': biddate, 'publishedAt': published,
                'year': fiscal_year(biddate, ptr['sheet'][:4]), 'ledgerYear': ptr['sheet'][:4], 'channel': field(f, '项目渠道', '信息来源', '项目情况'),
                'business': business_type(name, field(f, '业务类型'), b.get('项目类型'), f.get('项目类型')), 'company': f.get('所属公司', ''),
                'agency': f.get('代理机构', ''), 'participants': registered, 'result': result, 'ownMark': mark,
                'winner': field(f, '中标单位') or fact.get('winner') or '',
                'amount': money(field(f, '中标金额（万元）', '中标金额'), '中标金额（万元）'),
                'limit': money(field(f, '限价(万元)', '限价（万元）', '限价'), '限价(万元)'),
                'source': ptr, 'sources': row['aliases'] + ([link['row']['source']] if link else []), 'url': sourceurl, 'resultUrl': resulturl, 'notes': notes, 'fields': extra,
                'sourceFields': f.copy(), 'relatedFields': b.copy(), 'cellNotes': {**row['comments'], **(link['row'].get('comments', {}) if link else {})},
                'sourceRows': [source_record(source_row) for source_row in [row, *row.get('aliasRows', [])] + ([link['row']] if link else [])]})
        # 仅在盲投已参与且汇总没有对应项目时补入，避免单靠报名遗漏待结果项目。
        ownkeys = {(*identity(r['name'], r['client']), r['date']) for r in results}
        decline_rows = [r for r in all_blindrows if '未参与' in r['source']['sheet']]
        decline_refs = [{'name': r['fields']['项目名称'], 'client': r['fields'].get('甲方名称', ''),
                         'date': iso(r['fields'].get('开标时间')), 'row': r} for r in decline_rows]
        for row in blindrows:
            f = row['fields']
            key = identity(f.get('项目名称'), f.get('甲方名称'))
            dt = iso(f.get('开标时间')) or iso(f.get('招标时间'))
            match = unique_match(f.get('项目名称'), f.get('甲方名称'), dt, results)
            if participation(f) != '报名参与' or (*key, dt) in ownkeys or match:
                continue
            declined_match = unique_match(f.get('项目名称'), f.get('甲方名称'), dt, [r for r in decline_refs if not dt or not r['date'] or r['date'] == dt])
            if declined_match:
                excluded_rows.append({'source': row['source'], 'reason': '专项未参与表已记录最终未参与', 'decisionSource': declined_match['row']['source']})
                continue
            ownkeys.add((*key, dt))
            metas = dbblind.get(key, [])
            fact = (metas[0].get('blind_announcement') or {}) if len(metas) == 1 else {}
            mark = field(f, '是否中标')
            results.append({'id': 'result_' + hashlib.sha1('|'.join((*key, dt)).encode()).hexdigest()[:16],
                'name': f['项目名称'], 'client': f.get('甲方名称', ''), 'date': dt, 'year': fiscal_year(dt, '2026'),
                'publishedAt': iso(fact.get('date')),
                'channel': '盲投', 'business': business_type(f['项目名称'], f.get('项目类型')), 'company': '', 'agency': f.get('代理机构', ''),
                'participants': '', 'ownMark': mark, 'result': own_result(mark) if mark or fact or f.get('中标单位') else '待公布', 'winner': f.get('中标单位', '') or fact.get('winner', ''),
                'amount': money(field(f, '中标金额（万元）'), '中标金额（万元）') or field(f, '中标金额', 'BidOS中标金额原文') or fact.get('amount', ''), 'limit': f.get('招标金额', ''),
                'source': row['source'], 'sources': [row['source']], 'url': f.get('招标公告网址', ''), 'resultUrl': fact.get('sourceUrl') or '',
                'notes': result_notes(mark, f), 'fields': {k: v for k, v in f.items() if v and k not in ('项目名称', '甲方名称', '中标单位', '中标金额', '是否中标', '报名参与情况', '招标金额')},
                'sourceFields': f.copy(), 'relatedFields': {}, 'cellNotes': row['comments'].copy(),
                'sourceRows': [source_record(row)]})
        # 未中标子表有原始价格资料；只补唯一对应项目的缺值，年度汇总保持优先。
        price_rows = [r for r in ledger_rows if r['source']['sheet'].strip() == '未中标项目']
        price_refs = [{'name': r['fields']['项目名称'], 'client': r['fields'].get('甲方名称', ''),
                       'date': iso(r['fields'].get('开标时间')), 'row': r} for r in price_rows]
        for item in results:
            price_match = unique_match(item['name'], item['client'], item['date'], price_refs)
            if (not price_match or item['result'] != '未中标'
                or identity(item['name'], item['client']) != identity(price_match['name'], price_match['client'])
                or (item['date'] and price_match['date'] and item['date'] != price_match['date'])):
                continue
            price_row = price_match['row']
            price_fields = price_row['fields']
            if not any(price_fields.get(k) for k in ('报价类型', '限价', '中标价格', '折扣率', '中标单位')):
                continue
            item['priceFields'] = price_fields.copy()
            item['priceSource'] = price_row['source']
            item['priceCellNotes'] = price_row['comments'].copy()
            item.setdefault('sourceRows', []).append(source_record(price_row))
            merge_cell_notes(item.setdefault('cellNotes', {}), price_row['comments'])
            item['sources'].append(price_row['source'])
            if not item['winner']:
                item['winner'] = field(price_fields, '中标单位')
            for name, source_column in (('limit', '限价'), ('amount', '中标价格')):
                value = field(price_fields, source_column)
                if not item[name] and value:
                    item[name] = value + '（原表未标单位）' if numeric(value) is not None else value
        auxiliary = [r for r in ledger_rows if r['source']['sheet'].strip() in ('未中标项目', '26年盲投中标项目', '26年盲投中标项目 运营单位', '入库项目台账')]
        for item in results:
            item['auxiliaryFields'] = {}
            item['auxiliarySourceRows'] = []

        def one_year_service(value):
            return bool(re.search(r'(?:1|一)\s*年', clean(value)))

        def one_year_reprocurement(item, row, aux_date):
            old_date = item.get('date', '')
            if not (old_date and aux_date and aux_date > old_date
                    and (one_year_service(item.get('sourceFields', {}).get('服务期限'))
                         or one_year_service(item.get('relatedFields', {}).get('服务期限')))
                    and one_year_service(row['fields'].get('服务期限'))):
                return False
            try:
                return (date.fromisoformat(aux_date) - date.fromisoformat(old_date)).days >= 330
            except ValueError:
                return False

        def standalone_unwon_result(row, aux_name, aux_client, aux_date):
            f = row['fields']
            row_source = row['source']
            entry_id = hashlib.sha1('|'.join((*identity(aux_name, aux_client), aux_date,
                *map(str, source_key(row_source)))).encode()).hexdigest()[:16]
            winner = field(f, '中标单位')
            limit_value = field(f, '限价', '最高限价')
            price_value = field(f, '中标价格', '中标金额')
            return {'id': 'result_' + entry_id, 'name': aux_name, 'client': aux_client,
                'date': aux_date, 'publishedAt': '', 'year': fiscal_year(aux_date, ''),
                'ledgerYear': row_source.get('section', ''),
                'channel': field(f, '项目渠道', '信息来源', '项目情况'),
                'business': business_type(aux_name, field(f, '业务类型', '项目类型')),
                'company': field(f, '所属公司'), 'agency': field(f, '代理机构'),
                'participants': field(f, '参与单位'), 'result': '未中标', 'ownMark': '否',
                'winner': winner,
                'amount': (price_value + '（原表未标单位）' if price_value and numeric(price_value) is not None else price_value),
                'limit': (limit_value + '（原表未标单位）' if limit_value and numeric(limit_value) is not None else limit_value),
                'source': row_source, 'sources': [row_source],
                'url': field(f, '招标公告网址', '公告网址', '公告链接', '原文链接'),
                'resultUrl': field(f, '结果公告网址', '中标结果公告网址', '成交结果公告网址'),
                'notes': note_fields(f),
                'fields': {k: v for k, v in f.items() if v and k not in ('项目名称', '甲方名称', '中标单位')},
                'sourceFields': f.copy(), 'relatedFields': {}, 'priceFields': f.copy(),
                'priceSource': row_source, 'priceCellNotes': row['comments'].copy(),
                'auxiliaryFields': f.copy(), 'auxiliarySourceRows': [source_record(row)],
                'cellNotes': row['comments'].copy(), 'sourceRows': [source_record(row)]}

        for row in auxiliary:
            f = row['fields']
            aux_name = field(f, '项目名称', '投递项目名称')
            aux_client = field(f, '甲方名称', '甲方单位')
            aux_date = iso(field(f, '开标时间', '招采时间'))
            matches = [item for item in results
                       if aux_name and aux_client and identity(item['name'], item['client']) == identity(aux_name, aux_client)
                       and (not aux_date or not item['date'] or aux_date == item['date'])]
            same_project = [item for item in results
                            if aux_name and aux_client and identity(item['name'], item['client']) == identity(aux_name, aux_client)]
            later_flow_round = (len(matches) == 0 and len(same_project) == 1 and aux_date
                and aux_date > same_project[0].get('date', '')
                and same_project[0].get('ownMark') in ('流标', '废标')
                and row['source']['sheet'].strip() == '未中标项目'
                and meaningful(field(f, '中标单位')))
            new_one_year_procurement = (len(matches) == 0 and len(same_project) == 1
                and row['source']['sheet'].strip() == '未中标项目'
                and one_year_reprocurement(same_project[0], row, aux_date)
                and meaningful(field(f, '中标单位')))
            if later_flow_round:
                matches = same_project
            elif new_one_year_procurement:
                results.append(standalone_unwon_result(row, aux_name, aux_client, aux_date))
                continue
            if len(matches) != 1:
                # 入库中标台账本身就是一条结果事实；没有其他项目主记录时补成独立项目，避免漏出统计与详情。
                if (not matches and row['source']['sheet'].strip() == '入库项目台账'
                    and aux_name and aux_client and meaningful(field(f, '中标单位'))):
                    entry_id = hashlib.sha1('|'.join((*identity(aux_name, aux_client), aux_date, *map(str, source_key(row['source'])))).encode()).hexdigest()[:16]
                    results.append({'id': 'result_' + entry_id, 'name': aux_name, 'client': aux_client,
                        'date': aux_date, 'publishedAt': '', 'year': fiscal_year(aux_date, row['source']['sheet'][:4]),
                        'ledgerYear': row['source']['sheet'][:4], 'channel': '入库',
                        'business': business_type(aux_name, field(f, '项目类型', '业务类型')), 'company': field(f, '所属公司'),
                        'agency': field(f, '代理机构'), 'participants': '', 'result': '中标', 'ownMark': '入库',
                        'winner': field(f, '中标单位'), 'amount': money(field(f, '中标价格', '中标金额'), '中标金额'),
                        'limit': money(field(f, '限价', '最高限价'), '限价'), 'source': row['source'],
                        'sources': [row['source']], 'url': field(f, '招标公告网址', '公告网址'),
                        'resultUrl': field(f, '结果公告网址'), 'notes': {},
                        'fields': {k: v for k, v in f.items() if v and k not in ('项目名称', '甲方名称', '中标单位')},
                        'sourceFields': f.copy(), 'relatedFields': {}, 'auxiliaryFields': f.copy(),
                        'cellNotes': row['comments'].copy(), 'sourceRows': [source_record(row)]})
                continue
            item = matches[0]
            if source_key(row['source']) in {source_key(source) for source in item.get('sources', [])}:
                continue
            item['auxiliarySourceRows'].append(source_record(row))
            item['sourceRows'].append(source_record(row))
            merge_cell_notes(item['cellNotes'], row['comments'])
            item['sources'].append(row['source'])
            item['auxiliaryFields'].update({k: v for k, v in f.items() if v and not item['sourceFields'].get(k)})
            # 来源表头可能写“甲方单位”；仅用作匹配和标准字段补值，原始表头和值仍在来源行内保留。
            if f.get('甲方单位') and not item['sourceFields'].get('甲方名称'):
                item['auxiliaryFields'].setdefault('甲方名称', f['甲方单位'])
            if f.get('区域') and not item['sourceFields'].get('项目区域'):
                item['auxiliaryFields'].setdefault('项目区域', f['区域'])
            if not meaningful(item.get('winner', '')) and meaningful(field(f, '中标单位')):
                item['winner'] = field(f, '中标单位')
            if row['source']['sheet'].strip() == '未中标项目':
                item['priceFields'] = f.copy()
                item['priceSource'] = row['source']
                item['priceCellNotes'] = row['comments'].copy()
                if later_flow_round:
                    # 旧轮次流标、后续同名同甲方项目已公布中标结果：主视图展示最新轮次，旧源行仍保留在来源记录中。
                    item['date'] = aux_date
                    item['year'] = fiscal_year(aux_date, item.get('year', ''))
                    item['result'] = '未中标'
                    item['ownMark'] = '否'
                    item['notes'].pop('结果说明', None)
                if meaningful(field(f, '中标单位')):
                    item['winner'] = field(f, '中标单位')
                    if field(f, '中标单位') != field(f, '参与单位'):
                        item['result'] = '未中标'
                        item['ownMark'] = '否'
                for key, label in (('limit', '限价'), ('amount', '中标价格')):
                    value = field(f, label)
                    if value:
                        item[key] = value + '（原表未标单位）' if numeric(value) is not None else value
        linked_auxiliary = {source_key(source) for item in results for source in item.get('sources', [])}
        unlinked_auxiliary = []
        for row in auxiliary:
            if source_key(row['source']) in linked_auxiliary:
                continue
            f = row['fields']
            name = field(f, '项目名称', '投递项目名称')
            client = field(f, '甲方名称', '甲方单位')
            dt = iso(field(f, '开标时间', '招采时间'))
            same_project = [item for item in results if name and client and identity(item['name'], item['client']) == identity(name, client)]
            match_note = ''
            if same_project and dt:
                dates = sorted({item['date'] for item in same_project if item.get('date')})
                if dates and dt not in dates:
                    match_note = f'项目名称和甲方与已有项目相同，但原表开标时间为{dt}，已有记录为{ "、".join(dates) }；暂不合并，避免把不同批次或日期冲突覆盖。'
            unlinked_auxiliary.append(source_record(row, match_note))
        results.sort(key=lambda r: r['date'], reverse=True)
        from app.services.portfolio_announcement_facts import PortfolioAnnouncementFacts
        PortfolioAnnouncementFacts().apply(results)
        blind_records = self._blind_records(all_blindrows, ledger_rows, results, dbblind)
        bonds = self._bonds(ledger, results, today, [r for r in ledger_rows if '保证金' in r['source']['sheet']])
        competitors, compnotes = self._competitors(compfile, results, entities, [], today)
        reminders = []
        for b in bonds:
            if b['status'] == '逾期未退':
                reminders.append({'id': b['id'], 'kind': 'bond', 'title': b['name'],
                    'detail': f"{b['kind']} {b['amountText']} · 应退 {b['dueAt']} · 逾期 {b['overdueDays']} 天",
                    'severity': 'critical', 'at': b['dueAt'], 'path': '/bid-bonds?select=' + b['id']})
        for c in competitors:
            for p in c['projects']:
                if p['expiring']:
                    reminders.append({'id': 'expiry_' + p['id'], 'kind': 'competitor', 'title': p['name'],
                        'detail': f"{c['name']} · {p['endAt']} {'预计到期' if p['endEstimated'] else '到期'} · 剩余 {p['daysLeft']} 天",
                        'severity': 'warning', 'at': p['endAt'], 'path': '/competitors?select=' + c['id'] + '&view=expiry&project=' + p['id']})
        collection = read_state().get('collection') or {}
        return {'updatedAt': now.isoformat(), 'sources': [p.name for p in paths if p], 'results': results,
            'blindRecords': blind_records,
            'reconciliation': {'fiscalYear': '上年12月1日—当年11月30日', 'excludedResults': excluded_rows, 'duplicateResults': duplicate_rows,
                'unlinkedAuxiliary': unlinked_auxiliary,
                'readRows': [{'source': r['source'], 'fields': r['fields']} for r in ledger_rows + all_blindrows]},
            'excludedNonParticipants': excluded, 'bonds': bonds, 'competitors': competitors, 'reminders': reminders,
            'notes': compnotes, 'collection': {k: collection.get(k) for k in ('lastRun', 'status', 'usable', 'failed', 'total')},
            'resultPolicy': '财年为上年12月至当年11月，按实际开标日期归属；中标率=中标÷（中标+未中标）；入库计中标；仅报名参与，待公布不计分母。',
            'channelPolicy': '参与来源沿用原表“项目情况”；新开、新增、续签保留原口径。',
            'publicSync': self._sync_state()}

    def _blind_records(self, blindrows, ledger_rows, results, dbblind):
        """每个项目保留原表位置；最终参与/中标以汇总为准，未参与跟进以专项表为准。"""
        monthly = [r for r in blindrows if re.fullmatch(r'\d{1,2}月', r['source']['sheet'].strip())]
        declined = [r for r in blindrows if '未参与' in r['source']['sheet']]
        declined += [r for r in ledger_rows if '未参与' in r['source']['sheet'] and r['fields'].get('项目情况') == '盲投']
        declined += [r for r in ledger_rows if re.fullmatch(r'20\d{2}年', r['source']['sheet'].strip())
                     and r['fields'].get('项目情况') == '盲投' and ('未参与' in r['fields'].get('参与单位', '') or r['fields'].get('是否中标') == '放弃')]
        annual = [r for r in results if r['channel'] == '盲投' and re.fullmatch(r'20\d{2}年', r['source']['sheet'].strip())]
        def ref(row):
            f = row['fields']
            return {'name': f.get('项目名称', ''), 'client': f.get('甲方名称', ''), 'date': iso(f.get('开标时间')), 'row': row}
        decline_refs = [ref(r) for r in declined]
        annual_refs = [{**r, 'row': r} for r in annual]
        used_declines, used_annual, output = set(), set(), []

        def match(name, client, dt, refs, used):
            # 金额和结果只在同一开标日关联；时间不同的再次招采不合并。
            available = [r for r in refs if source_key(r['row']['source']) not in used
                         and (not dt or not r['date'] or dt == r['date'])]
            return unique_match(name, client, dt, available)

        def append(base, extras=(), own=None, declined_row=False):
            ptr, raw = base['source'], base['fields']
            fields, field_sources, sources = {}, {}, []
            cell_notes = {}
            source_rows = []
            warnings = []
            for source_row in (base, *extras):
                source = source_row['source']
                if source_key(source) not in {source_key(s) for s in sources}:
                    sources.append(source)
                source_rows.append(source_record(source_row))
                merge_cell_notes(cell_notes, source_row.get('comments'))
                for k, value in source_row['fields'].items():
                    if k == '项目类型' and value in ('企业', '机关', '酒店', '事业单位', '事业', '政府'):
                        fields['甲方类型'] = value
                        field_sources['甲方类型'] = {'source': source, 'field': k}
                        if fields.get(k) and fields[k] not in ('企业', '机关', '酒店', '事业单位', '事业', '政府'):
                            continue
                    if k not in fields or value:
                        fields[k] = value
                        field_sources[k] = {'source': source, 'field': k}
                # 早期两张未参与表把原因填入“打印费”栏：保留原文及原位置，不算费用。
                misplaced = misplaced_reason(source_row)
                if misplaced:
                    reason_key, reason_value = misplaced
                    if not fields.get('未参与原因'):
                        fields['未参与原因'] = reason_value
                        field_sources['未参与原因'] = {'source': source, 'field': reason_key}
                    warnings.append(f'原未参与说明填在“{reason_key}”栏，已按原文展示。')
            status = '报名参与' if own else '报名未参与' if declined_row or any('未参与' in s['sheet'] for s in sources) else participation(fields)
            # 原月表的名称/甲方用于读原招采资料，汇总的名称在独立记录中原样显示。
            name, client = raw.get('项目名称', ''), raw.get('甲方名称', '')
            dt = iso(fields.get('开标时间'))
            own_mark = own['ownMark'] if own else ''
            winner = own['winner'] if own and own['winner'] else field(fields, '中标单位', 'BidOS中标单位')
            amount = own['amount'] if own and own['amount'] else next((money(fields[k], k) for k in ('中标价格', '中标金额（万元）', '中标金额', 'BidOS中标金额原文') if fields.get(k)), '')
            limit = own['limit'] if own and own['limit'] else next((money(fields[k], k) for k in ('限价(万元)', '限价（万元）', '限价', '招标金额', '最高限价', '采购预算') if fields.get(k)), '')
            metas = dbblind.get(identity(name, client), [])
            fact = (metas[0].get('blind_announcement') or {}) if len(metas) == 1 else {}
            if not winner: winner = fact.get('winner', '')
            if not amount: amount = fact.get('amount', '')
            terminal = winner in ('流标', '废标', '重新招采', '放弃中标重新招采') or own_mark in ('流标', '废标', '重新招采')
            published = bool(meaningful(winner) or meaningful(amount) or terminal or own_mark in ('是', '否', '入库', '中标', '未中标') or fields.get('BidOS结果公布') == '已公布' or fact)
            result = own['result'] if own else ('未中标' if published else '待公布') if status == '报名未参与' else ('待确认' if published else '待公布') if status == '报名参与' else ''
            if not meaningful(winner): winner = ''
            mark = {**(own.get('sourceFields') or {}), '_file': own['source']['file'], '_sheet': own['source']['sheet'], '_row': own['source']['row']} if own else {}
            if own:
                sources += [s for s in own.get('sources', []) if source_key(s) not in {source_key(x) for x in sources}]
                # 标注改写必须定位实际汇总原格，不能依赖同名查找。
                field_sources['是否中标'] = {'source': own['source'], 'field': '是否中标'}
                if participation(raw) != '报名参与' and field(raw, '报名参与情况', '报名参与情况统计'):
                    warnings.append('月表参与状态与汇总实际参与单位不一致；按汇总事实计报名参与。')
                if any('未参与' in s['sheet'] for s in sources):
                    warnings.append('未参与表与汇总参与事实有冲突；已保留两处来源，是否中标按汇总标注。')
            actual = raw.get('BidOS项目ID', '') if re.fullmatch(r'\d{1,2}月', ptr['sheet']) else ''
            pid = actual or 'source_blind_' + hashlib.sha1('|'.join(map(str, source_key(ptr))).encode()).hexdigest()[:20]
            output.append({'id': pid, 'name': name, 'client': client, 'agency': fields.get('代理机构', ''),
                'region': field(fields, '项目区域', '项目地点'), 'type': business_type(name, field(raw, '业务类型', '项目类型')),
                'deadline': iso(fields.get('报名截止时间')), 'date': dt, 'year': own['year'] if own else fiscal_year(dt, re.search(r'20\d{2}', ptr['sheet']).group() if re.search(r'20\d{2}', ptr['sheet']) else '2026'),
                'month': ptr['sheet'], 'row': ptr['row'], 'status': status, 'result': result, 'published': published,
                'winner': winner, 'amount': amount, 'limit': limit, 'ownMark': own_mark,
                'ownMarkMissing': status == '报名参与' and result == '待确认', 'ledgerConflict': False,
                'resultUrl': own.get('resultUrl', '') if own else fields.get('BidOS结果公告网址', '') or fact.get('url', ''),
                'sync': '已同步', 'syncErrors': [], 'fields': fields, 'ledger': mark, 'source': ptr,
                'sources': sources, 'sourceRows': source_rows, 'cellNotes': cell_notes,
                'fieldSources': field_sources, 'sourceWarnings': warnings,
                'readOnlyFields': [k for k,origin in field_sources.items() if origin['field'] in origin['source'].get('formulaFields', [])]})

        for row in monthly:
            f = row['fields']; dt = iso(f.get('开标时间'))
            am = match(f['项目名称'], f.get('甲方名称', ''), dt, annual_refs, used_annual)
            dm = match(f['项目名称'], f.get('甲方名称', ''), dt, decline_refs, used_declines)
            extras = []
            if dm:
                used_declines.add(source_key(dm['row']['source'])); extras.append(dm['row'])
            own = am['row'] if am else None
            if own:
                used_annual.add(source_key(own['source']))
                primary_comments = next((r.get('comments', {}) for r in own.get('sourceRows', []) if source_key(r['source']) == source_key(own['source'])), {})
                extras.append({'source': own['source'], 'fields': own['sourceFields'], 'comments': primary_comments})
                extras.extend(own.get('aliasRows', []))
                if own.get('priceSource'):
                    price_comments = next((r.get('comments', {}) for r in own.get('sourceRows', []) if source_key(r['source']) == source_key(own['priceSource'])), {})
                    extras.append({'source': own['priceSource'], 'fields': own['priceFields'], 'comments': price_comments})
                extras.extend(own.get('auxiliarySourceRows', []))
            append(row, extras, own)
        for row in declined:
            if source_key(row['source']) in used_declines: continue
            f = row['fields']; dt = iso(f.get('开标时间'))
            am = match(f['项目名称'], f.get('甲方名称', ''), dt, annual_refs, used_annual)
            own = am['row'] if am else None
            extras = []
            if own:
                used_annual.add(source_key(own['source']))
                primary_comments = next((r.get('comments', {}) for r in own.get('sourceRows', []) if source_key(r['source']) == source_key(own['source'])), {})
                extras.append({'source': own['source'], 'fields': own['sourceFields'], 'comments': primary_comments})
                extras.extend(own.get('aliasRows', []))
                extras.extend(own.get('auxiliarySourceRows', []))
            # 不同未参与子表的确切同一项目合并来源，保留新表跟进资料优先。
            key = (*identity(f['项目名称'], f.get('甲方名称', '')), dt)
            previous = next((x for x in output if (*identity(x['name'], x['client']), x['date']) == key), None)
            if previous and not own:
                previous['sources'].append(row['source'])
                if source_key(row['source']) not in {source_key(x['source']) for x in previous['sourceRows']}:
                    previous['sourceRows'].append(source_record(row))
                merge_cell_notes(previous['cellNotes'], row.get('comments'))
                for k, v in f.items():
                    if v and any(word in k for word in ('原因', '备注')) and previous['fields'].get(k) and v not in previous['fields'][k]:
                        previous['fields'][k] += '\n' + v
                        previous['fieldSources'].pop(k, None)
                        previous.setdefault('readOnlyFields', []).append(k)
                        continue
                    if v and re.search(r'20\d{2}年未参与', row['source']['sheet']) and k in ('中标单位', '限价', '中标价格', '折扣率', '报价折扣率'):
                        previous['fields'][k] = v; previous['fieldSources'][k] = {'source': row['source'], 'field': k}
                        continue
                    if v and not previous['fields'].get(k):
                        previous['fields'][k] = v; previous['fieldSources'][k] = {'source': row['source'], 'field': k}
                misplaced = misplaced_reason(row)
                if misplaced:
                    reason_key, reason_value = misplaced
                    old_reason = previous['fields'].get('未参与原因', '')
                    if reason_value not in old_reason:
                        previous['fields']['未参与原因'] = old_reason + ('\n' if old_reason else '') + reason_value
                        # 合并后的旧备注不可写回一格；每处原文仍在来源详情中定位。
                        previous['fieldSources'].pop('未参与原因', None)
                        previous.setdefault('readOnlyFields', []).append('未参与原因')
                        previous['sourceWarnings'].append(f'另一张未参与表的说明填在{reason_key}栏，已一并保留；合并说明请在各原表分别修改。')
                # 补表中更完整的结果不能因为月表空白被吞掉。
                if meaningful(f.get('中标单位')) and (not previous['winner'] or re.search(r'20\d{2}年未参与', row['source']['sheet'])):
                    previous['winner'] = f['中标单位']; previous['published'] = True; previous['result'] = '未中标'
                if f.get('中标价格'): previous['amount'] = money(f['中标价格'], '中标价格')
                if f.get('限价'): previous['limit'] = money(f['限价'], '限价')
                continue
            append(row, extras, own, True)
        for own in annual:
            if source_key(own['source']) not in used_annual:
                extras = [{'source': own['priceSource'], 'fields': own['priceFields']}] if own.get('priceSource') else []
                append({'source': own['source'], 'fields': own['sourceFields']}, extras, own)
        # 月表中两处确切重复也只展示一个项目，保留全部原位置。
        distinct, undated = {}, []
        for item in output:
            if not item['date']:
                undated.append(item); continue
            k = (*identity(item['name'], item['client']), item['date'], field(item['fields'], '标段', '包号'))
            old = distinct.get(k)
            if not old:
                distinct[k] = item; continue
            primary, secondary = (item, old) if (bool(item['ledger']), item['status'] != '未报名') > (bool(old['ledger']), old['status'] != '未报名') else (old, item)
            for source in secondary['sources']:
                if source_key(source) not in {source_key(s) for s in primary['sources']}: primary['sources'].append(source)
            for field_key, value in secondary['fields'].items():
                if value and not primary['fields'].get(field_key):
                    primary['fields'][field_key] = value
                    if field_key in secondary['fieldSources']: primary['fieldSources'][field_key] = secondary['fieldSources'][field_key]
            for source_row in secondary.get('sourceRows', []):
                if source_key(source_row['source']) not in {source_key(x['source']) for x in primary['sourceRows']}:
                    primary['sourceRows'].append(source_row)
            merge_cell_notes(primary.setdefault('cellNotes', {}), secondary.get('cellNotes'))
            distinct[k] = primary
        return sorted([*distinct.values(), *undated], key=lambda r: r['date'] or iso(r['fields'].get('招标时间')), reverse=True)

    def _sync_state(self):
        try:
            return json.loads((ROOT / 'data/portfolio_public_sync.json').read_text())
        except (OSError, ValueError):
            return {}

    def sync_public_results(self):
        """跟随既有公告采集，明确中标人属于名单时写回竞对当月原表。"""
        from app.services.result_announcement_backfill import parse_result_announcement
        from app.services.tender_details import extract_details
        from openpyxl.styles import Alignment
        from openpyxl.comments import Comment
        target = workbook_path('*主要竞争*.xlsx')
        if not target:
            raise RuntimeError('未找到桌面竞对统计表')
        snapshot = self.snapshot()
        firms = snapshot['competitors']
        known_urls = {(c['name'], p['url']) for c in firms for p in c['projects'] if p['url']}
        known_names = {(c['name'], *identity(p['name'], p['client'])) for c in firms for p in c['projects']}
        pending, parsed_count = [], 0
        with self.sessions() as session:
            notices = session.scalars(select(SourceObject).where(SourceObject.source_type != 'sheet', SourceObject.published_at >= datetime.now(TZ) - timedelta(days=120))).all()
            for obj in notices:
                title, body = obj.title or '', obj.normalized_text or ''
                if not re.search(r'中标|成交|结果公告', title) or '候选' in title or not obj.source_url or not obj.published_at:
                    continue
                if not any(normalized(c['name']) in normalized(body) for c in firms):
                    continue
                published = obj.published_at.astimezone(TZ).date().isoformat()
                detail = extract_details(title, body, published=published, url=obj.source_url)['fields']
                client = detail.get('甲方名称') or ''
                parsed = parse_result_announcement(body, title=title, client_name=client, result_date=date.fromisoformat(published), source_url=obj.source_url)
                parsed_count += 1
                if not parsed.reliable or not client:
                    continue
                for c in firms:
                    if not any(normalized(c['name']) == normalized(w) for w in parsed.winners):
                        continue
                    if (c['name'], obj.source_url) in known_urls or (c['name'], *identity(title, client)) in known_names:
                        continue
                    amount = '；'.join(x['raw'] for x in parsed.amount_lines if normalized(x['winner']) == normalized(c['name']))
                    if not amount and len(parsed.winners) == 1 and parsed.amount_yuan is not None:
                        amount = f'{parsed.amount_yuan:g}元'
                    raw_limit = detail.get('招标金额') or ''
                    fields = {'公司名称': c['name'], '投递项目名称': title, '项目类型': detail.get('项目类型', ''),
                        '是否中标': '是', '项目地点': detail.get('项目区域', ''), '招采时间': published,
                        '服务期限': detail.get('服务期限', ''), '中标单位': '、'.join(parsed.winners),
                        '项目金额': '；'.join(v for v in (raw_limit, '中标金额：' + amount if amount else '') if v),
                        '岗位类型': '', '甲方名称': client, '招标平台': obj.source_id, '公告连接': obj.source_url}
                    pending.append(fields); known_urls.add((c['name'], obj.source_url))
        report = {'lastRun': datetime.now(TZ).isoformat(), 'status': '已整理', 'added': 0, 'parsed': parsed_count,
            'scope': '已有公开渠道采集到的结果公告；需明确中标单位、甲方和发布日期'}
        if pending:
            with file_lock('/tmp/bidos_nas_sync.lock'):
                if (target.parent / ('~$' + target.name)).exists():
                    raise RuntimeError('竞对Excel正在打开，本轮稍后重试')
                stamp = (target.stat().st_mtime_ns, target.stat().st_size)
                wb = openpyxl.load_workbook(target)
                tmp = target.with_name('.' + target.stem + '.bidos-tmp-' + uuid4().hex + '.xlsx')
                try:
                    month = datetime.now(TZ).strftime('%Y.%m')
                    template = wb['2026.08'] if '2026.08' in wb.sheetnames else next(s for s in wb if s.cell(2, 3).value == '投递项目名称')
                    if month in wb.sheetnames:
                        ws = wb[month]
                    else:
                        ws = wb.create_sheet(month)
                        ws.cell(1, 1, f'竞对投标项目统计（{month}）')
                        for c in range(1, 15):
                            old, new = template.cell(2, c), ws.cell(2, c)
                            new.value = old.value; new._style = copy.copy(old._style)
                            ws.column_dimensions[new.column_letter].width = template.column_dimensions[new.column_letter].width
                        ws.freeze_panes = 'D3'
                    columns = {text_value(ws.cell(2, c).value): c for c in range(1, ws.max_column + 1)}
                    if not all(k in columns for k in ('公司名称', '投递项目名称', '中标单位', '公告连接')):
                        raise RuntimeError('竞对当月表头缺少关键字段，未写入')
                    live_urls = {(clean(ws.cell(i, columns['公司名称']).value), clean(ws.cell(i, columns['公告连接']).value)) for i in range(3, ws.max_row + 1)}
                    for fields in pending:
                        if (fields['公司名称'], fields['公告连接']) in live_urls:
                            continue
                        row = ws.max_row + 1
                        ws.cell(row, 1, row - 2)
                        for k, value in fields.items():
                            col = columns.get(k)
                            if col:
                                cell = ws.cell(row, col, value or None)
                                cell.alignment = Alignment(wrap_text=True, vertical='top')
                                cell.comment = Comment('自动整理公开结果公告。来源：' + fields['公告连接'], 'BidOS')
                        report['added'] += 1
                    backup_dir = ROOT / 'data/portfolio_excel_backups'; backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup_dir / (target.stem + '.' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '.' + uuid4().hex[:6] + '.xlsx'))
                    wb.save(tmp)
                    preserve_formula_caches(target, tmp)
                    if (target.parent / ('~$' + target.name)).exists() or stamp != (target.stat().st_mtime_ns, target.stat().st_size):
                        raise RuntimeError('竞对Excel已变化，保留原文件等待下一轮')
                    os.replace(tmp, target)
                finally:
                    wb.close()
                    if tmp.exists(): tmp.unlink()
        state_path = ROOT / 'data/portfolio_public_sync.json'
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        self._cache = None
        return report

    def sync_result_dates(self):
        """全部渠道参与项目：仅凭唯一匹配的正式结果公告补公示日期。"""
        from app.services.result_announcement_backfill import parse_result_announcement
        target = workbook_path('*投标项目汇总*.xlsx')
        if not target:
            raise RuntimeError('未找到桌面投标项目汇总表')
        rows = [r for r in self.snapshot()['results'] if r['source']['file'] == target.name and not r['publishedAt'] and r['date']]
        updates = {}
        with self.sessions() as session:
            notices = session.scalars(select(SourceObject).where(SourceObject.source_type != 'sheet', SourceObject.published_at >= datetime.now(TZ) - timedelta(days=120))).all()
            for obj in notices:
                title, body = obj.title or '', obj.normalized_text or ''
                if not re.search(r'中标|成交|结果公告', title) or '候选' in title or not obj.published_at or not obj.source_url:
                    continue
                published = obj.published_at.astimezone(TZ).date().isoformat()
                body_norm, notice = normalized(body), normalized(title)
                possible = []
                for r in rows:
                    distance = (date.fromisoformat(published) - date.fromisoformat(r['date'])).days
                    if not 0 <= distance <= 60 or not r['client'] or normalized(r['client']) not in body_norm:
                        continue
                    core = re.sub(r'采购|招标|项目|公告|结果|中标|成交|服务', '', normalized(r['name']).replace(normalized(r['client']), ''))
                    if len(core) < 6 or core not in notice:
                        continue
                    parsed = parse_result_announcement(body, title=title, client_name=r['client'], result_date=date.fromisoformat(published), source_url=obj.source_url)
                    if parsed.reliable:
                        possible.append(r)
                if len(possible) == 1:
                    r = possible[0]
                    previous = updates.get(r['id'])
                    if previous is None or published > previous['date']:
                        updates[r['id']] = {'row': r, 'date': published, 'url': obj.source_url}
        if not updates:
            return {'addedDates': 0}
        with file_lock('/tmp/bidos_nas_sync.lock'):
            if (target.parent / ('~$' + target.name)).exists():
                raise RuntimeError('项目汇总Excel正在打开，公示日期稍后补全')
            stamp = (target.stat().st_mtime_ns, target.stat().st_size)
            wb = openpyxl.load_workbook(target)
            tmp = target.with_name('.' + target.stem + '.bidos-tmp-' + uuid4().hex + '.xlsx')
            changed = 0
            try:
                for update in updates.values():
                    r = update['row']; ws = wb[r['source']['sheet']]
                    number = r['source']['row']
                    live = [text_value(ws.cell(number, c).value) for c in range(1, r['source']['width'] + 1)]
                    fingerprint = hashlib.sha256(json.dumps(live, ensure_ascii=False).encode()).hexdigest()
                    if fingerprint != r['source']['fingerprint']:
                        continue
                    headers = {text_value(ws.cell(2, c).value): c for c in range(1, min(ws.max_column, 40) + 1) if ws.cell(2, c).value}
                    for key, value in (('结果公布日期', update['date']), ('结果公告网址', update['url'])):
                        col = headers.get(key)
                        if not col:
                            col = max(headers.values()) + 1
                            headers[key] = col; ws.cell(2, col, key)
                        if not clean(ws.cell(number, col).value):
                            ws.cell(number, col, value)
                    changed += 1
                if changed:
                    backup_dir = ROOT / 'data/portfolio_excel_backups'; backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup_dir / (target.stem + '.' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '.' + uuid4().hex[:6] + '.xlsx'))
                    wb.save(tmp)
                    preserve_formula_caches(target, tmp)
                    if (target.parent / ('~$' + target.name)).exists() or stamp != (target.stat().st_mtime_ns, target.stat().st_size):
                        raise RuntimeError('项目汇总已变化，本轮未替换原文件')
                    os.replace(tmp, target)
            finally:
                wb.close()
                if tmp.exists(): tmp.unlink()
        self._cache = None
        return {'addedDates': changed}

    def _bonds(self, ledger, results, today, rows=None):
        if rows is None:
            rows = read_tables(ledger, lambda s: '保证金' in s, include_comments=True)
        by_project = {}
        for r in results:
            by_project.setdefault(identity(r['name'], r['client']), []).append(r)
        groups = {}
        for row in rows:
            f, ptr = row['fields'], row['source']
            kind = '履约保证金' if '履约' in ptr['sheet'] else '投标保证金'
            amount_key = next((k for k in f if kind in k and ('元' in k)), '')
            raw = f.get(amount_key, '')
            factor = 10000 if '万元' in amount_key else 1
            amount = numeric(raw, factor)
            # 子表为付款台账，但空白状态不等于未缴纳。
            if not raw and not f.get('退回情况'):
                continue
            name, client = f['项目名称'], f.get('甲方名称', '')
            dt = iso(f.get('开标时间'))
            paid = iso(field(f, '缴纳时间', '缴纳日期'))
            key = (kind, *identity(name, client), dt or paid, str(amount) if amount is not None else raw)
            state = f.get('退回情况', '')
            status = '已退回' if '已退回' in state else '无需退回' if '转' in state and '履约' in state else '未缴纳' if '未缴纳' in state else '待退回' if state else '待确认'
            # 单元格明示完整日期时才解析；“10.13”保留原文，不猜年份。
            actual = iso(field(f, '实际退回日期', '实际退回时间') or state) if status == '已退回' else ''
            match = unique_match(name, client, dt, results)
            published = match['publishedAt'] if match else ''
            due = iso(field(f, '退回时间（合同到期）', '应退日期')) if kind == '履约保证金' else month_after(published) if published else ''
            if status in ('待退回', '待确认') and due and due < today:
                status = '逾期未退'
            b = {'id': 'bond_' + hashlib.sha1('|'.join(key).encode()).hexdigest()[:16],
                'kind': kind, 'name': name, 'client': client, 'date': dt, 'paidAt': paid,
                'amount': amount, 'amountText': money(raw, amount_key), 'amountRaw': raw,
                'status': status, 'statusRaw': state, 'publishedAt': published, 'dueAt': due, 'refundedAt': actual,
                'owner': field(f, '跟进人', '负责人'), 'company': f.get('所属公司', ''), 'note': field(f, '备注'),
                'resultStatus': match['result'] if match else '', 'sourceFields': f.copy(), 'cellNotes': row['comments'],
                'dueBasis': '合同到期（原表）' if due and kind == '履约保证金' else '结果公布后一个月' if due else '',
                'overdueDays': (date.fromisoformat(today) - date.fromisoformat(due)).days if status == '逾期未退' else 0,
                'projectId': match['id'] if match else '', 'sources': [ptr], 'sourceRows': [source_record(row)], '_rows': [row]}
            if key in groups:
                existing = groups[key]
                existing['sources'].append(ptr); existing['_rows'].append(row)
                existing['sourceRows'].append(source_record(row))
                existing['sourceFields'].update({k: v for k, v in f.items() if v})
                existing['cellNotes'].update(row['comments'])
                if status == '已退回' or (existing['status'] != '已退回' and '跟进' in ptr['sheet']):
                    for attr in ('status', 'statusRaw', 'refundedAt', 'owner', 'note'):
                        existing[attr] = b[attr] or existing[attr]
                if existing['status'] == '已退回':
                    existing['overdueDays'] = 0
            else:
                groups[key] = b
        return sorted(groups.values(), key=lambda b: (b['status'] != '逾期未退', b['status'] in ('已退回', '无需退回'), b['dueAt'] or '9999'))

    def _competitors(self, compfile, results, entities, public, today):
        rows = read_tables(compfile, lambda s: not any(k in s for k in ('汇总', '分析')), include_comments=True)
        tracked = sorted({r['fields'].get('公司名称') for r in rows if r['fields'].get('公司名称') and '公司' in r['fields'].get('公司名称')})
        projects, last_name, notes = {n: {} for n in tracked}, {}, []
        own = {}
        for r in results:
            own.setdefault(identity(r['name'], r['client']), []).append(r)
        for row in rows:
            f, src = row['fields'], row['source']
            company = f.get('公司名称') or last_name.get(src['sheet'], '')
            if f.get('公司名称'):
                last_name[src['sheet']] = f['公司名称']
            if company not in projects:
                continue
            name, client = field(f, '投递项目名称', '项目名称'), f.get('甲方名称', '')
            dt = iso(f.get('招采时间'))
            # “2025.4”等精度不足的日期保留原文；不臆造某一天。
            key = (*identity(name, client), dt or clean(f.get('招采时间')) or src['sheet'])
            url = field(f, '公告连接', '公告链接')
            mark = f.get('是否中标', '')
            won = mark in ('是', '中标', '入库')
            end = iso(field(f, '到期日期', '合同到期时间', '服务结束时间'))
            estimated = False
            duration = field(f, '服务期限')
            if won and not end and dt:
                m = re.fullmatch(r'(\d+)\s*(年|月|个月|天)', duration)
                if m:
                    n = int(m.group(1))
                    end = (date.fromisoformat(dt) + timedelta(days=n)).isoformat() if m.group(2) == '天' else month_after(dt, n * (12 if m.group(2) == '年' else 1))
                    estimated = True
            days = (date.fromisoformat(end) - date.fromisoformat(today)).days if end else None
            our = unique_match(name, client, dt, results)
            p = {'id': 'comp_' + hashlib.sha1((company + '|'.join(key)).encode()).hexdigest()[:16],
                'name': name, 'client': client, 'date': dt, 'dateRaw': f.get('招采时间', ''), 'year': fiscal_year(dt, src['sheet'][:4]),
                'region': f.get('项目地点', ''), 'business': business_type(name, f.get('项目类型')), 'result': '中标' if won else '未中标' if mark == '否' else '待公布',
                'winner': f.get('中标单位', ''), 'amount': field(f, '项目金额', '项目金额\n（限价）', '中标金额'),
                'duration': duration, 'endAt': end, 'endEstimated': estimated, 'daysLeft': days,
                'expiring': bool(won and days is not None and 0 <= days <= 90),
                'url': url if re.match(r'https?://', url) else '', 'source': src, 'sources': [src], 'ours': our,
                'fields': {k: v for k, v in f.items() if v and k not in ('公司名称', '投递项目名称', '甲方名称', '中标单位', '是否中标')},
                'notes': note_fields(f), 'sourceFields': f.copy(), 'cellNotes': row['comments'].copy(), 'sourceRows': [source_record(row)]}
            old = projects[company].get(key)
            if old:
                primary, secondary = (p, old) if len(p['fields']) > len(old['fields']) else (old, p)
                primary['sources'] = old['sources'] + [src]
                primary['sourceRows'] = old['sourceRows'] + p['sourceRows']
                for field_key, value in secondary['sourceFields'].items():
                    if value and not primary['sourceFields'].get(field_key):
                        primary['sourceFields'][field_key] = value
                for field_key, value in secondary['fields'].items():
                    if value and not primary['fields'].get(field_key):
                        primary['fields'][field_key] = value
                merge_cell_notes(primary['cellNotes'], secondary.get('cellNotes'))
                if primary is p:
                    projects[company][key] = p
            elif not old:
                projects[company][key] = p
        # 公开采集已落地结果纳入名单；精确公司名称+结构化中标人才能判为中标。
        for obj in public:
            meta = obj.raw_metadata or {}
            mapped = meta.get('mapped_fields') or meta.get('fields') or meta
            winner = clean(mapped.get('winner') or mapped.get('winning_supplier') or mapped.get('中标单位'))
            if not winner or not re.search(r'中标|成交|结果', obj.title or ''):
                continue
            for company in tracked:
                if normalized(company) not in normalized(winner):
                    continue
                title = clean(mapped.get('project_name')) or obj.title or ''
                client = clean(mapped.get('buyer') or mapped.get('purchaser') or mapped.get('甲方名称'))
                key = identity(title, client)
                if key in projects[company]:
                    continue
                projects[company][key] = {'id': 'public_' + obj.id, 'name': title, 'client': client,
                    'date': obj.published_at.astimezone(TZ).date().isoformat() if obj.published_at else '', 'dateRaw': '',
                    'region': clean(mapped.get('region')), 'business': clean(mapped.get('project_type')), 'result': '中标',
                    'winner': winner, 'amount': clean(mapped.get('winning_amount')), 'duration': clean(mapped.get('service_period')),
                    'endAt': iso(mapped.get('project_end_date')), 'endEstimated': False, 'daysLeft': None, 'expiring': False,
                    'url': obj.source_url or '', 'source': {'file': '公开结果公告', 'sheet': obj.source_id, 'row': ''}, 'ours': None,
                    'fields': {}, 'notes': {}}
        comps = []
        for company, ps in projects.items():
            vals = sorted(ps.values(), key=lambda p: p['date'], reverse=True)
            meta = entities.get(normalized(company), {})
            tyc = meta.get('tyc') or {}
            aliases = {'creditCode': 'credit_code', 'regStatus': 'reg_status', 'regCapital': 'reg_capital', 'actualController': 'actual_controller', 'riskSummary': 'risk_summary'}
            meta = {**meta, **{aliases.get(k, k): v for k, v in tyc.items()}}
            profile = {k: v for k, v in meta.items() if v and k in ('credit_code', 'reg_status', 'reg_capital', 'industry', 'actual_controller', 'risk_summary', 'related_companies', 'shareholders')}
            comps.append({'id': 'firm_' + hashlib.sha1(company.encode()).hexdigest()[:12], 'name': company, 'projects': vals,
                'profile': profile, 'regions': dict(Counter(p['region'] for p in vals if p['region'])),
                'clients': dict(Counter(p['client'] for p in vals if p['client'])), 'businesses': dict(Counter(p['business'] for p in vals if p['business']))})
        if not compfile:
            notes.append('桌面资料库缺少主要竞争对手项目统计表')
        return comps, notes

    def public_snapshot(self):
        data = self.snapshot()
        data.pop('blindRecords', None)
        data['reconciliation'].pop('readRows', None)
        for b in data['bonds']:
            b.pop('_rows', None)
        return data

    def edit_blind_source(self, record_id, edits, own_mark=None):
        """只改已存在的原业务列，校验每处来源/原值后备份原子保存。"""
        from app.services.blind_workspace import DESKTOP, STATUSES
        with file_lock('/tmp/bidos_nas_sync.lock', blocking=False) as _lk:
            if not _lk.acquired:
                raise RuntimeError('正在同步，请稍后保存')
            self._cache = None
            row = next((r for r in self.snapshot()['blindRecords'] if r['id'] == record_id), None)
            if not row: raise ValueError('原表位置已变化，请刷新')
            changes = []
            for e in edits:
                key = str(e.get('field', '')); value = text_value(e.get('value'))
                if key in ('BidOS项目ID', 'BidOS回写时间') or key not in row['fieldSources']:
                    raise ValueError('该字段没有可写的原表列：' + key)
                if text_value(e.get('baseValue')) != row['fields'].get(key, ''):
                    raise ValueError(f'「{key}」已更新，请刷新后修改')
                if key in ('报名参与情况', '报名参与情况统计') and value not in STATUSES:
                    raise ValueError('请选择有效的参与状态')
                changes.append((row['fieldSources'][key], value))
            if own_mark is not None:
                if '是否中标' not in row['fieldSources']: raise ValueError('原汇总表没有对应的是否中标列')
                value = text_value(own_mark.get('value'))
                if value not in ('', '是', '否', '入库', '暂未公示', '流标', '废标', '重新招采'): raise ValueError('无效的是否中标标注')
                if text_value(own_mark.get('baseValue')) != row['ownMark']: raise ValueError('汇总是否中标已修改，请刷新')
                changes.append((row['fieldSources']['是否中标'], value))
            if not changes: return {'ok': True}
            books, temps, originals, expected = {}, {}, {}, {}
            live_fingerprints = {}
            try:
                for origin, value in changes:
                    ptr, field_key = origin['source'], origin['field']
                    target = DESKTOP / ptr['file']
                    if target not in books:
                        if (target.parent / ('~$' + target.name)).exists() or workbook_is_open(target):
                            raise RuntimeError('Excel 正本正在 WPS/Excel 中打开，关闭后再写入')
                        expected[target] = (target.stat().st_mtime_ns, target.stat().st_size)
                        originals[target] = target.read_bytes()
                        books[target] = openpyxl.load_workbook(target, rich_text=True)
                        live_fingerprints[target] = {source_key(r['source']): r['source']['fingerprint'] for r in read_tables(target, lambda s: True)}
                    ws = books[target][ptr['sheet']]
                    live = [text_value(ws.cell(ptr['row'], c).value) for c in range(1, ptr['width'] + 1)]
                    # 同一行多格修改只需在第一次写入前验指纹。
                    check_key = (target, ptr['sheet'], ptr['row'])
                    if check_key not in temps:
                        if live_fingerprints[target].get(source_key(ptr)) != ptr['fingerprint']:
                            raise RuntimeError('原表记录已更新，请刷新后重试')
                        temps[check_key] = True
                    col = ptr['columns'].get(field_key)
                    if not col: raise ValueError('原表没有该列：' + field_key)
                    if ws.cell(ptr['row'], col).data_type == 'f': raise ValueError('这是原表公式，请修改对应输入值')
                    ws.cell(ptr['row'], col).value = value or None
                prepared = {}
                for target, wb in books.items():
                    tmp = target.with_name('.' + target.stem + '.bidos-tmp-' + uuid4().hex + '.xlsx')
                    prepared[target] = tmp; wb.save(tmp)
                    preserve_formula_caches(target, tmp)
                for target in books:
                    if expected[target] != (target.stat().st_mtime_ns, target.stat().st_size) or (target.parent / ('~$' + target.name)).exists():
                        raise RuntimeError('保存期间Excel有变更，已保留原文件，请刷新')
                backup_dir = ROOT / 'data/portfolio_excel_backups'; backup_dir.mkdir(parents=True, exist_ok=True)
                for target in books:
                    shutil.copy2(target, backup_dir / (target.stem + '.' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '.' + uuid4().hex[:6] + '.xlsx'))
                applied = []
                try:
                    for target, tmp in prepared.items():
                        os.replace(tmp, target); applied.append(target)
                except Exception:
                    for target in applied: target.write_bytes(originals[target])
                    raise
                self._cache = None
                return {'ok': True}
            finally:
                for wb in books.values(): wb.close()
                for tmp in locals().get('prepared', {}).values(): tmp.unlink(missing_ok=True)

    def refund(self, record_id, refunded_at, note, fingerprints):
        try:
            chosen = date.fromisoformat(refunded_at)
        except (ValueError, TypeError):
            raise ValueError('请选择有效的退回日期')
        if chosen > datetime.now(TZ).date():
            raise ValueError('已退回日期不能晚于今天')
        if len(note or '') > 2000:
            raise ValueError('备注请控制在2000字以内')
        target = workbook_path('*投标项目汇总*.xlsx')
        with file_lock('/tmp/bidos_nas_sync.lock', blocking=False) as _lk:
            if not _lk.acquired:
                raise RuntimeError('Excel正在同步，请稍后再保存')
            self._cache = None
            bond = next((b for b in self.snapshot()['bonds'] if b['id'] == record_id), None)
            if not bond:
                raise ValueError('台账位置已改变，请刷新后再操作')
            if bond['status'] in ('无需退回', '未缴纳'):
                raise ValueError('该笔保证金不属于待退款项')
            if [p['fingerprint'] for p in bond['sources']] != fingerprints:
                raise RuntimeError('这笔记录刚被修改，请刷新后确认最新内容')
            if (target.parent / ('~$' + target.name)).exists():
                raise RuntimeError('Excel正在打开，请关闭该文件后保存')
            stamp = (target.stat().st_mtime_ns, target.stat().st_size)
            wb = openpyxl.load_workbook(target)
            backup_dir = ROOT / 'data/portfolio_excel_backups'
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / (target.stem + '.' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '.' + uuid4().hex[:6] + '.xlsx')
            tmp = target.with_name('.' + target.stem + '.bidos-tmp-' + uuid4().hex + '.xlsx')
            try:
                for r in bond['_rows']:
                    ptr, cols = r['source'], r['columns']
                    ws = wb[ptr['sheet']]
                    c = cols.get('退回情况')
                    if not c:
                        raise RuntimeError('原表缺少退回情况列，未保存')
                    old = clean(ws.cell(ptr['row'], c).value)
                    # 原表无独立实际退回日期列；状态单元格记录日期，保留之前的业务备注。
                    status = f'已退回（{chosen.isoformat()}）'
                    remark_col = cols.get('备注')
                    if old and old not in ('已退回', '已缴纳', '财务已确认缴纳') and not old.startswith('已退回（'):
                        status += '；原记录：' + old
                    if note and not remark_col:
                        status += '；备注：' + note.strip()
                    ws.cell(ptr['row'], c).value = status
                    if remark_col and note:
                        prior = clean(ws.cell(ptr['row'], remark_col).value)
                        ws.cell(ptr['row'], remark_col).value = (prior + '\n' if prior else '') + note.strip()
                    actual_col = cols.get('实际退回日期') or cols.get('实际退回时间')
                    if actual_col:
                        ws.cell(ptr['row'], actual_col).value = chosen.isoformat()
                shutil.copy2(target, backup)
                wb.save(tmp)
                preserve_formula_caches(target, tmp)
                if (target.parent / ('~$' + target.name)).exists() or (target.stat().st_mtime_ns, target.stat().st_size) != stamp:
                    raise RuntimeError('保存期间Excel发生变化，已保留原文件，请刷新重试')
                check = openpyxl.load_workbook(tmp, read_only=True, data_only=True)
                try:
                    for r in bond['_rows']:
                        if chosen.isoformat() not in str(check[r['source']['sheet']].cell(r['source']['row'], r['columns']['退回情况']).value):
                            raise RuntimeError('Excel回写核对失败，未替换原文件')
                finally:
                    check.close()
                os.replace(tmp, target)
            finally:
                wb.close()
                if tmp.exists():
                    tmp.unlink()
            self._cache = None
            return {'ok': True, 'message': '已退回，桌面Excel已同步', 'refundedAt': chosen.isoformat(), 'backup': backup.name}
