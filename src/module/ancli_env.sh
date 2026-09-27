#!/system/bin/sh
# ============================================================
# AnCLI Environment Bootstrapper (ancli_env.sh)
# Sourced by wrappers to inject proxies and fix environment
# ============================================================

# LD_LIBRARY_PATH / LD_PRELOAD left over from Termux or other Android shells
# would make ANY dynamically-linked binary (getprop, dumpsys, ip, proot,
# container tools) load host libraries and crash — strip them FIRST, before
# any other command in this script runs.
unset LD_LIBRARY_PATH LD_PRELOAD

# Mode switch: "host" = native tools running directly on Android (no proot).
# In host mode the Android PATH is kept and TMPDIR points to a writable host
# dir; otherwise the container PATH/TMPDIR are used as before.
# ANCLI_DIR/bin hosts proot shims (git/bash/curl) for native tools; it is
# appended LAST so host binaries (e.g. Android's own curl) win when present.
PRE_ANCLI_PATH="$PATH"
if [ "${1:-}" = "host" ]; then
    export PATH="$PRE_ANCLI_PATH:/data/adb/ksu/bin:/data/adb/ap/bin:/data/local/tmp/ancli/bin"
    export TMPDIR=/data/local/tmp
else
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin
    export TMPDIR=/tmp
fi
export HOME=/root
export GODEBUG=netdns=go
export UV_USE_IO_URING=0
export BUN_FEATURE_FLAG_IO_URING=0

# Locale: C.UTF-8 is always available in Ubuntu 24.04 and keeps Python/Node
# output sane (no missing-locale warnings, UTF-8 file names).
export LANG=C.UTF-8

# TUI tools need a sane TERM; adb shell and some su wrappers leave it empty,
# which makes Ink/React TUIs (mimo, opencode, claude) crash on startup.
export TERM="${TERM:-xterm-256color}"

# Follow the Android system timezone so git/logger timestamps are local time.
TZ_SYS=$(getprop persist.sys.timezone 2>/dev/null)
[ -n "$TZ_SYS" ] && export TZ="$TZ_SYS"

# Raise the fd limit: Node/Bun TUIs spawn many workers/LSP servers and the
# Android default (1024) is easily exhausted.
ulimit -n 65535 2>/dev/null || ulimit -n 16384 2>/dev/null || true

# Clean Termux environment variables to prevent containerized binaries from seeking host Termux paths
unset TERMUX_VERSION PREFIX TERMUX_APP_PID TERMUX__PREFIX TERMUX__ROOTFS_DIR TERMUX_APK_RELEASE TERMUX_IS_DEBUGGABLE_BUILD TERMUX_MAIN_PACKAGE_FORMAT TERMUX__SE_PROCESS_CONTEXT TERMUX_APP__DATA_DIR TERMUX_APP__LEGACY_DATA_DIR TERMUX_APP__SE_INFO TERMUX_APP__SE_FILE_CONTEXT TERMUX__HOME

# git "dubious ownership" fix: files under /sdcard are owned by Android uids
# that don't exist inside the container, so git refuses to operate on them.
# Whitelist every directory via env (git >= 2.35) so tools that shell out to
# git (mimo, aider, opencode) work out of the box.
# Trade-off: a malicious repo under /sdcard could run its hooks with the
# container user's privileges — acceptable, since the container is a sandbox
# with no extra Android privileges (no root on the host side).
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0='*'

# --- Fcitx5 Input Method Integration ---
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx

