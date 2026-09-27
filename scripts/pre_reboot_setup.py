"""Pre-reboot: back up the module's current service.sh, install the new one, snapshot state."""
import json
import os
import shutil
import subprocess

MODULE = "/data/adb/modules/ancli"
STAGE = "/data/local/tmp"
BACKUP = f"{STAGE}/ancli/service.sh.bak-pre-boot-test"

def sh(cmd):
    return subprocess.run(cmd, shell=True, executable="/bin/bash",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode(errors="replace").strip()

# 1. back up whatever is live
os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
if os.path.exists(f"{MODULE}/service.sh"):
    shutil.copyfile(f"{MODULE}/service.sh", BACKUP)
    print(f"backed up  {BACKUP} ({os.path.getsize(BACKUP)} bytes)")

# 2. deploy the new boot script + the module copies of the other scripts
for src, dst in [
    (f"{STAGE}/service.sh.new", f"{MODULE}/service.sh"),
    (f"{STAGE}/ancli-env.sh.new", f"{MODULE}/ancli_env.sh"),
    (f"{STAGE}/ancli-env.sh.new", f"{MODULE}/ancli/ancli_env.sh"),
]:
    if not os.path.exists(src):
        print(f"SKIP  {dst}: {src} not staged")
        continue
    try:
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o755)
        print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
    except Exception as e:
        print(f"FAIL  {dst}: {e}")

# 3. snapshot the pre-reboot state
print("\n== before reboot ==")
print("shims in ksu bin:", sh("ls /data/adb/ksu/bin/git /data/adb/ksu/bin/bash /data/adb/ksu/bin/curl 2>&1"))
for p in ("/data/adb/ksu/bin/bash", "/data/adb/ksu/bin/curl", "/data/adb/ksu/bin/git"):
    if os.path.exists(p):
        with open(p, "r", errors="replace") as fh:
            print(f"  {p}: {fh.readline().strip()} / {fh.readline().strip()}")
print("resolv.conf:", sh("cat /data/local/tmp/ancli/rootfs/etc/resolv.conf"))
print("dns props:", sh("getprop net.dns1"), "|", sh("getprop net.dns2"))
print("root config owners:", sh("ls -ld /data/local/tmp/ancli/rootfs/root/.agents /data/local/tmp/ancli/rootfs/root/.dsh /data/local/tmp/ancli/rootfs/root/.claude 2>&1"))
print("module files:", sh(f"ls {MODULE}"))
print("skills links:", sh("ls -l /data/local/tmp/ancli/rootfs/root/.claude/skills /data/local/tmp/ancli/rootfs/root/.dsh/skills 2>&1"))
