#!/usr/bin/env python3
"""
Unified Zero-Friction Capability Hub (`ultra_modules.hub`).
Wires all ~140 evolved `ultra_modules` in this repository into a single, intuitive entry point.
Philosophy: Complex backend, ultra-simple 1-call frontend.
"""

import argparse
import importlib
import inspect
import json
import os
import pkgutil
from typing import Any, Dict, List, Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def list_modules() -> List[Dict[str, Any]]:
    """Return metadata for all evolved modules in `ultra_modules`."""
    items: List[Dict[str, Any]] = []
    for fname in sorted(os.listdir(PACKAGE_DIR)):
        if not fname.endswith(".py") or fname in ("__init__.py", "hub.py"):
            continue
        mod_name = fname[:-3]
        fpath = os.path.join(PACKAGE_DIR, fname)
        doc_first_line = ""
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(1200)
            for line in content.splitlines():
                s = line.strip().strip('"""').strip("'''").strip("#").strip()
                if s and not s.startswith(("import ", "from ", "class ", "def ")):
                    doc_first_line = s[:100]
                    break
        except Exception:
            pass
        items.append({
            "module": mod_name,
            "summary": doc_first_line or mod_name,
        })
    return items


def load_module(mod_name: str):
    """Safely import `ultra_modules.<mod_name>`."""
    clean = mod_name.replace("ultra_modules.", "").replace(".py", "").strip()
    return importlib.import_module(f"ultra_modules.{clean}")


def invoke(mod_name: str, *args, **kwargs) -> Dict[str, Any]:
    """
    Zero-friction unified invoker: automatically locates the primary public function
    or class method in `ultra_modules.<mod_name>` and executes it with safe fallback.
    """
    try:
        mod = load_module(mod_name)
    except Exception as exc:
        return {"ok": False, "module": mod_name, "error": f"模块加载失败: {exc}"}

    # 1. Look for top-level public functions first
    funcs = [
        (name, obj)
        for name, obj in inspect.getmembers(mod, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == mod.__name__
    ]
    for preferred in ("run", "process", "execute", "analyze", "evaluate", "scan", "format_report", "bridge"):
        for name, fn in funcs:
            if preferred in name:
                try:
                    return {"ok": True, "module": mod_name, "entrypoint": name, "result": fn(*args, **kwargs)}
                except Exception as exc:
                    return {"ok": False, "module": mod_name, "entrypoint": name, "error": str(exc)}
    if funcs:
        name, fn = funcs[0]
        try:
            return {"ok": True, "module": mod_name, "entrypoint": name, "result": fn(*args, **kwargs)}
        except Exception as exc:
            return {"ok": False, "module": mod_name, "entrypoint": name, "error": str(exc)}

    # 2. Look for public classes and their main method
    classes = [
        (name, obj)
        for name, obj in inspect.getmembers(mod, inspect.isclass)
        if not name.startswith("_") and obj.__module__ == mod.__name__
    ]
    if classes:
        cls_name, cls_obj = classes[0]
        try:
            inst = cls_obj()
        except Exception:
            inst = cls_obj
        for mname, mobj in inspect.getmembers(inst):
            if not mname.startswith("_") and callable(mobj):
                try:
                    return {"ok": True, "module": mod_name, "entrypoint": f"{cls_name}.{mname}", "result": mobj(*args, **kwargs)}
                except Exception as exc:
                    return {"ok": False, "module": mod_name, "entrypoint": f"{cls_name}.{mname}", "error": str(exc)}

    return {"ok": True, "module": mod_name, "entrypoint": "module_loaded", "result": dir(mod)}


def main():
    parser = argparse.ArgumentParser(description="Ultra Modules 极简统一调用中枢")
    parser.add_argument("--list", action="store_true", help="列出当前仓库所有已集成的 Ultra 业务模块")
    parser.add_argument("--search", default=None, help="按关键词搜索模块")
    parser.add_argument("--inspect", default=None, help="查看指定模块的可用函数与类")
    args = parser.parse_args()

    mods = list_modules()
    if args.search:
        kw = args.search.lower()
        mods = [m for m in mods if kw in m["module"].lower() or kw in m["summary"].lower()]
    if args.inspect:
        mod = load_module(args.inspect)
        pub = [n for n in dir(mod) if not n.startswith("_")]
        print(json.dumps({"module": args.inspect, "exports": pub}, ensure_ascii=False, indent=2))
        return
    print(json.dumps({"total_modules": len(mods), "modules": mods}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
