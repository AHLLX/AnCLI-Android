"""Verify the container's sha256sum -c --ignore-missing semantics and set up skills.

Read-only apart from a scratch dir under /tmp (removed at the end) plus the
shared skills directory that `ancli skills` is supposed to create.
"""
import hashlib
import os
import shutil
import subprocess
import sys

SCRATCH = "/tmp/ancli-sha-test"
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(SCRATCH, exist_ok=True)

payload = b"claude tarball payload\n"
target = os.path.join(SCRATCH, "claude-linux-arm64.tar.gz")
with open(target, "wb") as fh:
    fh.write(payload)
digest = hashlib.sha256(payload).hexdigest()
with open(os.path.join(SCRATCH, "SHASUMS256.txt"), "w") as fh:
    fh.write(f"{digest}  claude-linux-arm64.tar.gz\n")
    fh.write("deadbeef  claude-darwin-arm64.tar.gz\n")   # absent on purpose

def run(label):
    proc = subprocess.run("cd %s && sha256sum -c --ignore-missing SHASUMS256.txt" % SCRATCH,
                          shell=True, executable="/bin/bash",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode(errors="replace").strip().splitlines()
    print(f"{label}: rc={proc.returncode} out={out}")

run("intact ")
with open(target, "ab") as fh:
    fh.write(b"tampered")
run("tampered")

shutil.rmtree(SCRATCH, ignore_errors=True)

# Skills layout check (the real thing this release ships).
sys.path.insert(0, "/data/local/tmp/ancli/bin")
import importlib.util
spec = importlib.util.spec_from_file_location("core", "/data/local/tmp/ancli/bin/ancli-core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
result = core.setup_skill_dirs(verbose=False)
print("skills:", result)
for path in [core.SHARED_SKILL_DIR] + list(core.SKILL_LINK_DIRS):
    kind = "link->" + os.path.realpath(path) if os.path.islink(path) else ("dir" if os.path.isdir(path) else "MISSING")
    print(f"  {kind:45} {path}")
