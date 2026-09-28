"""Build the flashable module ZIP without requiring `zip` (Windows-friendly).

Equivalent to build.sh: sync the three generated sources into src/module/ancli/,
then archive src/module/ with the module root at the ZIP root. Entries carry mode
0755, matching every previous release (verified against the published v1.2.3
asset, whose entries are all 0755).

Usage: python scripts/build_module.py [output-name.zip]
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(ROOT, "src", "module")
SYNC = [
    ("src/ancli-core.py", "ancli/ancli-core.py"),
    ("src/registry.json", "ancli/registry.json"),
    ("src/module/ancli_env.sh", "ancli/ancli_env.sh"),
]
SKIP_DIRS = {".git", "__MACOSX", "__pycache__"}
SKIP_FILES = {".DS_Store"}


def module_version():
    with open(os.path.join(MODULE_DIR, "module.prop"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("module.prop has no version=")


def main():
    version = module_version()
    output = sys.argv[1] if len(sys.argv) > 1 else f"ancli-{version}.zip"
    output_path = os.path.join(ROOT, output)

    print("> Syncing source files...")
    for source, target in SYNC:
        src = os.path.join(ROOT, source)
        dst = os.path.join(MODULE_DIR, target)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as fh:
            data = fh.read()
        with open(dst, "wb") as fh:
            fh.write(data)
        print(f"    {source} -> src/module/{target} ({len(data)} bytes)")

    print("> Building module ZIP...")
    entries = []
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for base, dirs, files in os.walk(MODULE_DIR):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                if name in SKIP_FILES or name.endswith(".pyc"):
                    continue
                full = os.path.join(base, name)
                rel = os.path.relpath(full, MODULE_DIR).replace(os.sep, "/")
                info = zipfile.ZipInfo(rel)
                # 0755 for every entry, exactly like the published releases.
                info.external_attr = (0o100755 << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    zf.writestr(info, fh.read())
                entries.append(rel)

    print(f"> Wrote {output} ({os.path.getsize(output_path)} bytes, {len(entries)} entries)")
    if "module.prop" not in entries:
        raise SystemExit("module.prop must sit at the ZIP root — refusing this build")
    for rel in sorted(entries):
        print(f"    {rel}")


if __name__ == "__main__":
    main()
