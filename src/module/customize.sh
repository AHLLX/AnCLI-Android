#!/system/bin/sh
# ============================================================
# AnCLI 模块刷入安装钩子 (customize.sh)
# 触发时机：用户通过 Magisk / KernelSU / APatch 刷入模块 ZIP 时自动执行。
# 职责：准备运行目录、部署 PRoot、下载并解压 Ubuntu Base rootfs、
#       初始化 APT 基础工具链与 npm 路径、部署核心脚本与动态快捷入口。
# ============================================================

# 管理器框架自动注入环境变量与函数：$MODPATH, ui_print(), abort(), set_perm()
ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"
BIN_DIR="${ANCLI_DIR}/bin"
UBUNTU_MIRROR="${ANCLI_MIRROR:-mirrors.tuna.tsinghua.edu.cn}"

UBUNTU_PATH="ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-arm64.tar.gz"

# 从 module.prop 动态读取版本号用于刷入日志横幅，避免硬编码与发布元数据脱节
MODULE_VERSION="$(grep '^version=' "${MODPATH:-$(dirname "$0")}/module.prop" 2>/dev/null | cut -d= -f2)"
[ -n "$MODULE_VERSION" ] || MODULE_VERSION="unknown"

ui_print "============================================"
ui_print "  AnCLI Bootstrap Installer ${MODULE_VERSION}"
ui_print "============================================"
ui_print ""

# 1. 创建运行目录与共享内存目录
ui_print ">> Preparing directories..."
mkdir -p "$ROOTFS" "$BIN_DIR" "$ANCLI_DIR/shm"
# 挂载到容器 /dev/shm 的目录设为 1777（粘滞位），确保普通 shell 用户（uid 2000）可读写
chmod 1777 "$ANCLI_DIR/shm" 2>/dev/null || true

# 2. 从模块包部署静态编译的 PRoot 二进制
ui_print ">> Deploying PRoot..."
cp "$MODPATH/bin/proot" "$BIN_DIR/proot"
chmod 755 "$BIN_DIR/proot"
ui_print ">> PRoot deployed successfully."

# 3. 下载并解压 Ubuntu Base (arm64) 根文件系统（若已存在则幂等跳过）
if [ ! -f "$ROOTFS/bin/bash" ]; then
    ui_print ">> Downloading Ubuntu Base (arm64)..."
    TAR_PATH="$ANCLI_DIR/ubuntu-base.tar.gz"
    success=0
    
    # 依次尝试清华 TUNA、中科大 USTC 与 Ubuntu 官方 ports 镜像
    for mirror in \
        "https://${UBUNTU_MIRROR}/${UBUNTU_PATH}" \
        "https://mirrors.tuna.tsinghua.edu.cn/${UBUNTU_PATH}" \
        "https://mirrors.ustc.edu.cn/${UBUNTU_PATH}" \
        "https://ports.ubuntu.com/${UBUNTU_PATH}"; do
        
        ui_print ">> Trying mirror: $mirror"
        if curl -f -L --connect-timeout 20 --max-time 180 --progress-bar \
            -o "$TAR_PATH" "$mirror" 2>/dev/null && [ -s "$TAR_PATH" ]; then
            success=1
            break
        fi
    done
    
    if [ "$success" -ne 1 ]; then
        abort "Failed to download Ubuntu Base from all available mirrors"
    fi
    ui_print ">> Ubuntu Base downloaded successfully."

    ui_print ">> Extracting rootfs (this may take a minute)..."
    tar -xf "$TAR_PATH" -C "$ROOTFS" || abort "Failed to extract rootfs (corrupted download?)"
    rm -f "$TAR_PATH"

    # 初始化容器内 resolv.conf：优先写入宿主机 net.dns1/2，随后追加备用公共 DNS（去重且最多 3 条）
    rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    _dns_seen=""
    _dns_count=0
    for _dns_val in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" 8.8.8.8 1.1.1.1 223.5.5.5; do
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
    ui_print ">> Rootfs extracted and configured."
else
    ui_print ">> Rootfs already present, skipping download."
fi

# 4. 通过 PRoot 引导或补齐容器内 APT 基础依赖（带幂等检查）
PROOT_CMD="$BIN_DIR/proot -r $ROOTFS -b /dev -b /proc -b /sys -w /root"

