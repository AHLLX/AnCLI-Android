#!/system/bin/sh
# ============================================================
# AnCLI (Android CLI) Uninstaller
# ============================================================

R='\033[0;31m'; G='\033[0;32m'; Y='\033[0;33m'; C='\033[0;36m'; NC='\033[0m'
ok()   { printf "${G}[OK]${NC} %s\n" "$1"; }
info() { printf "${C}>${NC} %s\n" "$1"; }

ANCLI_DIR="/data/local/tmp/ancli"
MOD_DIR="/data/adb/modules/ancli"
KSU_BIN="/data/adb/ksu/bin"
AP_BIN="/data/adb/ap/bin"

echo "=================================================="
echo "          AnCLI Uninstaller"
echo "=================================================="

# Determine if running in interactive terminal (TTY)
INTERACTIVE=0
if [ -t 0 ] && [ -t 1 ]; then
    INTERACTIVE=1
fi

KEEP_DATA=1  # Default to keep container data during manager upgrades / non-interactive uninstall

if [ "$INTERACTIVE" -eq 1 ]; then
    printf "${Y}[?]${NC} Choose uninstallation mode:\n"
    printf "    [1] Keep my containers and configurations (Recommended for upgrading/reinstalling Magisk modules)\n"
    printf "    [2] Full Purge (Permanently delete all wrappers, configurations, and the Ubuntu container)\n"
    printf "    Choice [1/2]: "
    read -r choice
    case "$choice" in
        2) KEEP_DATA=0 ;;
        *) KEEP_DATA=1 ;;
    esac
else
    # Non-interactive mode (e.g. running from Magisk/KSU Manager uninstall tap)
    # Check if user explicitly created a file to force delete everything, otherwise keep data safe
    if [ -f "/data/local/tmp/ancli_force_purge" ]; then
        KEEP_DATA=0
    else
        KEEP_DATA=1
    fi
fi
echo ""

info "Stopping any running Proot instances..."
killall proot 2>/dev/null || true

if [ "$KEEP_DATA" -eq 1 ]; then
    info "Preserving container rootfs and configs..."
    # Only clean up the executables bin and dynamic wrappers, keeping rootfs and installed.json database
    rm -rf "$ANCLI_DIR/bin"
    ok "Cleaned core scripts in $ANCLI_DIR/bin, kept rootfs & configs."
else
    info "Removing Rootfs and Core files (This may take a while)..."
    rm -rf "$ANCLI_DIR"
    ok "Deleted $ANCLI_DIR"
fi

info "Removing Systemless Module..."
rm -rf "$MOD_DIR"
ok "Deleted $MOD_DIR"

info "Removing dynamic KSU/AP wrappers..."
if [ -d "$KSU_BIN" ]; then
    grep -rl "$ANCLI_DIR" "$KSU_BIN" 2>/dev/null | xargs rm -f 2>/dev/null || true
    ok "Cleaned KSU bin wrappers"
fi

if [ -d "$AP_BIN" ]; then
    grep -rl "$ANCLI_DIR" "$AP_BIN" 2>/dev/null | xargs rm -f 2>/dev/null || true
    ok "Cleaned Apatch bin wrappers"
fi

if [ "$KEEP_DATA" -eq 1 ]; then
    ok "AnCLI has been uninstalled. Your containers, Python packages, and API configs are SAFELY preserved."
else
    ok "AnCLI has been completely purged from your device."
fi
echo "Please reboot your phone if you wish to clear the Magisk/KSU module mount immediately."
