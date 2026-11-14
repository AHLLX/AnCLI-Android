#!/system/bin/sh
# ============================================================
# AnCLI 模块卸载脚本 (uninstall.sh)
# 触发时机：用户在 Magisk / KernelSU / APatch 管理器中卸载模块时自动执行。
# 核心原则：默认保留 Ubuntu rootfs、已安装工具清单及 API 密钥，防止模块升级或误卸丢失数据。
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"

# 1. 仅终止正在使用 AnCLI rootfs 的 PRoot 进程，避免误杀 Termux 等其他容器进程。
pkill -f "proot.*${ANCLI_DIR}/rootfs" 2>/dev/null || \
    pkill -f "proot.*ancli" 2>/dev/null || true

# 2. 判断是否保留容器数据：
#    管理器卸载在非交互后台运行（无 TTY），因此默认 KEEP_DATA=1；
#    仅当存在一次性标记文件 /data/local/tmp/ancli_force_purge 时才彻底清空。
KEEP_DATA=1
PURGE_MARKER="/data/local/tmp/ancli_force_purge"
if [ -f "$PURGE_MARKER" ]; then
    KEEP_DATA=0
    # 标记文件为一次性指令，读取后立即删除，避免后续重装或升级被意外清空
    rm -f "$PURGE_MARKER"
fi

if [ "$KEEP_DATA" -eq 1 ]; then
    # 默认保留 rootfs、installed.json、secrets/ 等用户资产，仅移除宿主机动态可执行目录
    rm -rf "$ANCLI_DIR/bin"
    # 移除模块部署的 AGY 宿主机 bash 兼容模板（用户自定义的 host bash 在下方单独保护）
    rm -f "$ANCLI_DIR/bash-host-compat"
else
    # 强制彻底清理模式：删除整个 AnCLI 运行时目录
    rm -rf "$ANCLI_DIR"
fi

# 3. 清理 KernelSU / APatch 动态 bin 目录中由 AnCLI 注入的包装器与辅助脚本
for BIN_DIR in /data/adb/ksu/bin /data/adb/ap/bin; do
    if [ -d "$BIN_DIR" ]; then
        # 删除文件内容引用了 ANCLI_DIR 的包装脚本
        grep -rl "$ANCLI_DIR" "$BIN_DIR" 2>/dev/null | while IFS= read -r f; do
            rm -f "$f" 2>/dev/null || true
        done
        # xdg-open 宿主机桥接脚本不直接引用 ANCLI_DIR 字符串，需按文件名显式清理
        rm -f "$BIN_DIR/xdg-open" 2>/dev/null || true
        # 仅删除带有 AnCLI 标记的 bash 兼容脚本，绝不误删用户自己安装的真实 bash 二进制
        if [ -f "$BIN_DIR/bash" ] && head -n 8 "$BIN_DIR/bash" 2>/dev/null \
            | grep -q 'AnCLI AGY host bash compatibility'; then
            rm -f "$BIN_DIR/bash" 2>/dev/null || true
        fi
    fi
done
