"""Check every registry entry against its configured official latest source.

This is a live-network smoke check. It deliberately does not replay a frozen
device snapshot. Device state should come from ``ancli list --json`` on the
target phone, otherwise old installed versions can look like current data.

Run from the repository root with::

    python scripts/smoke_update_check.py
"""
import importlib.util
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_PATH = os.path.join(ROOT, "src", "ancli-core.py")
REGISTRY_PATH = os.path.join(ROOT, "src", "registry.json")

spec = importlib.util.spec_from_file_location("ancli_core", CORE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load AnCLI core from {CORE_PATH}")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

with open(REGISTRY_PATH, encoding="utf-8") as fh:
    registry = json.load(fh)

failed = False
print("== official latest versions (live network) ==")
for app_id, app in registry["apps"].items():
    version, source, error = core._latest_version(app, registry)
    suffix = f"  ERROR: {error}" if error else ""
    print(f"  {app_id:12} -> {str(version):10} [{source}]{suffix}")
    failed = failed or bool(error)

sys.exit(1 if failed else 0)
