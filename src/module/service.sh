#!/system/bin/sh
# ============================================================
# AnCLI Boot Service
# Runs automatically after every boot via module framework
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"

# 1. Ensure DNS is configured (resolv.conf may be reset by system)
if [ -d "$ROOTFS/etc" ]; then
    echo "nameserver 8.8.8.8" > "$ROOTFS/etc/resolv.conf"
    echo "nameserver 1.1.1.1" >> "$ROOTFS/etc/resolv.conf"
fi

# 2. Ensure proot binary is executable (cleanup tools may reset permissions)
[ -f "$ANCLI_DIR/bin/proot" ] && chmod 755 "$ANCLI_DIR/bin/proot"

# 3. Ensure ancli-core.py is executable
[ -f "$ANCLI_DIR/bin/ancli-core.py" ] && chmod 755 "$ANCLI_DIR/bin/ancli-core.py"
