#!/system/bin/sh
# ============================================================
# AnCLI Quick Bootstrap
# Downloads the module ZIP and guides installation via Manager
# ============================================================
set -eu

R='\033[0;31m'; G='\033[0;32m'; Y='\033[0;33m'; C='\033[0;36m'; NC='\033[0m'
ok()   { printf "${G}[OK]${NC} %s\n" "$1"; }
err()  { printf "${R}[X]${NC} %s\n" "$1"; exit 1; }
info() { printf "${C}>${NC} %s\n" "$1"; }

MODULE_URL="https://github.com/AHLLX/AnCLI-Android/releases/latest/download/ancli-module.zip"
TMP_ZIP="/data/local/tmp/ancli-module.zip"

echo "=================================================="
echo "          AnCLI Quick Installer"
echo "=================================================="
echo ""

# Detect root manager
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

curl -L --connect-timeout 15 --progress-bar \
    -o "$TMP_ZIP" "$MODULE_URL" || err "Download failed"

ok "Module downloaded to $TMP_ZIP"
echo ""

# Try auto-install for Magisk
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
