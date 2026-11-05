"""非盲投项目的公告查证事实。只补展示缺口，不改 Excel 或我方中标结论。"""
from __future__ import annotations
from app.services.file_lock import file_lock

import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4


class PortfolioAnnouncementFacts:
    def __init__(self, path: Path | None = None):
        if path is None:
            from app.services.blind_workspace import ROOT
            path = ROOT / 'data/portfolio_announcement_facts.json'
        self.path = path

    def load(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    @staticmethod
    def identity(row):
        return [row.get('name') or '', row.get('client') or '', row.get('date') or '']

    def record(self, row, parsed):
        from app.services.result_announcement_backfill import ResultAnnouncementBackfill
        if not parsed.reliable or not parsed.source_url or not parsed.result_date or not row.get('date'):
            return False
        if not ResultAnnouncementBackfill._title_matches_project(row['name'], parsed.source_title or ''):
            return False
        days = (parsed.result_date - date.fromisoformat(row['date'])).days
        if not 0 <= days <= 120:
            return False
        raw_amount = '；'.join(line.get('raw', '') for line in parsed.amount_lines if line.get('raw'))
        if not raw_amount and parsed.amount_yuan is not None:
            raw_amount = f'{parsed.amount_yuan:g}元'
        fact = {'identity': self.identity(row), 'winner': '、'.join(parsed.winners), 'amount': raw_amount,
                'publishedAt': parsed.result_date.isoformat(), 'url': parsed.source_url,
                'title': parsed.source_title, 'terminal': parsed.terminal or ''}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(str(self.path) + '.lock'):
            data = self.load()
            previous = data.get(row['id']) or {}
            if previous.get('publishedAt', '') > fact['publishedAt']:
                return False
            if all(previous.get(k) == v for k, v in fact.items()):
                return False
            fact['checkedAt'] = datetime.now(timezone.utc).isoformat()
            data[row['id']] = fact
            tmp = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.replace(self.path)
        return True

    @staticmethod
    def _source_has_value(row, keys):
        fields = [row.get('sourceFields') or {}, row.get('relatedFields') or {}]
        fields.extend(source.get('fields') or {} for source in row.get('sourceRows') or [])
        # 原格“/”属于明确原值；自动补全不覆盖。
        return any(str(f.get(k) if f.get(k) is not None else '').strip() for f in fields for k in keys)

    def apply(self, rows):
        data = self.load()
        for row in rows:
            fact = data.get(row['id']) or {}
            if fact.get('identity') != self.identity(row):
                continue
            for key, source_key, source_headers in (
                ('winner', 'winner', ('中标单位',)),
                ('amount', 'amount', ('中标金额（万元）', '中标金额', '中标价格')),
                ('publishedAt', 'publishedAt', ('结果公布日期', '结果公布时间', '中标公示时间')),
                ('resultUrl', 'url', ('结果公告网址',)),
            ):
                if not row.get(key) and fact.get(source_key) and not self._source_has_value(row, source_headers):
                    row[key] = fact[source_key]
            row['announcementEvidence'] = fact
        return rows
