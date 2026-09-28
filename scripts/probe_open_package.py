"""Does the npm `open` package's bundled xdg-open reach our hand-off shim?

`open` prefers its own vendored freedesktop xdg-open over PATH lookups, so the
AnCLI shim is only reached when $BROWSER points at it.
"""
import os
import subprocess

QUEUE = "/data/local/tmp/ancli/.open_url"
BUNDLED = "/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/open/xdg-open"
SHIM = "/usr/local/bin/xdg-open"

BASE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
}


def run(label, extra_env, command):
    open(QUEUE, "w").close()
    env = dict(BASE_ENV)
    env.update(extra_env)
    try:
        proc = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=25)
        rc, out = proc.returncode, proc.stdout.decode(errors="replace")
    except subprocess.TimeoutExpired:
        rc, out = "TIMEOUT", ""
    queued = open(QUEUE).read().strip()
    tail = [line for line in out.strip().splitlines() if line.strip()][-2:]
    print(f"{label}: rc={rc} queued={queued!r} out_tail={tail}")


run("shim direct          ", {}, [SHIM, "https://example.com"])
run("bundled, no BROWSER  ", {}, [BUNDLED, "https://example.com"])
run("bundled, BROWSER=shim", {"BROWSER": SHIM}, [BUNDLED, "https://example.com"])
open(QUEUE, "w").close()
print("queue cleared")
