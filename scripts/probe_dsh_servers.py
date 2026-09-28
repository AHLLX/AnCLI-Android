"""Which dsh web servers are alive right now, and on which ports?

Also probes each port: a bare GET / must answer 401 (that is the page the user
sees), and the port owner's age tells whether it is a stale instance from an
earlier run whose token no longer matches anything on screen.
"""
import os
import re
import time
import urllib.error
import urllib.request


def listeners():
    rows = []
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(path).read().splitlines()[1:]:
                parts = line.split()
                if parts[3] != "0A":
                    continue
                hexip, hexport = parts[1].split(":")
                rows.append((int(hexport, 16), parts[9]))
        except OSError:
            pass
    return rows


def pid_for(inode):
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                if os.readlink(f"/proc/{pid}/fd/{fd}") == f"socket:[{inode}]":
                    return int(pid)
        except OSError:
            continue
    return None


now = time.time()
print("== dsh web processes ==")
for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()), key=int):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\x00", b" ").decode(errors="replace").strip()
        age = now - os.path.getmtime(f"/proc/{pid}")
    except OSError:
        continue
    if "dsh web" in cmd:
        print(f"  pid={pid} age={age/60:.1f}min  {cmd[:100]}")

print("\n== loopback listeners ==")
for port, inode in sorted(set(listeners())):
    ip = "127.0.0.1" if port else "?"
    owner = pid_for(inode)
    detail = ""
    if 1024 < port < 65535:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=4) as resp:
                body = resp.read(80).decode(errors="replace").strip()
                detail = f"HTTP {resp.status} {body[:40]!r}"
        except urllib.error.HTTPError as exc:
            body = exc.read(80).decode(errors="replace").strip()
            detail = f"HTTP {exc.code} {body[:60]!r}"
        except Exception as exc:
            detail = f"probe failed: {type(exc).__name__}"
    print(f"  port={port:<6} owner_pid={owner} {detail}")
