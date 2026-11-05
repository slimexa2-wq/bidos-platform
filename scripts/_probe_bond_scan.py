"""临时探针：dry-run 保证金提醒扫描（不写库），输出前后对比数字。

用法：./.venv311/bin/python scripts/_probe_bond_scan.py
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "api"))
os.environ.setdefault("BIDOS_DATA_MODE", "postgres")
os.environ.setdefault("DATABASE_URL", "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos")

from app.repositories.factory import build_repository  # noqa: E402
from app.services.event_scanner import EventScanner  # noqa: E402
from app.services.event_store import InMemoryEventStore  # noqa: E402

repo = build_repository()
rows = list(repo.bid_bonds())
print("台账行数:", len(rows))
store = InMemoryEventStore()
sc = EventScanner(store)
names = sc._resolve_project_names(None)
events, skipped = sc.scan_bid_bonds(rows, project_names=names)
gap = sc.bond_reminder_gap(rows, project_names=names)
print("产出事件:", len(events), dict(Counter(e.type.value for e in events)))
print("归类计数:", gap["counts"])
print("缺口说明:", gap["reason"])
print("跳过:", dict(Counter(str(s.get("reason")) for s in skipped)))
print("样例事件标题:")
for e in events[:10]:
    print("   -", e.type.value, "|", e.severity, "|", e.title)
