#!/system/bin/sh
# ============================================================
# AnCLI Module Installer (customize.sh)
# Runs automatically when module ZIP is flashed via Manager
# ============================================================

# Framework provides: $MODPATH, ui_print(), abort(), set_perm()
ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"
BIN_DIR="${ANCLI_DIR}/bin"
UBUNTU_MIRROR="${ANCLI_MIRROR:-mirrors.tuna.tsinghua.edu.cn}"

UBUNTU_PATH="ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-arm64.tar.gz"

ui_print "============================================"
ui_print "  AnCLI Bootstrap Installer v1.2.2"
ui_print "============================================"
ui_print ""

# 1. Prepare directories
ui_print ">> Preparing directories..."
mkdir -p "$ROOTFS" "$BIN_DIR"

# 2. Deploy PRoot from module package
ui_print ">> Deploying PRoot..."
cp "$MODPATH/bin/proot" "$BIN_DIR/proot"
chmod 755 "$BIN_DIR/proot"
ui_print ">> PRoot deployed successfully."

# 3. Download & Extract Ubuntu Base
if [ ! -f "$ROOTFS/bin/bash" ]; then
    ui_print ">> Downloading Ubuntu Base (arm64)..."
    TAR_PATH="$ANCLI_DIR/ubuntu-base.tar.gz"
    success=0
    
    # Try Tsinghua TUNA first, then fallback to USTC and official ports mirror
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

    # Fix DNS inside rootfs
    echo "nameserver 8.8.8.8" > "$ROOTFS/etc/resolv.conf"
    echo "nameserver 1.1.1.1" >> "$ROOTFS/etc/resolv.conf"
    ui_print ">> Rootfs extracted and configured."
else
    ui_print ">> Rootfs already present, skipping download."
fi

# 4. Install APT Dependencies via PRoot (with idempotency guard)
PROOT_CMD="$BIN_DIR/proot -r $ROOTFS -b /dev -b /proc -b /sys -w /root"

if ! $PROOT_CMD /usr/bin/python3 --version >/dev/null 2>&1; then
    ui_print ">> Bootstrapping APT dependencies (Python, Git, Node.js)..."

    # Pre-configure APT mirror from host side
    if [ -f "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" ]; then
        sed -i "s/ports.ubuntu.com/${UBUNTU_MIRROR}/g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s/archive.ubuntu.com/${UBUNTU_MIRROR}/g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        ui_print ">> APT mirror: ${UBUNTU_MIRROR}"
    fi

    # Detect system locale to determine whether CJK input support (Fcitx5) is needed.
    # Fcitx5 + fcitx5-chinese-addons add ~50 MB to the bootstrap image and are only
    # useful for CJK-locale (Chinese / Japanese / Korean) devices.
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

    cat > "$ROOTFS/root/setup.sh" << SETUP
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

# Run update and install with GPG verification disabled to bypass PRoot syscall compatibility issues
apt-get update -y -o Acquire::AllowInsecureRepositories=true -o Acquire::AllowUnauthenticated=true
apt-get install -y --no-install-recommends \\
    -o Acquire::AllowInsecureRepositories=true \\
    -o Acquire::AllowUnauthenticated=true \\
    --allow-unauthenticated \\
    ca-certificates curl python3 python3-pip git nodejs npm $FCITX_PKGS
apt-get clean
SETUP
    chmod 755 "$ROOTFS/root/setup.sh"
    $PROOT_CMD /root/setup.sh || abort "APT bootstrap failed"
    rm -f "$ROOTFS/root/setup.sh"
    ui_print ">> Dependencies installed successfully."
else
    ui_print ">> Dependencies already installed, skipping APT."
fi

# 4b. Ensure /usr/local directory structure exists for npm global installs
#     Ubuntu Base 24.04 does not create /usr/local by default when nodejs/npm
#     are installed via apt. npm -g requires /usr/local/lib/node_modules to exist.
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

# 5. Deploy AnCLI Core from module package
ui_print ">> Deploying AnCLI Core..."
cp "$MODPATH/ancli/ancli-core.py" "$BIN_DIR/ancli-core.py"
cp "$MODPATH/ancli/ancli_env.sh" "$BIN_DIR/ancli_env.sh"
chmod 755 "$BIN_DIR/ancli-core.py" "$BIN_DIR/ancli_env.sh"

# Deploy bundled fallback registry
cp "$MODPATH/ancli/registry.json" "$ANCLI_DIR/registry.json" 2>/dev/null || true


# 6. Instant access injection for KSU/AP dynamic paths.
#    We inject both 'ancli' and placeholder wrappers for all registry apps.
#    SELinux only permits overwriting existing files in /data/adb/ksu/bin after boot,
#    so we MUST pre-create all app entries now (during flash) when the context allows it.
#    The real wrappers will be written by 'ancli install' or 'ancli repair' later.
REGISTRY_FILE="$MODPATH/ancli/registry.json"

for INSTANT_BIN in /data/adb/ksu/bin /data/adb/ap/bin; do
    [ -d "$INSTANT_BIN" ] || continue

    # Inject main ancli command
    cp "$MODPATH/system/bin/ancli" "$INSTANT_BIN/ancli"
    chmod 755 "$INSTANT_BIN/ancli"
    ui_print ">> Instant access: $INSTANT_BIN/ancli"

    # Pre-seed placeholder wrappers for all registry apps
    if [ -f "$REGISTRY_FILE" ]; then
        # Extract executable names from registry JSON using basic shell parsing
        executables=$(grep '"executable"' "$REGISTRY_FILE" | sed 's/.*"executable"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
        for exe in $executables; do
            placeholder="$INSTANT_BIN/$exe"
            if [ ! -f "$placeholder" ]; then
                # Write a minimal placeholder — will be overwritten by ancli install/repair
                printf '#!/system/bin/sh\nexec sh /data/local/tmp/ancli/bin/%s "$@"\n' "$exe" > "$placeholder"
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
