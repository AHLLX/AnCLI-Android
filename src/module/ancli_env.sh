#!/system/bin/sh
# ============================================================
# AnCLI 宿主机环境桥接脚本 (ancli_env.sh)
# 职责：由各工具包装脚本在进入 PRoot 前 source 加载，完成动态库隔离、
#       环境变量修复、代理自动/手动继承、DNS 缓存探测、虚拟网卡绑定与权限修复。
# ============================================================

# 1. 首要步骤：剥离 Termux 或其他 Android Shell 遗留的 LD_LIBRARY_PATH / LD_PRELOAD。
#    若不优先清除，后续任何动态链接命令（getprop、dumpsys、ip、proot 等）都会加载宿主冲突库并崩溃。
unset LD_LIBRARY_PATH LD_PRELOAD

# 2. 容器内基础环境变量（所有受管工具均运行在 Ubuntu PRoot 客体中）
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin
export TMPDIR=/tmp
export HOME=/root
export GODEBUG=netdns=go
# 禁用 io_uring：Android 内核/PRoot 环境下 uv 与 bun 开启 io_uring 易触发系统调用失败
export UV_USE_IO_URING=0
export BUN_FEATURE_FLAG_IO_URING=0

# 3. Locale 与终端类型设置
#    Ubuntu 24.04 恒定内置 C.UTF-8，可消除 Python/Node 的缺失 locale 警告并保证 UTF-8 文件名正常
export LANG=C.UTF-8
#    adb shell 或部分 su 环境不传 TERM，会导致 Ink/React TUI（mimo/opencode/claude）启动崩溃
export TERM="${TERM:-xterm-256color}"

# 4. 同步 Android 系统时区，确保容器内 git 提交与日志时间戳与本机一致
TZ_SYS=$(getprop persist.sys.timezone 2>/dev/null)
[ -n "$TZ_SYS" ] && export TZ="$TZ_SYS"

# 5. 提高文件描述符上限：Node/Bun TUI 常并发拉起大量 worker/LSP，Android 默认 1024 极易耗尽
ulimit -n 65535 2>/dev/null || ulimit -n 16384 2>/dev/null || true

# 6. 清除 Termux 注入的环境变量，防止容器内二进制误寻宿主机 Termux 路径
unset TERMUX_VERSION PREFIX TERMUX_APP_PID TERMUX__PREFIX TERMUX__ROOTFS_DIR TERMUX_APK_RELEASE TERMUX_IS_DEBUGGABLE_BUILD TERMUX_MAIN_PACKAGE_FORMAT TERMUX__SE_PROCESS_CONTEXT TERMUX_APP__DATA_DIR TERMUX_APP__LEGACY_DATA_DIR TERMUX_APP__SE_INFO TERMUX_APP__SE_FILE_CONTEXT TERMUX__HOME

# 7. 修复 Git "dubious ownership" 报错：
#    /sdcard 下的文件属主为 Android UID，容器内不存在对应用户，Git >= 2.35 默认拒绝操作。
#    通过环境变量全局放行 safe.directory=*（容器本身处于无特权沙箱内，可接受该权衡）。
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0='*'

# 8. Fcitx5 中文输入法环境变量集成
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx

