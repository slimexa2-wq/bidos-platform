from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = [
    "apps/web/src/services/dataProvider.ts",
    "apps/api/app/main.py",
    "apps/api/app/domain.py",
    "apps/api/app/services/event_engine.py",
    "apps/api/app/services/source_pointer.py",
    "apps/api/app/ingestion/pipeline.py",
    "infra/postgres/init/002_schema.sql",
    "docs/FEATURE_IMPLEMENTATION_MATRIX.md",
]
missing = [path for path in required if not (ROOT / path).exists()]
if missing:
    print("Missing:", *missing, sep="\n- ")
    sys.exit(1)

json.loads((ROOT / "apps/api/data/mock.json").read_text(encoding="utf-8"))
print("architecture check: ok")
