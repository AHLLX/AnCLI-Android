"""Local smoke harness: exercise update detection against the *real* upstreams.

Not part of the pytest suite (it needs network access); run it manually from the
repo root to sanity-check the registry's `latest` sources and to see the exact
JSON payload the WebUI consumes:

    python scripts/smoke_update_check.py

Part 1 resolves every registry app's official latest version over the network.
Part 2 replays the device state seen on 2026-09-27 (legacy install records that
held the AnCLI version) and prints the WebUI `list --json` verdict.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("ancli_core", os.path.join(ROOT, "src", "ancli-core.py"))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

with open(os.path.join(ROOT, "src", "registry.json"), encoding="utf-8") as f:
    registry = json.load(f)

print("== 1. official latest versions (live network) ==")
live = {}
for aid, app in registry["apps"].items():
    ver, source, err = core._latest_version(app, registry)
    live[aid] = {"version": ver, "source": source, "error": err}
    print(f"  {aid:12} -> {str(ver):10} [{source}]{'  ERR: ' + err if err else ''}")

print()
print("== 2. WebUI payload simulation (installed records as of 2026-09-27) ==")
tmp = tempfile.mkdtemp(prefix="ancli-smoke-")
core.ANCLI_DIR = os.path.join(tmp, "ancli")
core.ROOTFS = os.path.join(tmp, "rootfs")
core.UPDATE_CACHE = os.path.join(core.ANCLI_DIR, ".update_cache.json")
core.INSTALLED_FILE = os.path.join(core.ANCLI_DIR, "installed.json")
core.LOCAL_REGISTRY = os.path.join(tmp, "registry.json")
shutil.copy(os.path.join(ROOT, "src", "registry.json"), core.LOCAL_REGISTRY)
os.makedirs(os.path.join(core.ROOTFS, "usr", "local", "bin"), exist_ok=True)
os.makedirs(os.path.join(core.ANCLI_DIR, "bin"), exist_ok=True)

# Real installed versions on the device (probed by `version_cmd`)...
probes = {"mimo": "0.1.10", "agy": "1.1.27", "claude": "2.1.226", "grok": "1.0.0"}
# ...and the polluted records the old code wrote (the AnCLI framework version).
core.save_installed({
    "mimo": {"name": "MiMo Code", "executable": "mimo", "installed_version": "1.2.2", "env": {}},
    "agy": {"name": "Antigravity CLI", "executable": "agy", "installed_version": "1.2.2", "env": {}},
    "claude-code": {"name": "Claude Code", "executable": "claude", "installed_version": "2.1.226", "env": {}},
    "grok": {"name": "Grok CLI", "executable": "grok", "installed_version": "1.2.2", "env": {}},
})
for exe in probes:
    open(os.path.join(core.ROOTFS, "usr", "local", "bin", exe), "w").close()

core._probe_installed_version = lambda app, reg=None: probes.get(app.get("executable"))
core._save_update_cache({"ts": int(time.time()), "latest": live})
core.check_updates(registry)   # re-probes installed versions and refreshes `live`
core.list_apps_json()
