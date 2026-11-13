#!/system/bin/sh
# ============================================================
# AnCLI Module Build Script
# Packages src/module/ into a flashable ZIP
# ============================================================
# Usage (from repo root):
#   sh build.sh              → outputs ancli-<versionCode>-<version>.zip
#   sh build.sh custom-name  → outputs custom-name.zip

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULE_DIR="$SCRIPT_DIR/src/module"
MODULE_ID=$(grep '^id=' "$MODULE_DIR/module.prop" | cut -d= -f2)
VERSION=$(grep '^version=' "$MODULE_DIR/module.prop" | cut -d= -f2)
VERSION_CODE=$(grep '^versionCode=' "$MODULE_DIR/module.prop" | cut -d= -f2)
OUTPUT="${1:-${MODULE_ID}-${VERSION_CODE}-${VERSION}.zip}"

# Sync latest source files into module package
echo "> Syncing source files..."
mkdir -p "$MODULE_DIR/ancli"
cp "$SCRIPT_DIR/src/ancli-core.py" "$MODULE_DIR/ancli/ancli-core.py"
cp "$SCRIPT_DIR/src/registry.json" "$MODULE_DIR/ancli/registry.json"
cp "$SCRIPT_DIR/src/module/ancli_env.sh" "$MODULE_DIR/ancli/ancli_env.sh"
cp "$SCRIPT_DIR/src/bash-host-compat" "$MODULE_DIR/ancli/bash-host-compat"
mkdir -p "$MODULE_DIR/ancli/runtime-context"
cp "$SCRIPT_DIR/src/runtime-context/AGENTS.md" "$MODULE_DIR/ancli/runtime-context/AGENTS.md"

# Build ZIP
echo "> Building module ZIP..."
cd "$MODULE_DIR"
zip -r9 "$SCRIPT_DIR/$OUTPUT" . \
    -x '*.DS_Store' -x '*__MACOSX*' -x '*.git*'
cd "$SCRIPT_DIR"

echo ""
echo "[OK] Module built: $OUTPUT"
echo "     Flash via Magisk/KSU/APatch Manager."
