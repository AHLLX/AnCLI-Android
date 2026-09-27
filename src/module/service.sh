#!/system/bin/sh
# ============================================================
# AnCLI Boot Service
# Runs automatically after every boot via module framework
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"

# 1. Ensure DNS is configured (resolv.conf may be reset by system on reboot).
#    Prefer the real Android DNS servers (they follow the current network and any
#    VPN); only fall back to public resolvers. Hardcoding 8.8.8.8 used to break
#    name resolution entirely on networks where it is blocked/poisoned.
if [ -d "$ROOTFS/etc" ]; then
    # /etc/resolv.conf is a symlink in some ubuntu-base images: replace it with a
    # real file so the writes below land somewhere useful.
    [ -L "$ROOTFS/etc/resolv.conf" ] && rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    _dns_seen=""
    _dns_count=0
    for _dns_val in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" 223.5.5.5 1.1.1.1 8.8.8.8; do
        case "$_dns_val" in
            ''|0.*|*[!0-9.]*) continue ;;
        esac
        case " $_dns_seen " in
            *" $_dns_val "*) continue ;;
        esac
        echo "nameserver $_dns_val" >> "$ROOTFS/etc/resolv.conf"
        _dns_seen="$_dns_seen $_dns_val"
        _dns_count=$((_dns_count + 1))
        [ "$_dns_count" -ge 3 ] && break
    done
fi

# 2. Ensure proot binary is executable (cleanup tools may reset permissions)
[ -f "$ANCLI_DIR/bin/proot" ] && chmod 755 "$ANCLI_DIR/bin/proot"

# 3. Ensure ancli-core.py is executable
[ -f "$ANCLI_DIR/bin/ancli-core.py" ] && chmod 755 "$ANCLI_DIR/bin/ancli-core.py"

# 4. Fix ownership of AI agent credential directories.
#    agy (and other Go/Node agents) write auth tokens as root on first launch.
#    After a terminal restart, subsequent shell-user runs fail with "Permission denied"
#    reading those files. We hand ownership to the shell user (UID 2000) and keep the
#    modes owner-only (u+rwX,go-rwx) — root ignores DAC, other apps cannot read tokens.
for _conf in ".config" ".gemini" ".claude" ".local"; do
    _full="$ROOTFS/root/$_conf"
    if [ -d "$_full" ]; then
        chown -R 2000:2000 "$_full" 2>/dev/null || true
        chmod -R u+rwX,go-rwx "$_full" 2>/dev/null || true
    fi
done

# 5. Ensure the hosts file for proot isolation exists and is readable
if [ ! -f "$ANCLI_DIR/hosts" ]; then
    printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n' \
        > "$ANCLI_DIR/hosts"
fi
chmod 644 "$ANCLI_DIR/hosts" 2>/dev/null || true

# 6. Sync app wrappers from ANCLI_DIR/bin/ to KSU/AP instant-access bin on every boot.
#    This ensures tools installed via 'ancli install' are globally reachable without
#    a module reinstall, and that wrapper updates take effect after reboot.
#    NOTE: git/bash/curl are host-side *shims* deployed by 'ancli repair' for
#    native-mode tools; they must NOT be copied into the root PATH, where they would
#    hijack those commands for every module and root script on the device.
for INSTANT_BIN in /data/adb/ksu/bin /data/adb/ap/bin; do
    [ -d "$INSTANT_BIN" ] || continue
    for wrapper in "$ANCLI_DIR/bin/"*; do
        name="${wrapper##*/}"
        # Skip core infrastructure files and host-side shims — only sync app wrappers
        case "$name" in
            proot|ancli-core.py|ancli|ancli_env.sh|registry.json|installed.json|hosts) continue ;;
            git|bash|curl) continue ;;
            .*) continue ;;
        esac
        [ -f "$wrapper" ] || continue
        cp -f "$wrapper" "$INSTANT_BIN/$name" 2>/dev/null || true
    done
    # Remove shims an older AnCLI version may have copied there (they shadow the
    # real commands for every root shell until removed).
    for shim in git bash curl; do
        if [ -f "$INSTANT_BIN/$shim" ] && head -n 2 "$INSTANT_BIN/$shim" 2>/dev/null | grep -q "AnCLI proot shim"; then
            rm -f "$INSTANT_BIN/$shim" 2>/dev/null || true
        fi
    done
done
