#!/usr/bin/env python
"""对比「公告候选」字段填充率（改前 / 改后）与污染面，用于验收提取器改动。

用法：
    ./.venv311/bin/python scripts/audit_candidate_fields.py [--before-json 路径]

不带 ``--before-json`` 时只打印当前填充率 + 可疑值清单。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
CANDIDATE_STAGE = "公告候选"

#: 2026-10-10 本轮改动**之前**的填充数（用于算净增，来自 audit 输出，手抄）。
BEFORE = {
    '项目类型': 43, '项目编号': 34, '甲方名称': 27, '报名截止时间': 26, '采购方式': 25,
    '资质要求': 24, '代理机构': 22, '项目区域': 18, '服务期限': 17, '招标金额': 16,
    '项目实施地点': 15, '项目情况简述': 14, '联合体投标': 9, '开标时间': 8,
    '业绩要求': 4, '报名费用': 4, '所属行业': 3, '其他要求': 2, '基本要求': 2,
    '甲方类型': 0, '投标保证金（元）': 0, '中标服务费（元）': 0,
}

#: 明显是"抓坏了"的形态：整句粘贴 / 表头串 / 时间当编号。
SUSPECT = re.compile(r'\d{2}:\d{2}$|招标人为|本项目已具备|进行公开招标|正式会员|'
                     r'标包编号|登录后|点击登录')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--before-json', help='可选：改前导出的 {字段: 填充数} JSON')
    args = parser.parse_args()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.repositories.models import Project
    from app.services.tender_details import VERSION

    before = dict(BEFORE)
    if args.before_json:
        before = {k: int(v) for k, v in json.loads(Path(args.before_json).read_text()).items()}

    engine = create_engine(DSN)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        rows = session.scalars(select(Project).where(Project.workflow_stage == CANDIDATE_STAGE)).all()
        fills: Counter = Counter()
        samples: dict[str, str] = {}
        suspects: list[tuple[str, str, str]] = []
        unbalanced = 0
        for project in rows:
            fields = ((project.metadata_ or {}).get('tender_detail') or {}).get('fields') or {}
            for key, raw in fields.items():
                value = str(raw or '').strip()
                if not value:
                    continue
                fills[key] += 1
                samples.setdefault(key, value[:44])
                if SUSPECT.search(value):
                    suspects.append((project.name[:26], key, value[:70]))
                for opener, closer in (('（', '）'), ('(', ')')):
                    if value.count(opener) != value.count(closer):
                        unbalanced += 1
                        break

    total = len(rows)
    print(f'公告候选 {total} 条；解析器 VERSION={VERSION}\n')
    print(f'{"字段":<16}{"改前":>5}{"改后":>5}{"净":>6}   样例')
    net = 0
    for key in sorted(set(before) | set(fills), key=lambda k: -fills.get(k, 0)):
        if key in ('项目名称', '招标时间', '招标公告网址'):
            continue
        old, new = before.get(key, 0), fills.get(key, 0)
        net += new - old
        print(f'{key:<16}{old:>5}{new:>5}{new - old:>+6}   {samples.get(key, "")}')
    print(f'\n净增 {net} 个字段值；括号不配对的值 {unbalanced} 处')
    if suspects:
        print(f'\n可疑值 {len(suspects)} 处：')
        for name, key, value in suspects:
            print(f'  {name:<28}{key:<14}{value!r}')
    else:
        print('\n无可疑值。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
