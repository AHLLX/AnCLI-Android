#!/system/bin/sh
# ============================================================
# AnCLI Environment Bootstrapper (ancli_env.sh)
# Sourced by wrappers to inject proxies and fix environment
# ============================================================

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin
export HOME=/root
export TMPDIR=/tmp
export GODEBUG=netdns=go
export UV_USE_IO_URING=0
export BUN_FEATURE_FLAG_IO_URING=0

# Clean Termux environment variables to prevent containerized binaries from seeking host Termux paths
unset TERMUX_VERSION PREFIX TERMUX_APP_PID TERMUX__PREFIX TERMUX__ROOTFS_DIR TERMUX_APK_RELEASE TERMUX_IS_DEBUGGABLE_BUILD TERMUX_MAIN_PACKAGE_FORMAT TERMUX__SE_PROCESS_CONTEXT TERMUX_APP__DATA_DIR TERMUX_APP__LEGACY_DATA_DIR TERMUX_APP__SE_INFO TERMUX_APP__SE_FILE_CONTEXT TERMUX__HOME

# --- Fcitx5 Input Method Integration ---
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx

# --- Android WiFi proxy detection & inheritance (cached for 30s) ---
# dumpsys connectivity is a slow binder call (~1-2s); caching avoids paying it
# on every tool launch while keeping VPN/proxy changes picked up quickly.
PROXY_CACHE="/data/local/tmp/ancli/.proxy_cache"
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
        fi
    fi
fi
if [ -n "$PROXY_HOST" ] && [ -n "$PROXY_PORT" ]; then
    export http_proxy="http://$PROXY_HOST:$PROXY_PORT"
    export https_proxy="http://$PROXY_HOST:$PROXY_PORT"
    export HTTP_PROXY="http://$PROXY_HOST:$PROXY_PORT"
    export HTTPS_PROXY="http://$PROXY_HOST:$PROXY_PORT"
    export ALL_PROXY="http://$PROXY_HOST:$PROXY_PORT"
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
for _conf_dir in /root/.config /root/.gemini /root/.claude /root/.local; do
    if [ -d "$ROOTFS$_conf_dir" ]; then
        chown -R 2000:2000 "$ROOTFS$_conf_dir" 2>/dev/null || true
        chmod -R 755 "$ROOTFS$_conf_dir" 2>/dev/null || true
    fi
done
