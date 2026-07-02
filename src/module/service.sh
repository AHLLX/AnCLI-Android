#!/system/bin/sh
# ============================================================
# AnCLI Boot Service
# Runs automatically after every boot via module framework
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"

# 1. Ensure DNS is configured (resolv.conf may be reset by system on reboot)
if [ -d "$ROOTFS/etc" ]; then
    echo "nameserver 8.8.8.8"  > "$ROOTFS/etc/resolv.conf"
    echo "nameserver 1.1.1.1" >> "$ROOTFS/etc/resolv.conf"
fi

# 2. Ensure proot binary is executable (cleanup tools may reset permissions)
[ -f "$ANCLI_DIR/bin/proot" ] && chmod 755 "$ANCLI_DIR/bin/proot"

# 3. Ensure ancli-core.py is executable
[ -f "$ANCLI_DIR/bin/ancli-core.py" ] && chmod 755 "$ANCLI_DIR/bin/ancli-core.py"

# 4. Fix ownership of AI agent credential directories.
#    agy (and other Go/Node agents) write auth tokens as root on first launch.
#    After a terminal restart, subsequent shell-user runs fail with "Permission denied"
#    reading those files. We reset ownership to shell (UID 2000) on every boot so
#    both root and adb-shell sessions can always read/write credentials.
for _conf in ".config" ".gemini" ".claude" ".local"; do
    _full="$ROOTFS/root/$_conf"
    if [ -d "$_full" ]; then
        chown -R 2000:2000 "$_full" 2>/dev/null || true
        chmod -R 755      "$_full" 2>/dev/null || true
    fi
done

# 5. Ensure the hosts file for proot isolation exists and is readable
if [ ! -f "$ANCLI_DIR/hosts" ]; then
    printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n' \
        > "$ANCLI_DIR/hosts"
fi
chmod 644 "$ANCLI_DIR/hosts" 2>/dev/null || true
