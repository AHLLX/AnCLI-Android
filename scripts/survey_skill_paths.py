"""Survey skill support of the installed CLIs from INSIDE the AnCLI container.

Read-only: greps bundled docs/config for "skill" and scans each tool binary for
literal skill path patterns. Run with: adb shell su -c '... python3 -' < file
"""
import os
import re
import sys

HOME = "/root"

DOCS = [
    f"{HOME}/.grok/README.md",
    f"{HOME}/.grok/CHANGELOG.md",
    f"{HOME}/.claude/settings.json",
    f"{HOME}/.config/opencode/opencode.jsonc",
    f"{HOME}/.config/mimocode/mimocode.jsonc",
    f"{HOME}/.dsh/profiles/web/cordis.patch.yml",
]

CANDIDATE_DIRS = [
    f"{HOME}/.claude/skills",
    f"{HOME}/.config/opencode/skill",
    f"{HOME}/.config/opencode/skills",
    f"{HOME}/.config/mimocode/skill",
    f"{HOME}/.mimocode/skill",
    f"{HOME}/.agents/skills",
    f"{HOME}/.dsh/skills",
    f"{HOME}/.gemini/antigravity-cli/skills",
    f"{HOME}/.gemini/skills",
    f"{HOME}/.grok/skills",
    f"{HOME}/.config/grok/skills",
]

BINARIES = {
    "claude": "/usr/local/bin/claude",
    "mimo": "/usr/local/bin/mimo",
    "agy": "/usr/local/bin/agy",
    "grok": "/usr/local/bin/grok",
    "dsh": "/usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js",
}

PATH_RE = re.compile(
    rb"(?:[A-Za-z0-9._-]*/)?(?:\.claude|\.dsh|\.agents|\.config/[a-z]+|\.gemini|\.grok|\.mimocode)"
    rb"/[A-Za-z0-9._/-]*skills?[A-Za-z0-9._/-]*"
)
AGENTS_RE = re.compile(rb"(?:AGENTS\.md|\.agents/skills|skills?/[A-Za-z0-9._-]+/SKILL\.md)")

print("== candidate skill dirs ==")
for path in CANDIDATE_DIRS:
    print(f"  {'EXISTS ' if os.path.isdir(path) else 'missing'} {path}")

print("== bundled docs mentioning skill ==")
for path in DOCS:
    if not os.path.isfile(path):
        continue
    try:
        with open(path, "r", errors="replace") as handle:
            hits = [line.strip()[:160] for line in handle if "skill" in line.lower()]
    except OSError as exc:
        print(f"  {path}: unreadable ({exc})")
        continue
    for line in hits[:4]:
        print(f"  {os.path.relpath(path, HOME)}: {line}")
    if not hits:
        print(f"  {os.path.relpath(path, HOME)}: (no 'skill' text)")

print("== skill paths found inside the binaries ==")
for name, path in BINARIES.items():
    if not os.path.isfile(path):
        print(f"  {name}: binary not found at {path}")
        continue
    patterns = set()
    agents = set()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(8 << 20)
            if not chunk:
                break
            for match in PATH_RE.findall(chunk):
                patterns.add(match.decode("utf-8", "replace"))
            for match in AGENTS_RE.findall(chunk):
                agents.add(match.decode("utf-8", "replace"))
    sample = sorted(p for p in patterns if len(p) < 60)[:8]
    print(f"  {name}: {sample if sample else 'no skill path literal'}")
    print(f"       agents: {sorted(agents)[:6] if agents else 'none'}")
sys.exit(0)
