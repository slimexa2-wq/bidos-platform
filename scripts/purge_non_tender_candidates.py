#!/usr/bin/env python
"""摘除被误收进「公告候选」池的**非招标类**公告（2026-10-10）。

背景：用户反馈「收集的盲投新信息格式内容完全不对」。逐个开页面取证发现，
四川省公共资源交易中心的公告列表里混着多种公告类型，类型只写在**栏目路径**
与详情页**面包屑**里，标题完全看不出来（实测标题就叫「物业管理采购」）。
一次采到 72 条，其中 20 条根本不是待投标的新招标信息。

判定依据（逐条开页面取证，非猜测）：
  /jyxx/002002/002002004/  政府采购-签约履行   附件是「…服务合同.pdf」，已签约
  /jyxx/002002/002002005/  政府采购-终止公告   正文「符合条件的供应商不足三家」
  /jyxx/002002/002002006/  政府采购-履约验收   正文「一、合同编号…二、合同名称…」
  /jyxx/002001/002001012/  工程建设-招标文件   与 002001001「招标公告」同项目，重复

排除规则与采集侧**共用同一份实测清单**（``channel_strategies.SITE_EXCLUDE_PATH``），
不在这里另写一套判据 —— 否则明天采集又收回来。

纪律：
* 只动 ``workflow_stage='公告候选'`` 的行，已转正式项目/已进待评估的一律不碰；
* 落库前先把被删行的完整快照写到 ``data/backups/``，可回滚；
* 默认 ``--dry-run``，必须显式 ``--apply`` 才写库。

用法：
    ./.venv311/bin/python scripts/purge_non_tender_candidates.py            # 只看
    ./.venv311/bin/python scripts/purge_non_tender_candidates.py --apply    # 真删
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
CANDIDATE_STAGE = "公告候选"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='真正删库；不给则只打印')
    args = parser.parse_args()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.repositories.models import Project
    from app.services import channel_strategies as cs

    engine = create_engine(DSN)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        rows = session.scalars(select(Project).where(Project.workflow_stage == CANDIDATE_STAGE)).all()
        doomed = []
        for project in rows:
            landing = (project.metadata_ or {}).get('landing') or {}
            url = ((project.metadata_ or {}).get('tender_detail') or {}).get('sourceUrl') or landing.get('source_url') or ''
            excluded = cs.excluded_paths(url)
            if excluded and urlparse(url).path.startswith(excluded):
                doomed.append((project.id, project.name, url))

    print(f'候选池 {len(rows)} 条；命中非招标类栏目 {len(doomed)} 条')
    for pid, name, url in doomed:
        print(f'  {urlparse(url).path.split("/")[3] if len(urlparse(url).path.split("/")) > 3 else "?":<10} | {name[:52]}')
    if not doomed:
        print('无需清理。')
        return 0
    if not args.apply:
        print('\n--dry-run：没有写库。加 --apply 才会删。')
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = ROOT / 'data' / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f'purged_candidates_{stamp}.json'
    backup.write_text(json.dumps([
        {'id': pid, 'name': name, 'url': url,
         'metadata': next((p.metadata_ for p in rows if p.id == pid), {})}
        for pid, name, url in doomed
    ], ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已备份 {len(doomed)} 条 → {backup}')

    with Session() as session:
        deleted = 0
        for pid, _name, _url in doomed:
            project = session.get(Project, pid)
            if project is not None:
                session.delete(project)
                deleted += 1
        session.commit()
    print(f'已删除 {deleted} 条非招标类候选。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
