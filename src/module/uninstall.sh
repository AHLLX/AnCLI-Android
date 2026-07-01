#!/system/bin/sh
# ============================================================
# AnCLI Module Uninstaller
# Auto-triggered by Magisk/KSU/APatch Manager on module removal
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"

# Kill running proot instances
killall proot 2>/dev/null || true

# Clean up rootfs and core files
rm -rf "$ANCLI_DIR"

# Clean KSU/AP dynamic wrappers that reference ancli
for BIN_DIR in /data/adb/ksu/bin /data/adb/ap/bin; do
    if [ -d "$BIN_DIR" ]; then
        grep -rl "$ANCLI_DIR" "$BIN_DIR" 2>/dev/null | xargs rm -f 2>/dev/null || true
    fi
done
