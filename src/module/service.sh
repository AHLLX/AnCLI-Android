#!/system/bin/sh
# ============================================================
# AnCLI Boot Service
# Runs automatically after every boot via module framework
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"

# 1. Ensure DNS is configured (resolv.conf may be reset by system on reboot).
#    Prefer the real DNS of the current network: getprop net.dns1/2 is empty on
#    modern Android, so the per-network DnsAddresses from `dumpsys connectivity`
#    (the same binder call ancli_env.sh uses for the proxy) is the real source;
#    only then fall back to China-friendly resolvers and finally public ones.
#    Hardcoding 8.8.8.8 first used to break name resolution on networks where it
#    is blocked or poisoned.
if [ -d "$ROOTFS/etc" ]; then
    # /etc/resolv.conf is a symlink in some ubuntu-base images: replace it with a
    # real file so the writes below land somewhere useful.
    [ -L "$ROOTFS/etc/resolv.conf" ] && rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    # Absolute paths: a service script's PATH is minimal by design.
    _dns_net=$(/system/bin/dumpsys connectivity 2>/dev/null | grep -o 'DnsAddresses: \[[^]]*\]' | head -n 1 \
        | sed 's/.*\[//; s/\]//' | tr ',' '\n' | sed 's#^ */##; s/ //g')
    _dns_seen=""
    _dns_count=0
    for _dns_val in "$(/system/bin/getprop net.dns1 2>/dev/null)" "$(/system/bin/getprop net.dns2 2>/dev/null)" \
                    $_dns_net 223.5.5.5 119.29.29.29 1.1.1.1 8.8.8.8; do
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
    # late_start can run before Wi-Fi/data finishes connecting, in which case only
    # the public fallbacks above are known. Keep watching for a few minutes in the
    # background and rewrite the file with the real resolvers once they appear
    # (service scripts must not block boot, hence the subshell).
    if [ -z "$_dns_net" ]; then
        (
            _tries=0
            while [ "$_tries" -lt 10 ]; do
                sleep 30
                _tries=$((_tries + 1))
                _dns_retry=$(/system/bin/dumpsys connectivity 2>/dev/null \
                    | grep -o 'DnsAddresses: \[[^]]*\]' | head -n 1 \
                    | tr ',' '\n' | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}' | head -n 3)
                [ -z "$_dns_retry" ] && continue
                : > "$ROOTFS/etc/resolv.conf"
                _written=0
                for _ip in $_dns_retry; do
                    if [ "$_written" -lt 3 ]; then
                        echo "nameserver $_ip" >> "$ROOTFS/etc/resolv.conf"
                        _written=$((_written + 1))
                    fi
                done
                [ "$_written" -lt 3 ] && echo "nameserver 223.5.5.5" >> "$ROOTFS/etc/resolv.conf"
                echo "AnCLI: container DNS updated to the network resolvers: $(echo $_dns_retry)"
                break
            done
        ) &
    fi
fi

# 2. Ensure proot binary is executable (cleanup tools may reset permissions)
[ -f "$ANCLI_DIR/bin/proot" ] && chmod 755 "$ANCLI_DIR/bin/proot"

# 3. Ensure ancli-core.py is executable
[ -f "$ANCLI_DIR/bin/ancli-core.py" ] && chmod 755 "$ANCLI_DIR/bin/ancli-core.py"

# 4. Fix ownership of AI agent credential/config directories.
#    agy (and other Go/Node agents) write auth tokens as root on first launch.
#    After a terminal restart, subsequent shell-user runs fail with "Permission denied"
#    reading those files. We hand ownership to the shell user (UID 2000) and keep the
#    modes owner-only (u+rwX,go-rwx) — root ignores DAC, other apps cannot read tokens.
#    .agents is where the shared skills live (all agent CLIs read it; ancli skills /
#    ancli repair creates the per-tool links).
for _conf in ".config" ".gemini" ".claude" ".local" ".agents" ".dsh" ".grok" ".mimocode"; do
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

# 7. Remove stale host-side shims from the module's own system/bin.
#    Older builds dropped git/bash/curl shims there; when the module overlay is
#    active they are mounted into /system/bin and shadow those commands for the
#    whole device (and they point at the ksu path step 6 no longer populates).
for shim in git bash curl; do
    mod_shim="${MODDIR:-/data/adb/modules/ancli}/system/bin/$shim"
    if [ -f "$mod_shim" ] && grep -qE 'AnCLI proot shim|exec /data/adb/(ksu|ap)/bin/' "$mod_shim" 2>/dev/null; then
        rm -f "$mod_shim" 2>/dev/null && echo "AnCLI: removed stale module shim $mod_shim"
    fi
done
