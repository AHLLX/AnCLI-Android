"""Cut a release: fold the CHANGELOG's Unreleased sections into one section and
move every version number together.

Usage:
    python scripts/prepare_release.py                # re-sync to the current version (no CHANGELOG fold)
    python scripts/prepare_release.py v1.2.5         # new version, versionCode = current + 1
    python scripts/prepare_release.py v1.2.5 11      # new version with an explicit versionCode

Why this script derives instead of carrying literals: the v1.2.4 cut left the
core's VERSION at 1.2.3 (the manager said v1.2.4 while `ancli --version` said
1.2.3), because the version lived in five places and only three were updated by
hand. Here every copy is written from one source:

    src/module/module.prop     version / versionCode   (managers read this)
    update.json                version / versionCode / zipUrl
    src/ancli-core.py          VERSION                  (`ancli --version`, --json payloads)
    src/registry.json          top-level "version"      (docs/bookkeeping; no code reads it)
    CHANGELOG.md               ## vX.Y.Z section
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROP = ROOT / "src" / "module" / "module.prop"
CORE = ROOT / "src" / "ancli-core.py"
REGISTRY = ROOT / "src" / "registry.json"
MANIFEST = ROOT / "update.json"
CHANGELOG = ROOT / "CHANGELOG.md"


def read(path):
    return path.read_text(encoding="utf-8")


def write(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


prop = read(PROP)
current_version = re.search(r"^version=(.+)$", prop, re.M).group(1).strip()
current_code = int(re.search(r"^versionCode=(\d+)$", prop, re.M).group(1))

VERSION = sys.argv[1].strip() if len(sys.argv) > 1 else current_version
if len(sys.argv) > 2:
    CODE = int(sys.argv[2])
elif VERSION != current_version:
    CODE = current_code + 1          # versionCode must always increase
else:
    CODE = current_code

if not re.fullmatch(r"v\d+\.\d+\.\d+", VERSION):
    raise SystemExit(f"version must look like v1.2.5, got {VERSION!r}")
if VERSION != current_version and CODE <= current_code:
    raise SystemExit(
        f"versionCode must increase: current {current_code}, requested {CODE}")

# 1) CHANGELOG: fold "## Unreleased — …" headings into one release section.
#    Skipped (not an error) when there is nothing unreleased to fold, so a plain
#    re-sync run stays usable.
text = read(CHANGELOG)
count = len(re.findall(r"^## Unreleased — ", text, re.M))
if count:
    text = re.sub(r"^## Unreleased — (.+)$", r"### \1", text, flags=re.M)
    first = text.index("### ")
    text = text[:first] + f"## {VERSION}\n\n" + text[first:]
    write(CHANGELOG, text)
    print(f"CHANGELOG: folded {count} Unreleased section(s) into ## {VERSION}")
elif f"## {VERSION}" not in text:
    # A brand-new version with no CHANGELOG entry would ship silently; make that
    # explicit instead of pretending the release notes were updated.
    print(f"CHANGELOG: [warn] no Unreleased section and no '## {VERSION}' heading — "
          f"add release notes before publishing")
else:
    print(f"CHANGELOG: ## {VERSION} already present; left untouched")

# 2) module.prop
prop = re.sub(r"^version=.*$", f"version={VERSION}", prop, flags=re.M)
prop = re.sub(r"^versionCode=.*$", f"versionCode={CODE}", prop, flags=re.M)
write(PROP, prop)
print("module.prop:", [line for line in prop.splitlines()
                       if line.startswith(("version", "versionCode"))])

# 3) update.json (zipUrl must name the exact release asset)
manifest = json.loads(read(MANIFEST))
manifest["version"] = VERSION
manifest["versionCode"] = CODE
manifest["zipUrl"] = (f"https://github.com/AHLLX/AnCLI-Android/releases/download/"
                      f"{VERSION}/ancli-{VERSION}.zip")
write(MANIFEST, json.dumps(manifest, indent=2) + "\n")
print("update.json:", {k: manifest[k] for k in ("version", "versionCode", "zipUrl")})

# 4) src/ancli-core.py VERSION — the string `ancli --version` and the WebUI show.
core = read(CORE)
core, n = re.subn(r'^VERSION = ".*"$', f'VERSION = "{VERSION.lstrip("v")}"', core, flags=re.M)
if n != 1:
    raise SystemExit(f"expected exactly one VERSION = \"…\" line in {CORE.name}, found {n}")
write(CORE, core)
print(f"ancli-core.py: VERSION = \"{VERSION.lstrip('v')}\"")

# 5) src/registry.json top-level version (bookkeeping — keep it from drifting).
#    Edited textually, not through json.dumps: re-serialising the whole registry
#    would reflow every entry and bury the one-line change in noise.
registry_text = read(REGISTRY)
registry_text, n = re.subn(r'^(\s*"version"\s*:\s*")[^"]*(")',
                           rf"\g<1>{VERSION.lstrip('v')}\g<2>",
                           registry_text, count=1, flags=re.M)
if n != 1:
    raise SystemExit(f"could not find the top-level version in {REGISTRY.name}")
write(REGISTRY, registry_text)
print(f"registry.json: version = {VERSION.lstrip('v')}")

print(f"\n[OK] {VERSION} (versionCode {CODE}) is consistent across all five places.")
print("     Next: python scripts/build_module.py, then commit, tag, push, gh release create.")
