"""Device-side deploy helper: run INSIDE the AnCLI container (python3 -).

Copies the freshly pushed files into the live AnCLI tree and reports what the
KernelSU mount namespace allows. Used only during development verification.
"""
import os
import shutil
import sys

ANCLI = "/data/local/tmp/ancli"
STAGE = "/data/local/tmp"

jobs = [
    (f"{STAGE}/ancli-core.py.new", f"{ANCLI}/bin/ancli-core.py", 0o755),
    (f"{STAGE}/ancli-registry.new", f"{ANCLI}/registry.json", 0o644),
    (f"{STAGE}/ancli-registry.new", f"{ANCLI}/bin/registry.json", 0o644),
    (f"{STAGE}/ancli-env.sh.new", f"{ANCLI}/bin/ancli_env.sh", 0o755),
    # Module copies: what a reflash would deploy, so the on-device module stays
    # consistent with the running core (overwritten by the next flash anyway).
    (f"{STAGE}/ancli-core.py.new", "/data/adb/modules/ancli/ancli/ancli-core.py", 0o755),
    (f"{STAGE}/ancli-registry.new", "/data/adb/modules/ancli/ancli/registry.json", 0o644),
    (f"{STAGE}/ancli-env.sh.new", "/data/adb/modules/ancli/ancli/ancli_env.sh", 0o755),
]

for src, dst, mode in jobs:
    if not os.path.exists(src):
        print(f"SKIP  {dst}: {src} not staged")
        continue
    try:
        shutil.copyfile(src, dst)
        os.chmod(dst, mode)
        print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
    except Exception as e:
        print(f"FAIL  {dst}: {e}")

for stale in (".webui_last.log", ".webui_last.log.exit"):
    path = f"{ANCLI}/{stale}"
    if os.path.exists(path):
        try:
            os.remove(path)
            print(f"OK    removed {path}")
        except Exception as e:
            print(f"FAIL  remove {path}: {e}")

# Module directory: is it writable at all (mount-level read-only check)?
probe = "/data/adb/modules/ancli/.write_probe"
try:
    with open(probe, "w") as fh:
        fh.write("x")
    os.remove(probe)
    print("MODULE-DIR  writable")
except Exception as e:
    print(f"MODULE-DIR  read-only ({e})")

# Can the WebUI page be replaced from here?
webui_src = f"{STAGE}/ancli-webui.html.new"
webui_dst = "/data/adb/modules/ancli/webroot/index.html"
if os.path.exists(webui_src):
    try:
        shutil.copyfile(webui_src, webui_dst)
        print(f"OK    {webui_dst} replaced")
    except Exception as e:
        print(f"FAIL  {webui_dst}: {e}")

sys.exit(0)
