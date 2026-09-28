"""One-line state probe: is dsh's node alive, is the port listening?"""
import os

PORT = 3087


def listening(port):
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(path).read().splitlines()[1:]:
                parts = line.split()
                if parts[3] == "0A" and int(parts[1].split(":")[1], 16) == port:
                    return True
        except OSError:
            pass
    return False


nodes = []
shells = 0
for pid in (p for p in os.listdir("/proc") if p.isdigit()):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\x00", b" ").decode(errors="replace")
        rss = ""
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS"):
                rss = line.split()[1] + "kB"
                break
    except OSError:
        continue
    if cmd.startswith("node ") and "dsh" in cmd:
        nodes.append((pid, rss))
    if "dsh web" in cmd:
        shells += 1

print(f"node={nodes or 'none'} wrappers={shells} port{PORT}={'LISTEN' if listening(PORT) else 'closed'}")
