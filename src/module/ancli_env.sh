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

# --- Android WiFi proxy detection & inheritance ---
PROXY_INFO=$(dumpsys connectivity 2>/dev/null | grep -i 'HttpProxy:' | head -n 1)
if [ -n "$PROXY_INFO" ]; then
    PROXY_HOST=$(echo "$PROXY_INFO" | sed -n 's/.*HttpProxy:[[:space:]]*\[\([^ ]*\)\].*/\1/p')
    PROXY_PORT=$(echo "$PROXY_INFO" | sed -ne 's/.*HttpProxy:[[:space:]]*\[[^ ]*\][[:space:]]*\([0-9]*\).*/\1/p')
    if [ -n "$PROXY_PORT" ] && [ -n "$PROXY_HOST" ]; then
        export http_proxy="http://$PROXY_HOST:$PROXY_PORT"
        export https_proxy="http://$PROXY_HOST:$PROXY_PORT"
        export HTTP_PROXY="http://$PROXY_HOST:$PROXY_PORT"
        export HTTPS_PROXY="http://$PROXY_HOST:$PROXY_PORT"
        export ALL_PROXY="http://$PROXY_HOST:$PROXY_PORT"
    fi
fi

# Auto-bind potential Clash/Tun virtual IPs to local loopback to satisfy Go socket bind traversal.
for i in $(seq 10 25); do
    ip addr add 198.18.0.$i/32 dev lo 2>/dev/null || true
done

# Fix ownership of agy/gemini/claude auth credential directories on every launch.
# This prevents root-locked files from blocking subsequent shell-user runs.
ROOTFS="/data/local/tmp/ancli/rootfs"
for _conf_dir in /root/.config /root/.gemini /root/.claude /root/.local; do
    if [ -d "$ROOTFS$_conf_dir" ]; then
        chown -R 2000:2000 "$ROOTFS$_conf_dir" 2>/dev/null || true
        chmod -R 755 "$ROOTFS$_conf_dir" 2>/dev/null || true
    fi
done
