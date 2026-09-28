"""Prepare the v1.2.4 release: one CHANGELOG section + synced version numbers."""
import json
import re

VERSION = "v1.2.4"
CODE = 10

# 1) CHANGELOG: fold the six "Unreleased — …" sections into one release section.
path = "CHANGELOG.md"
text = open(path, encoding="utf-8").read()
count = len(re.findall(r"^## Unreleased — ", text, re.M))
assert count >= 1, "no Unreleased sections found"
text = re.sub(r"^## Unreleased — (.+)$", r"### \1", text, flags=re.M)
first = text.index("### ")
text = text[:first] + f"## {VERSION}\n\n" + text[first:]
open(path, "w", encoding="utf-8", newline="\n").write(text)
print(f"CHANGELOG: folded {count} Unreleased sections into ## {VERSION}")

# 2) module.prop
path = "src/module/module.prop"
prop = open(path, encoding="utf-8").read()
prop = re.sub(r"^version=.*$", f"version={VERSION}", prop, flags=re.M)
prop = re.sub(r"^versionCode=.*$", f"versionCode={CODE}", prop, flags=re.M)
open(path, "w", encoding="utf-8", newline="\n").write(prop)
print("module.prop:", [l for l in prop.splitlines() if l.startswith(("version", "versionCode"))])

# 3) update.json
path = "update.json"
manifest = json.load(open(path, encoding="utf-8"))
manifest["version"] = VERSION
manifest["versionCode"] = CODE
manifest["zipUrl"] = (f"https://github.com/AHLLX/AnCLI-Android/releases/download/"
                      f"{VERSION}/ancli-{VERSION}.zip")
open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(manifest, indent=2) + "\n")
print("update.json:", manifest)