# APT 依赖脚本生成（幂等）：基础工具链 = python/git/node + Java 17 + binutils
# Java 17 供 jadx 等反编译工具；binutils 提供 strings/readelf/objdump；
# 并解锁 PEP 668（Ubuntu 24.04 默认拒绝 pip install）以允许直接 pip 装库。
write_setup_script() {
    cat > "$ROOTFS/root/setup.sh" << SETUP
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

# 禁用 APT GPG 校验以规避 PRoot 下部分沙箱/签名系统调用兼容性问题
apt-get update -y -o Acquire::AllowInsecureRepositories=true -o Acquire::AllowUnauthenticated=true
apt-get install -y --no-install-recommends \\
    -o Acquire::AllowInsecureRepositories=true \\
    -o Acquire::AllowUnauthenticated=true \\
    --allow-unauthenticated \\
    ca-certificates curl python3 python3-pip git nodejs npm \\
    openjdk-17-jre-headless binutils $FCITX_PKGS
apt-get clean

# 移除 PEP 668 限制标记并配置清华 PyPI 镜像，允许在容器系统 Python 中直接 pip install
rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED
printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n' > /etc/pip.conf
SETUP
    chmod 755 "$ROOTFS/root/setup.sh"
}

if ! $PROOT_CMD /usr/bin/python3 --version >/dev/null 2>&1; then
    ui_print ">> Bootstrapping APT dependencies (Python, Git, Node.js)..."

    # 宿主机侧预先替换 Ubuntu APT 源为国内镜像
    if [ -f "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" ]; then
        sed -i "s/ports.ubuntu.com/${UBUNTU_MIRROR}/g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s/archive.ubuntu.com/${UBUNTU_MIRROR}/g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        ui_print ">> APT mirror: ${UBUNTU_MIRROR}"
    fi

    # 按系统语言决定是否安装 Fcitx5 中文输入法（约 50MB，仅对中/日/韩系统启用）
    SYS_LANG=$(getprop persist.sys.locale 2>/dev/null || getprop ro.product.locale 2>/dev/null || echo "en-US")
    case "$SYS_LANG" in
        zh*|ja*|ko*)
            FCITX_PKGS="fcitx5 fcitx5-chinese-addons"
            ui_print ">> CJK locale detected ($SYS_LANG) — will install Fcitx5 input method."
            ;;
        *)
            FCITX_PKGS=""
            ui_print ">> Non-CJK locale ($SYS_LANG) — skipping Fcitx5."
            ;;
    esac

    write_setup_script
    $PROOT_CMD /root/setup.sh || abort "APT bootstrap failed"
    rm -f "$ROOTFS/root/setup.sh"
    ui_print ">> Dependencies installed successfully."
else
    ui_print ">> Dependencies already installed, skipping APT."
    # 模块覆盖升级路径：若旧版 rootfs 缺失新版本追加的 Java 17 或 binutils，则幂等补装
    if ! $PROOT_CMD /usr/bin/java -version >/dev/null 2>&1 || \
       ! $PROOT_CMD /usr/bin/strings --version >/dev/null 2>&1; then
        ui_print ">> Backfilling base toolchain (Java 17 / binutils / pip unlock)..."
        write_setup_script
        if $PROOT_CMD /root/setup.sh; then
            ui_print ">> Base toolchain backfill complete."
        else
            ui_print ">> [WARN] Backfill failed — run 'ancli repair' or reinstall the module."
        fi
        rm -f "$ROOTFS/root/setup.sh"
    fi
fi

# 4b. 确保 /usr/local 目录结构存在（Ubuntu Base 24.04 默认不带 /usr/local/lib/node_modules，npm -g 依赖它）
ui_print ">> Ensuring npm global install paths..."
mkdir -p "$ROOTFS/usr/local/bin" \
         "$ROOTFS/usr/local/sbin" \
         "$ROOTFS/usr/local/lib/node_modules" \
         "$ROOTFS/usr/local/share" \
         "$ROOTFS/root/.npm/_logs"
chmod 755 "$ROOTFS/usr/local/bin" \
          "$ROOTFS/usr/local/sbin" \
          "$ROOTFS/usr/local/lib" \
          "$ROOTFS/usr/local/lib/node_modules"
ui_print ">> npm paths ready."

# 5. 部署 AnCLI 核心脚本、环境桥接脚本、宿主机 bash 兼容模板与运行时上下文模板
ui_print ">> Deploying AnCLI Core..."
cp "$MODPATH/ancli/ancli-core.py" "$BIN_DIR/ancli-core.py"
cp "$MODPATH/ancli/ancli_env.sh" "$BIN_DIR/ancli_env.sh"
chmod 755 "$BIN_DIR/ancli-core.py" "$BIN_DIR/ancli_env.sh"

