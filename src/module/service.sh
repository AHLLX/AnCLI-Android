#!/system/bin/sh
# ============================================================
# AnCLI 开机自启服务脚本 (service.sh)
# 触发时机：每次设备开机 late_start 阶段由 Magisk / KSU / APatch 自动执行。
# 职责：重建容器 DNS、校验核心权限、同步 Agent 凭证权限、部署 host bash 兼容层、
#       将已安装工具的包装器同步到动态 bin 目录并清理历史遗留 shim。
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"
MODULE_DIR="${MODDIR:-/data/adb/modules/ancli}"

# 1. 重建容器内 /etc/resolv.conf（系统重启后可能被重置）。
#    优先使用当前网络的真实 DNS（先查 getprop，再查 dumpsys connectivity DnsAddresses），
#    其后回落至国内友好解析器（223.5.5.5 / 119.29.29.29），最后才是 1.1.1.1 / 8.8.8.8。
#    绝不将 8.8.8.8 放在首位，避免在被屏蔽/污染的网络中导致域名解析失败。
if [ -d "$ROOTFS/etc" ]; then
    # 部分 ubuntu-base 镜像将 resolv.conf 设为符号链接，先删除以确保写入真实文件
    [ -L "$ROOTFS/etc/resolv.conf" ] && rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    # 使用绝对路径：开机服务脚本默认的 PATH 极简
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
    # 若开机时 Wi-Fi/移动数据尚未就绪（_dns_net 为空），启动后台异步子 Shell 最多重试 5 分钟（10×30s），
    # 待网络就绪后自动用真实 DNS 覆写 resolv.conf，避免阻塞系统启动流程。
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

# 2. 确保 proot 可执行权限（防止被手机管家/清理工具重置）
[ -f "$ANCLI_DIR/bin/proot" ] && chmod 755 "$ANCLI_DIR/bin/proot"

# 3. 确保 ancli-core.py 可执行权限
[ -f "$ANCLI_DIR/bin/ancli-core.py" ] && chmod 755 "$ANCLI_DIR/bin/ancli-core.py"

# 4. 修复各类 AI Agent 凭证与共享技能目录属主（移交 UID 2000 并收紧为 u+rwX,go-rwx），
#    防止首次以 root 启动写入的令牌导致后续普通 shell 会话报 Permission denied。
for _conf in ".config" ".gemini" ".claude" ".local" ".agents" ".dsh" ".grok" ".mimocode"; do
    _full="$ROOTFS/root/$_conf"
    if [ -d "$_full" ]; then
        chown -R 2000:2000 "$_full" 2>/dev/null || true
        chmod -R u+rwX,go-rwx "$_full" 2>/dev/null || true
    fi
done

# 5. 确保 PRoot 隔离用的 hosts 文件存在且可读
if [ ! -f "$ANCLI_DIR/hosts" ]; then
    printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n' \
        > "$ANCLI_DIR/hosts"
fi
chmod 644 "$ANCLI_DIR/hosts" 2>/dev/null || true

# 5.1 部署 AGY 宿主机 `bash -c` POSIX 兼容入口到 KSU/AP 动态目录：
#     仅当目标不存在或带有 AnCLI 标记时才写入，绝不覆盖用户自装的真实 bash，也不触碰 /system/bin。
if [ -f "$MODULE_DIR/ancli/bash-host-compat" ]; then
    for INSTANT_BIN in /data/adb/ksu/bin /data/adb/ap/bin; do
        [ -d "$INSTANT_BIN" ] || continue
        host_bash="$INSTANT_BIN/bash"
        replace_host_bash=0
        if [ ! -e "$host_bash" ]; then
            replace_host_bash=1
        elif head -n 8 "$host_bash" 2>/dev/null | grep -q 'AnCLI AGY host bash compatibility\|AnCLI proot shim'; then
            replace_host_bash=1
        fi
        if [ "$replace_host_bash" -eq 1 ]; then
            cp -f "$MODULE_DIR/ancli/bash-host-compat" "$host_bash" 2>/dev/null || true
            chmod 755 "$host_bash" 2>/dev/null || true
        fi
    done
fi

# 6. 每次开机将 $ANCLI_DIR/bin/ 下的工具包装器同步至 KSU/AP 动态路径，
#    跳过核心基础文件与宿主机 shim（bash 已在 5.1 单独处理），并清理旧版 git/curl 广域 shim。
for INSTANT_BIN in /data/adb/ksu/bin /data/adb/ap/bin; do
    [ -d "$INSTANT_BIN" ] || continue
    for wrapper in "$ANCLI_DIR/bin/"*; do
        name="${wrapper##*/}"
        # 跳过基础设施文件与宿主机 shim，仅同步应用包装脚本
        case "$name" in
            proot|ancli-core.py|ancli|ancli_env.sh|registry.json|installed.json|hosts) continue ;;
            git|bash|curl) continue ;;
            .*) continue ;;
        esac
        [ -f "$wrapper" ] || continue
        cp -f "$wrapper" "$INSTANT_BIN/$name" 2>/dev/null || true
    done
    # 清理旧版本遗留的广域 git/curl proot shim（保留 5.1 刚维护的 bash 兼容入口）
    for shim in git curl; do
        if [ -f "$INSTANT_BIN/$shim" ] && head -n 2 "$INSTANT_BIN/$shim" 2>/dev/null | grep -q "AnCLI proot shim"; then
            rm -f "$INSTANT_BIN/$shim" 2>/dev/null || true
        fi
    done
done

# 7. 清理模块自身 system/bin 下可能残留的旧版宿主机 shim，防止挂载后全局劫持系统命令
for shim in git bash curl; do
    mod_shim="${MODDIR:-/data/adb/modules/ancli}/system/bin/$shim"
    if [ -f "$mod_shim" ] && grep -qE 'AnCLI proot shim|exec /data/adb/(ksu|ap)/bin/' "$mod_shim" 2>/dev/null; then
        rm -f "$mod_shim" 2>/dev/null && echo "AnCLI: removed stale module shim $mod_shim"
    fi
done
