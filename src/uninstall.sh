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

printf "${Y}[!]${NC} This will PERMANENTLY delete AnCLI and ALL installed tools.\n"
printf "    Continue? [y/N] "
read -r confirm
case "$confirm" in
    [yY]) ;;
    *) echo "Aborted."; exit 0 ;;
esac
echo ""

info "Stopping any running Proot instances..."
killall proot 2>/dev/null || true

info "Removing Rootfs and Core files (This may take a while)..."
rm -rf "$ANCLI_DIR"
ok "Deleted $ANCLI_DIR"

info "Removing Systemless Module..."
rm -rf "$MOD_DIR"
ok "Deleted $MOD_DIR"

info "Removing dynamic KSU/AP wrappers..."
if [ -d "$KSU_BIN" ]; then
    # Remove any scripts pointing to ancli dir
    grep -rl "$ANCLI_DIR" "$KSU_BIN" 2>/dev/null | xargs rm -f 2>/dev/null || true
    ok "Cleaned KSU bin wrappers"
fi

if [ -d "$AP_BIN" ]; then
    grep -rl "$ANCLI_DIR" "$AP_BIN" 2>/dev/null | xargs rm -f 2>/dev/null || true
    ok "Cleaned Apatch bin wrappers"
fi

ok "AnCLI has been completely uninstalled from your device."
echo "Please reboot your phone if you wish to clear the Magisk/KSU module mount immediately."
