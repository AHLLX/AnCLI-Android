#!/system/bin/sh
# ============================================================
# AnCLI Module Uninstaller
# Auto-triggered by Magisk/KSU/APatch Manager on module removal
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"

# Kill proot instances that use the AnCLI rootfs.
# Using pkill -f avoids killing unrelated proot processes (e.g. Termux-proot).
pkill -f "proot.*${ANCLI_DIR}/rootfs" 2>/dev/null || \
    pkill -f "proot.*ancli" 2>/dev/null || true

# Determine if we should preserve the container and config files
# Default to KEEP (KEEP_DATA=1) unless the user explicitly requested a force purge
KEEP_DATA=1
if [ -f "/data/local/tmp/ancli_force_purge" ]; then
    KEEP_DATA=0
fi

if [ "$KEEP_DATA" -eq 1 ]; then
    # Only clean up the dynamic host-side executable components, keeping rootfs and installed.json database intact
    rm -rf "$ANCLI_DIR/bin"
else
    # Fully delete everything
    rm -rf "$ANCLI_DIR"
fi

# Clean KSU/AP dynamic wrappers that reference ancli
for BIN_DIR in /data/adb/ksu/bin /data/adb/ap/bin; do
    if [ -d "$BIN_DIR" ]; then
        grep -rl "$ANCLI_DIR" "$BIN_DIR" 2>/dev/null | xargs rm -f 2>/dev/null || true
    fi
done
