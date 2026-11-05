#!/usr/bin/env python
"""解析器升版后，用**已缓存正文**离线重算全部候选，与库中现值逐字段对照（只读，不写库）。

为什么需要它：联网重抓会换回排版不同的页面（实测四川站同一 URL 两次抓取换行结构不同），
字段结果因此抖动 —— 那没法判断改动到底有没有效。本脚本把「改动效果」与「抓取抖动」分离。

用法：
    ./.venv311/bin/python scripts/compare_tender_details.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

WATCH = ('项目名称', '甲方名称', '甲方类型', '代理机构', '项目区域', '项目类型', '所属行业',
         '招标金额', '报名截止时间', '开标时间', '服务期限', '报名费用', '项目情况简述',
         '项目编号', '采购方式', '资质要求', '业绩要求', '基本要求', '其他要求', '联合体投标',
         '投标保证金（元）', '中标服务费（元）', '项目实施地点')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', dest='json_out', default='')
    args = parser.parse_args()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.repositories.models import Project, SourcePointer
    from app.services.tender_details import extract_details, VERSION

    Session = sessionmaker(bind=create_engine(DSN))
    rows_dump = []
    fills_before = {k: 0 for k in WATCH}
    fills_after = {k: 0 for k in WATCH}
    longest_before = {k: 0 for k in WATCH}
    longest_after = {k: 0 for k in WATCH}
    diffs = []

    with Session() as s:
        projects = s.scalars(select(Project).where(Project.workflow_stage == '公告候选')).all()
        for p in projects:
            meta = p.metadata_ or {}
            detail = meta.get('tender_detail') or {}
            landing = meta.get('landing') or {}
            url = detail.get('sourceUrl') or landing.get('source_url') or ''
            pointers = s.scalars(select(SourcePointer).where(SourcePointer.project_id == p.id)).all()
            cached = next((x.original_text for x in pointers if x.source_url == url and x.original_text), '')
            if not cached:
                cached = max((x.original_text or '' for x in pointers), key=len, default='')
            new = extract_details(p.name, cached, published=landing.get('published_at') or '',
                                  url=url, source_name=detail.get('sourceName') or landing.get('channel_name') or '')
            old_fields = detail.get('fields') or {}
            new_fields = new['fields']
            for k in WATCH:
                ov = str(old_fields.get(k) or '').strip()
                nv = str(new_fields.get(k) or '').strip()
                if ov:
                    fills_before[k] += 1
                    longest_before[k] = max(longest_before[k], len(ov))
                if nv:
                    fills_after[k] += 1
                    longest_after[k] = max(longest_after[k], len(nv))
                if ov != nv:
                    diffs.append({'id': p.id, 'name': p.name, 'field': k,
                                  'old': ov[:300], 'new': nv[:300],
                                  'old_len': len(ov), 'new_len': len(nv)})
            rows_dump.append({'id': p.id, 'name': p.name, 'ver_old': detail.get('version'),
                              'ver_new': VERSION, 'filled_old': sum(bool(old_fields.get(k)) for k in WATCH),
                              'filled_new': sum(bool(new_fields.get(k)) for k in WATCH)})

    print(f'候选 {len(rows_dump)} 条；解析器 VERSION={VERSION}（离线重算，未写库）\n')
    print(f'{"字段":<14}{"改前":>5}{"改后":>5}{"净":>5}   {"旧最长":>6}{"新最长":>7}')
    for k in WATCH:
        print(f'{k:<14}{fills_before[k]:>5}{fills_after[k]:>5}{fills_after[k] - fills_before[k]:>+5}   '
              f'{longest_before[k]:>6}{longest_after[k]:>7}')
    print(f'\n字段差异 {len(diffs)} 处')

    lost = [d for d in diffs if d['old'] and not d['new']]
    shrink = [d for d in diffs if d['old'] and d['new'] and d['new_len'] < d['old_len']]
    grow = [d for d in diffs if d['old'] and d['new'] and d['new_len'] > d['old_len']]
    added = [d for d in diffs if not d['old'] and d['new']]
    print(f'  新增 {len(added)} / 变短(多为越界被修) {len(shrink)} / 变长 {len(grow)} / 丢失 {len(lost)}')

    if lost:
        print('\n== 会丢值（需人工确认是否误伤）==')
        for d in lost[:25]:
            print(f"  [{d['field']}] {d['name'][:26]}  ← {d['old'][:90]!r}")
    if grow:
        print('\n== 变长（确认不是越界）==')
        for d in grow[:20]:
            print(f"  [{d['field']}] {d['old_len']}→{d['new_len']}  {d['name'][:24]}  {d['new'][:90]!r}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({'diffs': diffs, 'rows': rows_dump},
                                                  ensure_ascii=False, indent=1))
        print(f'\n明细 → {args.json_out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
