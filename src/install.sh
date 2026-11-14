#!/system/bin/sh
# ============================================================
# AnCLI 快速引导安装脚本 (install.sh)
# 作用：从 GitHub update.json 解析最新模块 ZIP 下载地址，
#       自动尝试直连或国内镜像拉取，并引导/自动通过 Root 管理器刷入。
# ============================================================
set -eu

# 终端彩色日志输出辅助函数
R='\033[0;31m'; G='\033[0;32m'; Y='\033[0;33m'; C='\033[0;36m'; NC='\033[0m'
ok()   { printf "${G}[OK]${NC} %s\n" "$1"; }
err()  { printf "${R}[X]${NC} %s\n" "$1"; exit 1; }
info() { printf "${C}>${NC} %s\n" "$1"; }

# 1. 从仓库唯一的版本清单 update.json 动态解析最新模块 ZIP URL，
#    发版时只需维护 update.json 即可同步更新引导脚本。
UPDATE_JSON_URL="https://raw.githubusercontent.com/AHLLX/AnCLI/main/update.json"
MODULE_URL=$(curl -fsSL --connect-timeout 15 --max-time 30 "$UPDATE_JSON_URL" 2>/dev/null \
    | sed -n 's/.*"zipUrl"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
if [ -z "${MODULE_URL:-}" ]; then
    err "Failed to resolve module URL from $UPDATE_JSON_URL"
fi
TMP_ZIP="/data/local/tmp/ancli-module.zip"

echo "=================================================="
echo "          AnCLI Quick Installer"
echo "=================================================="
echo ""

# 2. 检测设备当前使用的 Root 管理器类型（Magisk / KernelSU / APatch）
if [ -f /data/adb/magisk/util_functions.sh ]; then
    ROOT_MGR="Magisk"
elif [ -d /data/adb/ksu ]; then
    ROOT_MGR="KernelSU"
elif [ -d /data/adb/ap ]; then
    ROOT_MGR="APatch"
else
    err "No supported root manager found (Magisk/KernelSU/APatch)"
fi

info "Detected root manager: $ROOT_MGR"
info "Downloading AnCLI module..."

# 3. 依次尝试 GitHub 直连与多个加速镜像代理下载模块 ZIP
success=0
for source in \
    "$MODULE_URL" \
    "https://gh-proxy.com/${MODULE_URL}" \
    "https://ghfast.top/${MODULE_URL}" \
    "https://gh.moeyy.cn/${MODULE_URL}"; do
    
    ui_source_name=$(echo "$source" | awk -F/ '{print $3}')
    info "Trying download from: $ui_source_name"
    if curl -f -L --connect-timeout 15 --max-time 120 --progress-bar \
        -o "$TMP_ZIP" "$source" 2>/dev/null && [ -s "$TMP_ZIP" ]; then
        success=1
        break
    fi
done

if [ "$success" -ne 1 ]; then
    err "Download failed from all available sources"
fi
ok "Module downloaded successfully"

ok "Module downloaded to $TMP_ZIP"
echo ""

# 4. 若为 Magisk 环境且 CLI 可用，尝试调用 magisk --install-module 自动刷入；
#    否则提示用户在管理器 App 中手动选择本地 ZIP 刷入。
if [ "$ROOT_MGR" = "Magisk" ] && command -v magisk >/dev/null 2>&1; then
    info "Attempting automatic installation via Magisk..."
    if magisk --install-module "$TMP_ZIP"; then
        ok "Module installed! Please reboot your device."
        rm -f "$TMP_ZIP"
        exit 0
    fi
    info "Auto-install failed, please install manually."
fi

info "To install, open your $ROOT_MGR Manager app:"
echo "  -> Modules -> Install from storage"
echo "  -> Select: $TMP_ZIP"
echo ""
ok "Done! Flash the module and reboot."