# --- Android WiFi proxy detection & inheritance (cached for 30s) ---
# dumpsys connectivity is a slow binder call (~1-2s); caching avoids paying it
# on every tool launch while keeping VPN/proxy changes picked up quickly.
# PROXY_LAST keeps the most recent *successful* detection with no expiry: when
# this probe fails (system proxy temporarily unset, dumpsys busy), falling back to
# the last known proxy beats silently connecting directly and timing out.
PROXY_CACHE="/data/local/tmp/ancli/.proxy_cache"
PROXY_LAST="/data/local/tmp/ancli/.proxy_cache_last"
PROXY_HOST=""
PROXY_PORT=""
if [ -f "$PROXY_CACHE" ]; then
    read -r CACHED_HOST CACHED_PORT CACHED_TS < "$PROXY_CACHE" 2>/dev/null
    # CACHED_TS must be a positive integer; reject garbage from a corrupted cache
    case "$CACHED_TS" in
        ''|*[!0-9]*) CACHED_TS=0 ;;
    esac
    if [ -n "$CACHED_HOST" ] && [ "$CACHED_TS" -gt 0 ]; then
        NOW=$(date +%s 2>/dev/null || echo 0)
        # Age must be within [0, 30): the lower bound rejects clock rollback
        # (e.g. after reboot before NTP sync), which would otherwise keep a
        # stale proxy cached forever.
        AGE=$((NOW - CACHED_TS))
        if [ "$NOW" -gt 0 ] && [ "$AGE" -ge 0 ] && [ "$AGE" -lt 30 ]; then
            PROXY_HOST="$CACHED_HOST"
            PROXY_PORT="$CACHED_PORT"
        fi
    fi
fi
if [ -z "$PROXY_HOST" ]; then
    PROXY_INFO=$(dumpsys connectivity 2>/dev/null | grep -i 'HttpProxy:' | head -n 1)
    if [ -n "$PROXY_INFO" ]; then
        PROXY_HOST=$(echo "$PROXY_INFO" | sed -n 's/.*HttpProxy:[[:space:]]*\[\([^ ]*\)\].*/\1/p')
        PROXY_PORT=$(echo "$PROXY_INFO" | sed -ne 's/.*HttpProxy:[[:space:]]*\[[^ ]*\][[:space:]]*\([0-9]*\).*/\1/p')
        if [ -n "$PROXY_HOST" ] && [ -n "$PROXY_PORT" ]; then
            # mkdir -p: when the wrapper runs as a plain shell user (uid 2000),
            # /data/local/tmp/ancli is root-owned (0755) and not writable; if
            # the write fails we simply fall back to probing dumpsys on every
            # launch (same behaviour as before, just slower).
            mkdir -p /data/local/tmp/ancli 2>/dev/null || true
            echo "$PROXY_HOST $PROXY_PORT $(date +%s 2>/dev/null || echo 0)" > "$PROXY_CACHE" 2>/dev/null || true
            chmod 644 "$PROXY_CACHE" 2>/dev/null || true
            cp -f "$PROXY_CACHE" "$PROXY_LAST" 2>/dev/null || true
        fi
    fi
fi
if [ -z "$PROXY_HOST" ] && [ -f "$PROXY_LAST" ]; then
    read -r LAST_HOST LAST_PORT _ < "$PROXY_LAST" 2>/dev/null
    if [ -n "$LAST_HOST" ] && [ -n "$LAST_PORT" ]; then
        PROXY_HOST="$LAST_HOST"
        PROXY_PORT="$LAST_PORT"
        echo "[AnCLI] System proxy not advertised right now; reusing last known $PROXY_HOST:$PROXY_PORT." >&2
    fi
fi
if [ -n "$PROXY_HOST" ] && [ -n "$PROXY_PORT" ]; then
    export http_proxy="http://$PROXY_HOST:$PROXY_PORT"
    export https_proxy="http://$PROXY_HOST:$PROXY_PORT"
    export HTTP_PROXY="http://$PROXY_HOST:$PROXY_PORT"
    export HTTPS_PROXY="http://$PROXY_HOST:$PROXY_PORT"
    export ALL_PROXY="http://$PROXY_HOST:$PROXY_PORT"
    # Loopback must never go through the (possibly remote) proxy: local model
    # servers, MCP endpoints and the container's own services live there.
    export no_proxy="localhost,127.0.0.1,::1,0.0.0.0"
    export NO_PROXY="$no_proxy"
