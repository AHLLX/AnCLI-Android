"""Extract one version section from CHANGELOG.md for an immutable release.

Usage:
    python scripts/extract_release_notes.py v1.2.609 .release-notes.md

KernelSU Modules displays the Release notes as the version changelog. Passing
the complete CHANGELOG would make every release repeat all previous versions,
so the workflow extracts exactly one top-level section before publishing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"


def extract(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}(?:\s|$)", text, flags=re.MULTILINE)
    if not match:
        raise SystemExit(f"CHANGELOG.md has no top-level section for {version}")
    end = re.search(r"^## (?!" + re.escape(version) + r"(?:\s|$)).+", text[match.end():], flags=re.MULTILINE)
    section = text[match.start(): match.end() + end.start() if end else None]
    return section.rstrip() + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: extract_release_notes.py vX.Y.Z output.md")
    version, output = sys.argv[1:]
    Path(output).write_text(extract(version), encoding="utf-8", newline="\n")
    print(f"[OK] extracted {version} release notes -> {output}")


if __name__ == "__main__":
    main()
