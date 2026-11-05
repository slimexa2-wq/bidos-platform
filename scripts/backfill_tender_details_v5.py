#!/usr/bin/env python
"""把「公告候选」的公告字段按新版解析器重新读一遍（2026-10-10）。

背景：``tender_details.VERSION`` 从 4 升到 5，修了三个已复现的根因 ——
① 正文提纯把被 <span> 切碎的标签/值拆成两行，值一路吞到文末；
② 正文容器选不中退回整页，面包屑与页脚省市导航混进字段；
③ JSON 分支渠道把接口记录 dump 当正文，正则在 JSON 串上乱抓。

本脚本**只走生产同一条补取路径**（``TenderDetailsService.refresh_missing``），
不另写一套解析逻辑：这样回填结果与下一次 16:30 定时采集的结果口径完全一致。
只处理 ``workflow_stage='公告候选'`` 且落地 24 小时内的项目（与线上同一道闸），
已转正式项目的一律不碰。

用法：
    ./.venv311/bin/python scripts/backfill_tender_details_v5.py [--limit 200] [--dry-run]
    ./.venv311/bin/python scripts/backfill_tender_details_v5.py --offline   # 不联网，只用已缓存正文重算
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

WATCH = ('项目名称', '甲方名称', '甲方类型', '代理机构', '项目区域', '项目类型', '所属行业',
         '招标金额', '报名截止时间', '开标时间', '服务期限', '报名费用', '项目情况简述',
         '项目编号', '采购方式', '资质要求', '业绩要求', '基本要求', '其他要求', '联合体投标',
         '投标保证金（元）', '中标服务费（元）', '项目实施地点')


def snapshot(Session):
    """候选项目当前字段的填充计数与最长值长度（用于改前/改后对照）。"""
    from sqlalchemy import select
    from app.repositories.models import Project

    fills = {key: 0 for key in WATCH}
    longest = {key: 0 for key in WATCH}
    with Session() as session:
        rows = session.scalars(select(Project).where(Project.workflow_stage == '公告候选')).all()
        for project in rows:
            fields = ((project.metadata_ or {}).get('tender_detail') or {}).get('fields') or {}
            for key in WATCH:
                value = str(fields.get(key) or '').strip()
                if value:
                    fills[key] += 1
                    longest[key] = max(longest[key], len(value))
    return len(rows), fills, longest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--dry-run', action='store_true', help='只打印将要处理的条数，不写库')
    parser.add_argument('--offline', action='store_true',
                        help='不联网，只用已缓存正文重算 —— 只用于**对照**，正式回写请走联网')
    parser.add_argument('--force', action='store_true',
                        help='跳过 30 分钟/6 小时重试窗口立刻重算（同版本会保留旧非空值，不会丢数据）')
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.services.tender_details import TenderDetailsService, VERSION

    engine = create_engine(DSN)
    Session = sessionmaker(bind=engine)

    total, before, before_long = snapshot(Session)
    print(f'候选 {total} 条；解析器 VERSION={VERSION}；'
          f'模式={"离线（只用缓存正文）" if args.offline else "联网补取"}')
    if args.dry_run:
        print('--dry-run：不动库。')
        return 0

    reports = TenderDetailsService(Session).refresh_missing(
        catalog=[], limit=args.limit, offline=args.offline, force=args.force)
    print(f'本轮处理 {len(reports)} 条；状态分布：')
    tally: dict[str, int] = {}
    for item in reports:
        tally[item['status']] = tally.get(item['status'], 0) + 1
    for status, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f'   {status}: {count}')

    total2, after, after_long = snapshot(Session)
    print(f'\n{"字段":<12}{"改前":>5}{"改后":>5}{"净":>5}   {"旧最长":>6}{"新最长":>7}')
    for key in WATCH:
        print(f'{key:<12}{before[key]:>5}{after[key]:>5}{after[key] - before[key]:>+5}   '
              f'{before_long[key]:>6}{after_long[key]:>7}')
    print(f'\n候选仍有 {total2} 条（回填不新建、不删除项目）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