fi

# --- DNS inheritance for the container ---
# The core runs inside the glibc guest, where Android's getprop/dumpsys cannot
# execute at all, so it cannot discover the network's resolvers by itself. We are
# on the host here: probe them and cache the result for `ancli repair` /
# `_write_resolv_conf()` (refreshed when missing or older than an hour).
DNS_CACHE="/data/local/tmp/ancli/.dns_cache"
_dns_refresh=1
if [ -f "$DNS_CACHE" ]; then
    read -r _dns_ts _ < "$DNS_CACHE" 2>/dev/null
    case "$_dns_ts" in
        ''|*[!0-9]*) _dns_ts=0 ;;
    esac
    _dns_now=$(date +%s 2>/dev/null || echo 0)
    if [ "$_dns_now" -gt 0 ] && [ "$_dns_ts" -gt 0 ]; then
        _dns_age=$((_dns_now - _dns_ts))
        [ "$_dns_age" -ge 0 ] && [ "$_dns_age" -lt 3600 ] && _dns_refresh=0
    fi
fi
if [ "$_dns_refresh" -eq 1 ]; then
    _dns_list=""
    for _dns_prop in net.dns1 net.dns2; do
        _dns_val=$(getprop "$_dns_prop" 2>/dev/null)
        case "$_dns_val" in
            ''|0.*|*[!0-9.]*) continue ;;
        esac
        case " $_dns_list " in
            *" $_dns_val "*) continue ;;
        esac
        _dns_list="$_dns_list $_dns_val"
    done
    if [ -z "${_dns_list// /}" ]; then
        # Modern Android keeps the per-network resolvers here instead.
        _dns_list=$(dumpsys connectivity 2>/dev/null | grep -o 'DnsAddresses: \[[^]]*\]' | head -n 1 \
            | tr ',' '\n' | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}' | head -n 3 \
            | while read -r _dns_ip; do printf ' %s' "$_dns_ip"; done)
    fi
    _dns_list=$(echo "$_dns_list" | tr -s ' ')
    if [ -n "${_dns_list// /}" ]; then
        mkdir -p /data/local/tmp/ancli 2>/dev/null || true
        printf '%s%s\n' "$(date +%s 2>/dev/null || echo 0)" "$_dns_list" > "$DNS_CACHE" 2>/dev/null || true
        chmod 644 "$DNS_CACHE" 2>/dev/null || true
    fi
fi

# Auto-bind potential Clash/Tun virtual IPs to local loopback to satisfy Go socket bind traversal.
# Only run the bind loop when none of the virtual IPs is present yet (avoids 16
# failing `ip addr add` calls on every wrapper launch).
if ! ip addr show dev lo 2>/dev/null | grep -q "198\.18\.0\."; then
    for i in $(seq 10 25); do
        ip addr add 198.18.0.$i/32 dev lo 2>/dev/null || true
    done
fi

# Fix ownership of agy/gemini/claude auth credential directories on every launch.
# This prevents root-locked files from blocking subsequent shell-user runs.
ROOTFS="/data/local/tmp/ancli/rootfs"
# /root must be traversable by the shell user (uid 2000) when wrappers run
# without su; ubuntu-base ships it as 0700 root.
chmod 755 "$ROOTFS/root" 2>/dev/null || true
# Hand the credential dirs to the shell user and keep them owner-only: root
# ignores DAC so it still works, and other apps can no longer read OAuth tokens.
for _conf_dir in /root/.config /root/.gemini /root/.claude /root/.local; do
    if [ -d "$ROOTFS$_conf_dir" ]; then
        chown -R 2000:2000 "$ROOTFS$_conf_dir" 2>/dev/null || true
        chmod -R u+rwX,go-rwx "$ROOTFS$_conf_dir" 2>/dev/null || true
    fi
done
