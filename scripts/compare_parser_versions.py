#!/usr/bin/env python
"""在**同一份缓存正文**上对比「新规则开/关」的抽取结果（只读）。

为什么不能拿库里的现值当"改前"：库里字段是**联网抓取的正文**算出来的，而重算用的是
采集时缓存下来的正文 —— 同一 URL 两次抓取排版不同（四川站实测），差值是"抓取抖动"
不是"解析器改动"。只有固定正文、只切换规则，才能把改动效果单独量出来。

用法：
    ./.venv311/bin/python scripts/compare_parser_versions.py [--measure v11|v12] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

WATCH = ('项目名称', '甲方名称', '甲方类型', '代理机构', '项目区域', '项目类型', '所属行业',
         '招标金额', '报名截止时间', '开标时间', '服务期限', '报名费用', '项目情况简述',
         '项目编号', '采购方式', '资质要求', '业绩要求', '基本要求', '其他要求', '联合体投标',
         '投标保证金（元）', '中标服务费（元）', '项目实施地点')

NEVER = re.compile(r'(?!)')


def _apply_baseline(td, measure):
    """把 ``measure`` 那一版**新增的规则**关掉，得到该版之前的抽取口径。

    返回 ``(撤销函数, 说明)``。撤销函数必须把被 monkey-patch 的模块级名恢复原样。
    """
    saved = {}

    def patch(name, value):
        saved[name] = getattr(td, name)
        setattr(td, name, value)

    if measure == 'v12':
        # VERSION 11→12 新增的六条：免费费用句式、行首费用标签行、采购方式句式、
        # 无冒号章节正文、按甲方名称推区域、机构名不能当地点（``_ORG_NAME_RE`` 两处闸）。
        patch('_FREE_FEE_RE', NEVER)
        patch('_FEE_LABEL_RE', NEVER)
        patch('_METHOD_SENTENCE_RE', NEVER)
        patch('_SECTION_BODY_RE', NEVER)
        patch('_ORG_NAME_RE', NEVER)
        patch('_region_from_org', lambda name: '')
        label = 'VERSION 11（=关掉 V12 新增规则）'
    elif measure == 'v11':
        # VERSION 10→11 新增的三条：中缝标题、短值字段上限、采购方式取值白名单。
        patch('_MID_HEADING_RE', NEVER)
        patch('_MID_SUBHEADING_RE', NEVER)
        patch('_FIELD_MAX', {})
        patch('_METHOD_OK_RE', re.compile(r''))  # 空模式 = 任何值都通过（等于没有白名单）
        label = 'VERSION 10（=关掉 V11 新增规则）'
    else:
        raise SystemExit(f'未知的 --measure：{measure}（可选 v11 / v12）')

    def undo():
        for name, value in saved.items():
            setattr(td, name, value)

    return undo, label


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', dest='json_out', default='')
    parser.add_argument('--measure', default='v12', choices=('v11', 'v12'),
                        help='要量哪一版的改动（把该版新增规则关掉当基线）')
    args = parser.parse_args()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.repositories.models import Project, SourcePointer
    from app.services import tender_details as td

    Session = sessionmaker(bind=create_engine(DSN))
    with Session() as s:
        projects = s.scalars(select(Project).where(Project.workflow_stage == '公告候选')).all()
        samples = []
        for p in projects:
            meta = p.metadata_ or {}
            detail = meta.get('tender_detail') or {}
            landing = meta.get('landing') or {}
            url = detail.get('sourceUrl') or landing.get('source_url') or ''
            pts = s.scalars(select(SourcePointer).where(SourcePointer.project_id == p.id)).all()
            cached = next((x.original_text for x in pts if x.source_url == url and x.original_text), '')
            if not cached:
                cached = max((x.original_text or '' for x in pts), key=len, default='')
            samples.append({
                'id': p.id, 'name': p.name, 'body': cached, 'url': url, 'src': detail.get('sourceName') or '',
                'published': landing.get('published_at') or '', 'cur': detail.get('fields') or {},
            })

    def run_new():
        return [td.extract_details(x['name'], x['body'], published=x['published'],
                                   url=x['url'], source_name=x['src'])['fields'] for x in samples]

    new = run_new()

    undo, baseline_label = _apply_baseline(td, args.measure)
    try:
        old = run_new()
    finally:
        undo()

    fills_o = {k: 0 for k in WATCH}; fills_n = {k: 0 for k in WATCH}
    long_o = {k: 0 for k in WATCH}; long_n = {k: 0 for k in WATCH}
    diffs = []
    for x, fo, fn in zip(samples, old, new):
        for k in WATCH:
            ov = str(fo.get(k) or '').strip(); nv = str(fn.get(k) or '').strip()
            if ov:
                fills_o[k] += 1; long_o[k] = max(long_o[k], len(ov))
            if nv:
                fills_n[k] += 1; long_n[k] = max(long_n[k], len(nv))
            if ov != nv:
                diffs.append({'id': x['id'], 'name': x['name'], 'field': k,
                              'old': ov[:400], 'new': nv[:400], 'old_len': len(ov), 'new_len': len(nv)})

    print(f'候选 {len(samples)} 条，**同一份缓存正文**；VERSION={td.VERSION}')
    print(f'基线 = {baseline_label}\n')
    print(f'{"字段":<14}{"基线":>6}{"现在":>6}{"净":>5}   {"旧最长":>6}{"新最长":>7}')
    for k in WATCH:
        print(f'{k:<14}{fills_o[k]:>6}{fills_n[k]:>6}{fills_n[k] - fills_o[k]:>+5}   '
              f'{long_o[k]:>6}{long_n[k]:>7}')
    print(f'\n字段差异 {len(diffs)} 处')
    lost = [d for d in diffs if d['old'] and not d['new']]
    shrink = [d for d in diffs if d['old'] and d['new'] and d['new_len'] < d['old_len']]
    grow = [d for d in diffs if d['old'] and d['new'] and d['new_len'] > d['old_len']]
    added = [d for d in diffs if not d['old'] and d['new']]
    print(f'  新增 {len(added)} / 变短 {len(shrink)} / 变长 {len(grow)} / 丢失 {len(lost)}')

    if lost:
        print('\n== 开关规则后会丢的值 ==')
        for d in lost:
            print(f"  [{d['field']}] {d['name'][:30]}\n      原 {d['old_len']} 字: {d['old'][:120]!r}")
    if grow:
        print('\n== 变长 ==')
        for d in grow:
            print(f"  [{d['field']}] {d['old_len']}→{d['new_len']} {d['name'][:26]}: {d['new'][:110]!r}")
    if added:
        print('\n== 新增 ==')
        for d in added:
            print(f"  [{d['field']}] {d['name'][:30]}: {d['new'][:110]!r}")
    if shrink:
        print('\n== 变短（前 25）==')
        for d in shrink[:25]:
            print(f"  [{d['field']}] {d['old_len']}→{d['new_len']} {d['name'][:26]}")
            print(f"      旧: {d['old'][:110]!r}\n      新: {d['new'][:110]!r}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(diffs, ensure_ascii=False, indent=1))
        print(f'\n明细 → {args.json_out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