# 部署 AGY 宿主机 POSIX `bash -c` 兼容模板（非原生后端，不覆盖 /system/bin，不启动嵌套 PRoot）
if [ -f "$MODPATH/ancli/bash-host-compat" ]; then
    cp "$MODPATH/ancli/bash-host-compat" "$ANCLI_DIR/bash-host-compat"
    chmod 755 "$ANCLI_DIR/bash-host-compat"
fi

# 部署不可变运行时上下文模板（仅作基准模板，实际 /root/.agents/AGENTS.md 由 ancli agent-init/repair 生成）
if [ -f "$MODPATH/ancli/runtime-context/AGENTS.md" ]; then
    mkdir -p "$ANCLI_DIR/runtime-context"
    cp "$MODPATH/ancli/runtime-context/AGENTS.md" "$ANCLI_DIR/runtime-context/AGENTS.md"
    chmod 644 "$ANCLI_DIR/runtime-context/AGENTS.md"
fi

# 同时部署 registry.json 到 $BIN_DIR 和 $ANCLI_DIR，兼容新旧核心版本的离线首启读取路径
cp "$MODPATH/ancli/registry.json" "$BIN_DIR/registry.json" 2>/dev/null || true
cp "$MODPATH/ancli/registry.json" "$ANCLI_DIR/registry.json" 2>/dev/null || true


# 6. 向 KernelSU / APatch 动态路径注入即时生效入口（无需重启即可使用）：
#    由于 SELinux 仅允许开机后覆写动态 bin 中已存在的文件，刷入阶段必须预先创建
#    主命令 `ancli`、AGY host bash 兼容脚本以及全部注册表工具的占位包装器。
REGISTRY_FILE="$MODPATH/ancli/registry.json"

for INSTANT_BIN in /data/adb/ksu/bin /data/adb/ap/bin; do
    [ -d "$INSTANT_BIN" ] || continue

    # 部署带 AnCLI 标记的 host bash 兼容脚本，不覆盖用户自己提供的真实 bash 二进制
    if [ -f "$MODPATH/ancli/bash-host-compat" ]; then
        host_bash="$INSTANT_BIN/bash"
        replace_host_bash=0
        if [ ! -e "$host_bash" ]; then
            replace_host_bash=1
        elif head -n 8 "$host_bash" 2>/dev/null | grep -q 'AnCLI AGY host bash compatibility\|AnCLI proot shim'; then
            replace_host_bash=1
        fi
        if [ "$replace_host_bash" -eq 1 ]; then
            cp -f "$MODPATH/ancli/bash-host-compat" "$host_bash"
            chmod 755 "$host_bash"
            ui_print ">> AGY host bash compatibility: $host_bash"
        fi
    fi

    # 注入主命令 ancli
    cp "$MODPATH/system/bin/ancli" "$INSTANT_BIN/ancli"
    chmod 755 "$INSTANT_BIN/ancli"
    ui_print ">> Instant access: $INSTANT_BIN/ancli"

    # 为注册表中的每个工具预置占位入口（后续由 ancli install / ancli repair 生成真实包装器）
    if [ -f "$REGISTRY_FILE" ]; then
        executables=$(grep '"executable"' "$REGISTRY_FILE" | sed 's/.*"executable"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
        for exe in $executables; do
            placeholder="$INSTANT_BIN/$exe"
            if [ ! -f "$placeholder" ]; then
                {
                    printf '#!/system/bin/sh\n'
                    printf 'target="/data/local/tmp/ancli/bin/%s"\n' "$exe"
                    printf 'if [ ! -x "$target" ]; then\n'
                    printf '    echo "[AnCLI] %s is not set up yet. Run: ancli repair" >&2\n' "$exe"
                    printf '    exit 127\n'
                    printf 'fi\n'
                    printf 'exec sh "$target" "$@"\n'
                } > "$placeholder"
                chmod 755 "$placeholder"
                ui_print ">> Pre-seeded placeholder: $INSTANT_BIN/$exe"
            fi
        done
    fi
done


ui_print ""
ui_print "=========================================="
ui_print "  Installation complete!"
ui_print "  Type 'ancli' in any root shell to start."
ui_print "=========================================="