# 9. 读取 AnCLI 全局代理配置（/root/.ancli-config.json），按模式分发处理：
#    - direct：强制直连，清空代理变量（跳过慢速 dumpsys 探测）
#    - manual：使用配置的 proxy_url（跳过系统探测）
#    - system（默认）：探测 Android 系统代理（带 30s 缓存与末次成功兜底）
ANCLI_CONFIG="/data/local/tmp/ancli/rootfs/root/.ancli-config.json"
ANCLI_PROXY_URL=""
ANCLI_PROXY_MODE="system"
if [ -f "$ANCLI_CONFIG" ]; then
    ANCLI_PROXY_URL=$(sed -n 's/^[[:space:]]*"proxy_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ANCLI_CONFIG" 2>/dev/null | head -n 1)
    ANCLI_PROXY_MODE=$(sed -n 's/^[[:space:]]*"proxy_mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ANCLI_CONFIG" 2>/dev/null | head -n 1)
fi

if [ "$ANCLI_PROXY_MODE" = "direct" ]; then
    unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
    export no_proxy="localhost,127.0.0.1,::1,0.0.0.0"
    export NO_PROXY="$no_proxy"
elif [ "$ANCLI_PROXY_MODE" = "manual" ] && [ -n "$ANCLI_PROXY_URL" ]; then
    export http_proxy="$ANCLI_PROXY_URL"
    export https_proxy="$ANCLI_PROXY_URL"
    export HTTP_PROXY="$ANCLI_PROXY_URL"
    export HTTPS_PROXY="$ANCLI_PROXY_URL"
    export ALL_PROXY="$ANCLI_PROXY_URL"
    export no_proxy="localhost,127.0.0.1,::1,0.0.0.0"
    export NO_PROXY="$no_proxy"
else
    # --- Android Wi-Fi / 系统代理自动探测与继承（30 秒有效缓存） ---
    # dumpsys connectivity 属于跨进程 Binder 慢调用（~1-2s），缓存可显著加快命令启动；
    # .proxy_cache_last 永久保存最近一次成功探测结果，当系统代理瞬时未广播或 dumpsys 繁忙时
    # 复用上次代理，避免静默退化为直连而超时。
    PROXY_CACHE="/data/local/tmp/ancli/.proxy_cache"
    PROXY_LAST="/data/local/tmp/ancli/.proxy_cache_last"
    PROXY_HOST=""
    PROXY_PORT=""
    if [ -f "$PROXY_CACHE" ]; then
        read -r CACHED_HOST CACHED_PORT CACHED_TS < "$PROXY_CACHE" 2>/dev/null
        # 校验时间戳为正整数，防止缓存损坏导致异常
        case "$CACHED_TS" in
            ''|*[!0-9]*) CACHED_TS=0 ;;
        esac
        if [ -n "$CACHED_HOST" ] && [ "$CACHED_TS" -gt 0 ]; then
            NOW=$(date +%s 2>/dev/null || echo 0)
            # 缓存有效期 [0, 30) 秒：下界检查防止开机 NTP 校时前时钟回拨导致过期缓存永久生效
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
                # 普通 shell 用户（uid 2000）无权写入 root 所有的 /data/local/tmp/ancli，失败时静默退回每次探测
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
        # 本地回环地址绝不走代理，保证本地模型服务、MCP 端点与容器自身服务直连
        export no_proxy="localhost,127.0.0.1,::1,0.0.0.0"
        export NO_PROXY="$no_proxy"
    fi
fi

# 10. 容器 DNS 解析器探测与缓存（每小时刷新一次）
#     glibc 容器内部无法执行 Android 的 getprop/dumpsys，因此由宿主机侧探出真实 DNS
#     并写入 .dns_cache，供 `ancli repair` 与 `_write_resolv_conf()` 读取。
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
        # 新版 Android 将按网络隔离的 DNS 存放在 dumpsys connectivity 的 DnsAddresses 中
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

# 11. 为 Go 程序（如 agy）预绑定 Clash TUN 虚拟 IP 段 (198.18.0.10~25) 到 lo 回环网卡，
#     防止 Go runtime 在遍历/绑定 TUN 虚拟地址时触发 socket bind 崩溃。
#     仅在尚未绑定过时执行循环，避免每次启动产生 16 次无效的 ip addr add 调用。
if ! ip addr show dev lo 2>/dev/null | grep -q "198\.18\.0\."; then
    for i in $(seq 10 25); do
        ip addr add 198.18.0.$i/32 dev lo 2>/dev/null || true
    done
fi

# 12. 修复凭证与配置目录权限，防止 root 首次运行生成的文件阻塞后续 shell 用户（uid 2000）访问
ROOTFS="/data/local/tmp/ancli/rootfs"
# ubuntu-base 默认 /root 为 0700，必须放宽到 755 以便非 root 包装器进入目录
chmod 755 "$ROOTFS/root" 2>/dev/null || true
# 将凭证目录移交 uid 2000 并收紧为仅属主可读写（u+rwX,go-rwx），root 仍可正常访问且其他 App 无法窃取 OAuth 令牌
for _conf_dir in /root/.config /root/.gemini /root/.claude /root/.local; do
    if [ -d "$ROOTFS$_conf_dir" ]; then
        chown -R 2000:2000 "$ROOTFS$_conf_dir" 2>/dev/null || true
        chmod -R u+rwX,go-rwx "$ROOTFS$_conf_dir" 2>/dev/null || true
    fi
done
