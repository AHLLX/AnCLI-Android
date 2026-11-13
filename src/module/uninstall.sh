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
PURGE_MARKER="/data/local/tmp/ancli_force_purge"
if [ -f "$PURGE_MARKER" ]; then
    KEEP_DATA=0
    # The marker is a one-shot request. Do not let a later upgrade or removal
    # unexpectedly purge a newly created container.
    rm -f "$PURGE_MARKER"
fi

if [ "$KEEP_DATA" -eq 1 ]; then
    # Only clean up the dynamic host-side executable components, keeping rootfs and installed.json database intact
    rm -rf "$ANCLI_DIR/bin"
    # The AGY host bash compatibility file is generated beside bin/ so that
    # `ancli repair` can redeploy it; it is module-owned and safe to remove on
    # a normal uninstall. User-provided host bash files are handled below.
    rm -f "$ANCLI_DIR/bash-host-compat"
else
    # Fully delete everything
    rm -rf "$ANCLI_DIR"
fi

# Clean KSU/AP dynamic wrappers that reference ancli
for BIN_DIR in /data/adb/ksu/bin /data/adb/ap/bin; do
    if [ -d "$BIN_DIR" ]; then
        grep -rl "$ANCLI_DIR" "$BIN_DIR" 2>/dev/null | while IFS= read -r f; do
            rm -f "$f" 2>/dev/null || true
        done
        # xdg-open host helper deployed by 'ancli repair' — its content does
        # not reference ANCLI_DIR, so the grep above cannot catch it.
        rm -f "$BIN_DIR/xdg-open" 2>/dev/null || true
        # Remove only AnCLI's marked compatibility script; preserve a real
        # user-provided host bash binary.
        if [ -f "$BIN_DIR/bash" ] && head -n 8 "$BIN_DIR/bash" 2>/dev/null \
            | grep -q 'AnCLI AGY host bash compatibility'; then
            rm -f "$BIN_DIR/bash" 2>/dev/null || true
        fi
    fi
done
