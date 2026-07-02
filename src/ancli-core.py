#!/usr/bin/env python3
import os
import sys
import json
import shlex
import time
import subprocess
import urllib.request
import re

# Paths inside the PRoot environment
ANCLI_DIR = "/data/local/tmp/ancli"
ROOTFS = f"{ANCLI_DIR}/rootfs"
MOD_DIR = "/data/adb/modules/ancli"

# Optional dynamic bin paths (KSU/Apatch)
KSU_BIN = "/data/adb/ksu/bin"
AP_BIN  = "/data/adb/ap/bin"

# Termux Host backend paths
TERMUX_PREFIX = "/data/data/com.termux/files/usr"

VERSION = "1.0.2"

REGISTRY_URL   = "https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/registry.json"
LOCAL_REGISTRY = "/root/.ancli-registry.json"   # persistent and writable inside proot
INSTALLED_FILE = f"{ANCLI_DIR}/installed.json"

# Allowed command prefixes for security validation
ALLOWED_CMD_PREFIXES = ("pip ", "npm ", "apt-get ", "apt ", "curl ", "rm ", "agy ", "bash ", "sh ")

# ---------------------------------------------------------------------------
# Registry & State
# ---------------------------------------------------------------------------

def fetch_registry():
    last_net_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(REGISTRY_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())
                # Try to cache locally; silently ignore if the filesystem is read-only
                # (e.g. SELinux denies writes from inside the PRoot context).
                try:
                    with open(LOCAL_REGISTRY, "w") as f:
                        json.dump(data, f)
                except OSError:
                    pass
                return data
        except Exception as e:
            last_net_err = e
            if attempt < 2:
                print(f"\033[93m[!] Retry {attempt+1}/3: {e}\033[0m")
                time.sleep(2)
    # All retries exhausted, fall back to user local cache
    if os.path.exists(LOCAL_REGISTRY):
        print(f"\033[93m[!] Using local cache (network unavailable: {last_net_err})\033[0m")
        with open(LOCAL_REGISTRY, "r") as f:
            return json.load(f)

    # Critical Edge Case: If offline and no cache exists (e.g., first install),
    # try loading the bundled fallback registry shipped with the module zip.
    fallback_path = f"{ANCLI_DIR}/bin/registry.json"
    if os.path.exists(fallback_path):
        print(f"\033[93m[!] Using bundled fallback registry (offline first boot)\033[0m")
        with open(fallback_path, "r") as f:
            return json.load(f)

    print(f"\033[91m[X] Failed to fetch registry, no cache, and no fallback found: {last_net_err}\033[0m")
    sys.exit(1)

def load_installed():
    if os.path.exists(INSTALLED_FILE):
        try:
            with open(INSTALLED_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print("\033[93m[!] Warning: installed.json corrupted, resetting.\033[0m")
            return {}
    return {}

def save_installed(installed):
    """Save installed apps metadata to local state JSON file."""
    tmp_file = f"{INSTALLED_FILE}.tmp"
    try:
        # Check if old tmp exists and delete it to prevent permission error
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass
        with open(tmp_file, "w") as f:
            json.dump(installed, f, indent=2)

        # Replace atomically
        if os.path.exists(INSTALLED_FILE):
            try:
                os.remove(INSTALLED_FILE)
            except Exception:
                pass
        os.rename(tmp_file, INSTALLED_FILE)
        # Ensure correct permission and ownership.
        # Use Android shell UID/GID (2000:2000) numerically for reliability
        # since 'shell' username may not exist inside the proot container's /etc/passwd.
        try:
            os.system(f"chown 2000:2000 {INSTALLED_FILE} 2>/dev/null")
            os.chmod(INSTALLED_FILE, 0o666)
        except Exception:
            pass
    except Exception as e:
        print(f"\033[91m[X] Failed to save installation database: {e}\033[0m")
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass

def generate_proot_wrapper(executable, env_dict=None, runtime_env_list=None):
    """Generate a wrapper that routes execution into the Ubuntu PRoot container."""
    if '/' in executable or '\\' in executable or '..' in executable:
        print(f"\033[91m[X] Invalid executable name: {executable}\033[0m")
        return

    exports = ""
    # Inject static runtime env vars from registry (e.g., HOME override for npm tools)
    if runtime_env_list:
        for item in runtime_env_list:
            if '=' in item:
                key, _, val = item.partition('=')
                if key.replace('_', '').isalnum():
                    exports += f'export {key}={shlex.quote(val)}\n'
    # Inject user-supplied env vars (e.g., API keys)
    if env_dict:
        for k, v in env_dict.items():
            exports += f"export {k}='{v}'\n"

    # Build proot bind arguments.
    # Avoid duplicate bind if $PWD happens to be /sdcard or its submount.
    # We use a shell variable to deduplicate at runtime.
    wrapper = f"""#!/system/bin/sh
# AnCLI wrapper for: {executable}

# --- Android WiFi proxy detection & inheritance ---
PROXY_INFO=$(dumpsys connectivity 2>/dev/null | grep -i 'HttpProxy:' | head -n 1)
if [ -n "$PROXY_INFO" ]; then
    PROXY_HOST=$(echo "$PROXY_INFO" | sed -n 's/.*HttpProxy:[[:space:]]*\\[\\([^ ]*\\)\\].*/\\1/p')
    PROXY_PORT=$(echo "$PROXY_INFO" | sed -ne 's/.*HttpProxy:[[:space:]]*\\[[^ ]*\\][[:space:]]*\\([0-9]*\\).*/\\1/p')
    if [ -n "$PROXY_HOST" ] && [ -n "$PROXY_PORT" ]; then
        export http_proxy="http://$PROXY_HOST:$PROXY_PORT"
        export https_proxy="http://$PROXY_HOST:$PROXY_PORT"
    fi
fi

{exports}export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin
export HOME=/root
# Force Go runtime to use its own DNS resolver, bypassing Android dnsproxyd hijacking.
export GODEBUG=netdns=go

# Auto-bind potential Clash/Tun virtual IPs to local loopback to satisfy Go socket bind traversal.
for i in $(seq 10 25); do
    ip addr add 198.18.0.$i/32 dev lo 2>/dev/null || true
done

# Fix ownership of agy/gemini/claude auth credential directories on every launch.
# This prevents root-locked files from blocking subsequent shell-user runs.
for _conf_dir in /root/.config /root/.gemini /root/.claude /root/.local; do
    if [ -d "{ROOTFS}$_conf_dir" ]; then
        chown -R 2000:2000 "{ROOTFS}$_conf_dir" 2>/dev/null || true
        chmod -R 755 "{ROOTFS}$_conf_dir" 2>/dev/null || true
    fi
done

# Determine working directory and launch proot accordingly.
# Only treat $PWD as valid if it starts with '/' (Android absolute path).
# Windows paths, empty values, or /root all fall back to container's /root.
case "$PWD" in
    /sdcard|/sdcard/*|/storage/emulated/0|/storage/emulated/0/*)
        exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} -b /sdcard -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb -w "$PWD" /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin HOME=/root GODEBUG=netdns=go {executable} "$@"
        ;;
    /root|/)
        exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} -b /sdcard -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin HOME=/root GODEBUG=netdns=go {executable} "$@"
        ;;
    /*)
        exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} -b /sdcard -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb -b "$PWD" -w "$PWD" /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin HOME=/root GODEBUG=netdns=go {executable} "$@"
        ;;
    *)
        exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} -b /sdcard -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin HOME=/root GODEBUG=netdns=go {executable} "$@"
        ;;
esac
"""
    _write_wrapper_to_paths(executable, wrapper)

def _write_wrapper_to_paths(executable, wrapper):
    """Write a wrapper script to the systemless module path and all instant-access paths."""
    # Also write to ANCLI_DIR/bin for direct 'sh /data/local/tmp/ancli/bin/<cmd>' invocation
    # (bypasses noexec restrictions on /data/local/tmp without requiring a reboot).
    ancli_bin_path = f"{ANCLI_DIR}/bin/{executable}"
    try:
        if os.path.exists(ancli_bin_path):
            os.remove(ancli_bin_path)
        with open(ancli_bin_path, "w") as f:
            f.write(wrapper)
        os.chmod(ancli_bin_path, 0o755)
        print(f"\033[92m[OK] Written: {ancli_bin_path}\033[0m")
    except Exception as e:
        print(f"\033[93m[!] Warning: Could not write to {ancli_bin_path}: {e}\033[0m")

    # 1. Systemless module path (takes effect after reboot via Magisk/KSU overlay)
    sys_bin  = f"{MOD_DIR}/system/bin"
    sys_path = f"{sys_bin}/{executable}"
    try:
        os.makedirs(sys_bin, exist_ok=True)
        if os.path.exists(sys_path):
            os.remove(sys_path)
        with open(sys_path, "w") as f:
            f.write(wrapper)
        os.chmod(sys_path, 0o755)
    except Exception as e:
        print(f"\033[93m[!] Warning: Could not write systemless wrapper to {sys_path}: {e}\033[0m")

    # 2. Instant-access paths (KSU / APatch), take effect immediately without reboot.
    # Python open() inside proot is blocked by SELinux for /data/adb paths.
    # Strategy: write to a tmp file in ANCLI_DIR (always writable from proot), then
    # use os.system('cp') which forks a host-side root shell that can write to /data/adb.
    tmp_wrapper = f"{ANCLI_DIR}/bin/.{executable}.tmp"
    try:
        with open(tmp_wrapper, "w") as f:
            f.write(wrapper)
        os.chmod(tmp_wrapper, 0o755)
        for instant_bin in [KSU_BIN, AP_BIN]:
            if os.path.isdir(instant_bin):
                inst_path = f"{instant_bin}/{executable}"
                ret = os.system(f"cp -f {tmp_wrapper} {inst_path} 2>/dev/null && chmod 755 {inst_path} 2>/dev/null")
                if ret == 0:
                    print(f"\033[92m[OK] Instant wrapper updated: {inst_path}\033[0m")
                else:
                    # If cp fails (SELinux restricts new file creation), the pre-seeded
                    # placeholder from customize.sh will route the command anyway.
                    if os.path.exists(inst_path):
                        print(f"\033[92m[OK] Instant wrapper ready (via placeholder): {inst_path}\033[0m")
                    else:
                        print(f"\033[93m[!] Warning: Could not write instant wrapper to {inst_path}\033[0m")
                        print(f"    (The tool will be globally available after next reboot)\033[0m")
    except Exception as e:
        print(f"\033[93m[!] Warning: Could not prepare instant wrapper for {executable}: {e}\033[0m")
    finally:
        try:
            os.remove(tmp_wrapper)
        except Exception:
            pass

def remove_wrapper(executable):
    paths = [
        f"{ANCLI_DIR}/bin/{executable}",
        f"{MOD_DIR}/system/bin/{executable}",
        f"{KSU_BIN}/{executable}",
        f"{AP_BIN}/{executable}",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def validate_cmd(cmd):
    """Security: verify command starts with an allowed prefix and has no shell operators."""
    cmd_stripped = cmd.strip()
    for operator in ['`', '$(']:
        if operator in cmd_stripped:
            print(f"\033[91m[X] Blocked command with shell operator '{operator}': {cmd_stripped}\033[0m")
            return False
    if not any(cmd_stripped.startswith(prefix) for prefix in ALLOWED_CMD_PREFIXES):
        print(f"\033[91m[X] Blocked untrusted command: {cmd_stripped}\033[0m")
        print(f"\033[93m    Allowed prefixes: {', '.join(ALLOWED_CMD_PREFIXES)}\033[0m")
        return False
    return True

def run_cmd(cmd):
    if not validate_cmd(cmd):
        return False
    print(f"\033[96m> {cmd}\033[0m")
    # Execute directly in container's bash to support pipefail and bypass nested proot conflicts
    result = subprocess.run(
        f"set -o pipefail; {cmd}",
        shell=True,
        executable="/bin/bash"
    )
    return result.returncode == 0

# ---------------------------------------------------------------------------
# Install / Uninstall / Update / Config / Repair
# ---------------------------------------------------------------------------

def _fix_config_permissions():
    """Fix ownership of auth config directories so both root and shell users can access them."""
    root_dir = f"{ROOTFS}/root"
    if not os.path.isdir(root_dir):
        return
    # chmod the root home dir itself so shell user can enter it
    os.system(f"chmod 755 {root_dir} 2>/dev/null")
    for conf_dir in [".config", ".claude", ".gemini", ".local"]:
        full_path = f"{root_dir}/{conf_dir}"
        if os.path.exists(full_path):
            # Use numeric UID 2000 (Android shell) for reliability
            os.system(f"chown -R 2000:2000 {full_path} 2>/dev/null")
            os.system(f"chmod -R 755 {full_path} 2>/dev/null")

def repair_env(registry):
    """Diagnose and fix container environment, resolv.conf, permissions, and wrappers."""
    print("\033[96m[*] Starting environment diagnostics and repair...\033[0m")

    # Guard: ROOTFS must be a real path before doing any recursive operations
    if not ROOTFS or not os.path.isabs(ROOTFS) or len(ROOTFS) < 10:
        print(f"\033[91m[X] Refusing to operate: ROOTFS path looks invalid: {ROOTFS}\033[0m")
        return

    # 1. Fix resolv.conf DNS
    resolv_path = f"{ROOTFS}/etc/resolv.conf"
    try:
        # Check if resolv.conf is a symlink, remove it if so to write actual file
        if os.path.islink(resolv_path):
            os.remove(resolv_path)
        with open(resolv_path, "w") as f:
            f.write("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
        print("\033[92m[OK] Container DNS configuration repaired (/etc/resolv.conf).\033[0m")
    except Exception as e:
        print(f"\033[91m[X] Failed to repair DNS: {e}\033[0m")

    # 1.1 Fix/Create custom pure hosts file (used as isolated /etc/hosts inside proot)
    hosts_path = f"{ANCLI_DIR}/hosts"
    try:
        with open(hosts_path, "w") as f:
            f.write("127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n")
        os.chmod(hosts_path, 0o644)
        print("\033[92m[OK] Pure custom hosts template created.\033[0m")
    except Exception as e:
        print(f"\033[91m[X] Failed to create hosts template: {e}\033[0m")

    # 2. Fix auth config directory permissions (key fix for agy re-launch failures)
    print("\033[96m[*] Repairing auth credential directory permissions...\033[0m")
    _fix_config_permissions()
    print("\033[92m[OK] Auth config folder permissions and ownership restored.\033[0m")

    # 3. Repair proot and ancli-core.py executable permissions
    try:
        proot_path = f"{ANCLI_DIR}/bin/proot"
        if os.path.exists(proot_path):
            os.chmod(proot_path, 0o755)
        # Fix binary installation folders — use 755 not 777 for security
        for bin_dir in ["/usr/local/bin", "/usr/bin", "/bin"]:
            full_bin = f"{ROOTFS}{bin_dir}"
            if os.path.isdir(full_bin):
                # chown to root:root (0:0) inside container is correct for system bins
                os.system(f"chmod -R 755 {full_bin} 2>/dev/null")
        print("\033[92m[OK] Key binary executable permissions restored (0755).\033[0m")
    except Exception as e:
        print(f"\033[91m[X] Failed to restore binary permissions: {e}\033[0m")

    # 4. Repair missing application wrappers
    installed = load_installed()
    if installed:
        print(f"\033[96m[*] Verifying wrappers for installed apps: {', '.join(installed.keys())}...\033[0m")
        if not registry:
            try:
                registry = fetch_registry()
            except Exception:
                pass

        for app_id, info in installed.items():
            exec_name = info.get('executable', app_id)
            # Check each candidate path — only consider it "missing" if the
            # parent directory actually exists (same logic as _write_wrapper_to_paths)
            candidate_paths = [
                f"{ANCLI_DIR}/bin/{exec_name}",
                f"{MOD_DIR}/system/bin/{exec_name}",
                f"{KSU_BIN}/{exec_name}",
                f"{AP_BIN}/{exec_name}",
            ]
            needs_recreate = False
            for wp in candidate_paths:
                parent = os.path.dirname(wp)
                if os.path.isdir(parent) and not os.path.exists(wp):
                    needs_recreate = True
                    break

            if needs_recreate:
                print(f"  \033[93m[!] Wrapper for '{app_id}' missing, regenerating...\033[0m")
                app_reg = registry['apps'].get(app_id) if registry else None
                runtime_env = app_reg.get('runtime_env', []) if app_reg else []
                stored_env = info.get('env', {})
                generate_proot_wrapper(exec_name, stored_env if stored_env else None, runtime_env)
        print("\033[92m[OK] Wrapper integrity check complete.\033[0m")
    else:
        print("\033[90m[i] No installed apps to repair.\033[0m")

    print("\033[92m[OK] Repair complete! If problems persist, try: ancli config <app_id>\033[0m")

def _install_proot_common(app_id, app, registry_version="unknown"):
    """Shared post-install bookkeeping: write wrapper and save state."""
    runtime_env = app.get('runtime_env', [])
    generate_proot_wrapper(app['executable'], {}, runtime_env)
    installed = load_installed()
    installed[app_id] = {
        "name": app['name'],
        "executable": app['executable'],
        "install_mode": "proot",
        "installed_version": registry_version,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "env": {},
    }
    save_installed(installed)
    # Fix permissions immediately so the tool is usable without a reboot
    _fix_config_permissions()
    print(f"\033[92m[OK] Successfully installed {app['name']}! Type '{app['executable']}' to run.\033[0m")
    print(f"\033[93m[i] Configure API keys anytime with: ancli config {app_id}\033[0m")

def install_app(app_id, registry):
    if app_id not in registry['apps']:
        print(f"\033[91m[X] App {app_id} not found in registry.\033[0m")
        return
    app = registry['apps'][app_id]
    reg_ver = app.get('version', registry.get('version', 'unknown'))

    print(f"\033[92m[*] Installing {app['name']}...\033[0m")
    print(f"\033[96m[i] Backend: proot\033[0m")
    _install_proot(app_id, app, reg_ver)

def _install_proot(app_id, app, registry_version="unknown"):
    """Install a tool inside the Ubuntu PRoot container."""
    import shutil
    # Ensure 'curl' and 'ca-certificates' exist in container before executing any install commands
    if not shutil.which("curl"):
        print("\033[96m[i] Container is missing 'curl'. Auto-installing dependencies via apt...\033[0m")
        apt_cmd = "apt-get update -qy && apt-get install -qy curl ca-certificates"
        if not run_cmd(apt_cmd):
            print("\033[91m[X] Failed to install 'curl' inside container. Installation might fail.\033[0m")
        else:
            print("\033[92m[OK] 'curl' and certificates installed successfully.\033[0m")

    # --- agy: bypass ADB shell piping/escaping by downloading via Python urllib ---
    # The registry install_cmd for agy uses `curl | bash` which fails under adb due to
    # shell escaping. We download the script natively and execute it as a local file.
    if app_id == "agy":
        try:
            print("\033[96m[*] Downloading Antigravity installer via Python to bypass pipe escaping...\033[0m")
            installer_url = "https://antigravity.google/cli/install.sh"

            # Propagate proxy if set in process environment
            proxy_url = (os.environ.get('http_proxy') or os.environ.get('https_proxy')
                         or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'))
            if proxy_url:
                handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
                opener  = urllib.request.build_opener(handler)
                urllib.request.install_opener(opener)

            req = urllib.request.Request(installer_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read()
                os.makedirs("/tmp", exist_ok=True)
                with open("/tmp/install_agy.sh", "wb") as f:
                    f.write(content)

            if run_cmd("bash /tmp/install_agy.sh --dir /usr/local/bin"):
                _install_proot_common(app_id, app, registry_version)
                return
            else:
                print("\033[91m[X] agy installer script failed.\033[0m")
                return
        except Exception as e:
            print(f"\033[91m[X] Python downloader failed: {e}\033[0m")
            print("\033[93m[!] Falling back to standard shell installer...\033[0m")

    # --- Generic proot install path ---
    if run_cmd(app['install_cmd']):
        _install_proot_common(app_id, app, registry_version)
    else:
        print(f"\033[91m[X] Installation failed.\033[0m")


def uninstall_app(app_id, registry):
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return
    app = registry['apps'].get(app_id, {})
    cmd = app.get('uninstall_cmd', f"echo 'No uninstall cmd for {app_id}'")
    print(f"\033[93m[*] Uninstalling {app.get('name', app_id)}...\033[0m")

    run_cmd(cmd)

    remove_wrapper(app.get('executable', app_id))
    del installed[app_id]
    save_installed(installed)
    print(f"\033[92m[OK] Successfully uninstalled.\033[0m")

def update_app(app_id, registry):
    """Update an installed app, regenerate wrapper using cached env, and update version."""
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return
    app = registry['apps'].get(app_id, {})
    cmd = app.get('update_cmd', f"echo 'No update cmd for {app_id}'")
    print(f"\033[93m[*] Updating {app.get('name', app_id)}...\033[0m")

    if run_cmd(cmd):
        cached_env = installed[app_id].get('env', {})
        if cached_env:
            print(f"\033[92m[i] Re-injecting stored configuration keys: {', '.join(cached_env.keys())}\033[0m")

        runtime_env = app.get('runtime_env', [])
        generate_proot_wrapper(app.get('executable', app_id), cached_env if cached_env else None, runtime_env)

        reg_ver = app.get('version', registry.get('version', 'unknown'))
        installed[app_id]['installed_version'] = reg_ver
        installed[app_id]['installed_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_installed(installed)
        _fix_config_permissions()
        print(f"\033[92m[OK] Successfully updated {app.get('name', app_id)} to v{reg_ver}.\033[0m")

def reconfigure_app(app_id, registry):
    """Reconfigure env vars and regenerate wrapper for an installed app."""
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return
    app = registry['apps'].get(app_id, {})
    env_dict = {}
    all_vars = app.get('env_vars', []) + app.get('optional_env_vars', [])
    if not all_vars:
        print(f"\033[93m[!] No configurable env vars for {app_id}.\033[0m")
        return
    print(f"\033[96m[*] Reconfiguring {app.get('name', app_id)}...\033[0m")
    for var in all_vars:
        prev_val = installed[app_id].get('env', {}).get(var, '')
        hint = f" [{prev_val}]" if prev_val else ""
        val = input(f"\033[96mEnter {var}{hint} (leave blank to keep/skip): \033[0m").strip()

        if val:
            env_dict[var] = val
        elif prev_val:
            env_dict[var] = prev_val  # Keep existing value if skipped

    runtime_env = app.get('runtime_env', [])
    generate_proot_wrapper(app.get('executable', app_id), env_dict if env_dict else None, runtime_env)

    installed[app_id]['env'] = env_dict
    save_installed(installed)
    print(f"\033[92m[OK] Reconfigured and wrapper regenerated.\033[0m")

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def show_menu():
    registry = fetch_registry()

    while True:
        installed = load_installed()
        print("\n\033[1;36m=== 🚀 AnCLI App Store ===\033[0m")
        apps = list(registry['apps'].keys())
        for i, app_id in enumerate(apps, 1):
            app = registry['apps'][app_id]
            mode_tag = f"\033[90m[{app.get('install_mode', 'proot')}]\033[0m"
            status = "\033[92m[Installed]\033[0m" if app_id in installed else ""
            print(f"[{i}] {app['name']} {mode_tag} - {app['description']} {status}")

        print("[r] Repair environment (Fix DNS, permissions, wrappers)")
        print("[u] Uninstall an app")
        print("[0] Exit")
        choice = input("\033[93mChoose an option: \033[0m").strip()

        if choice == '0':
            break
        elif choice.lower() == 'r':
            repair_env(registry)
        elif choice.lower() == 'u':
            if not installed:
                print("\033[91mNo apps installed yet.\033[0m")
                continue
            print("\n\033[1;36mSelect app to uninstall:\033[0m")
            installed_list = list(installed.keys())
            for i, app_id in enumerate(installed_list, 1):
                app_name = registry['apps'].get(app_id, {}).get('name', app_id)
                print(f"[{i}] {app_name}")
            sub = input("Enter number to uninstall (0 to cancel): ").strip()
            if sub.isdigit() and 1 <= int(sub) <= len(installed_list):
                uninstall_app(installed_list[int(sub)-1], registry)
        elif choice.isdigit() and 1 <= int(choice) <= len(apps):
            app_id = apps[int(choice)-1]
            if app_id in installed:
                print(f"\n\033[96m=== Manage: {app_id} ===\n[1] Update\n[2] Uninstall\n[3] Reconfigure env vars\n[0] Cancel\033[0m")
                sub = input("Action: ").strip()
                if sub == '1':
                    update_app(app_id, registry)
                elif sub == '2':
                    uninstall_app(app_id, registry)
                elif sub == '3':
                    reconfigure_app(app_id, registry)
            else:
                install_app(app_id, registry)
        else:
            print("\033[91mInvalid choice.\033[0m")

def print_help():
    print("""\033[1;36mAnCLI (Android CLI) - Unified Systemless CLI Environment Manager\033[0m

\033[1mUsage:\033[0m
  ancli                          Open interactive App Store menu
  ancli install <app_id>         Install an app from the registry
  ancli uninstall <app_id>       Uninstall an installed app
  ancli update <app_id>          Update an installed app
  ancli config <app_id>          Reconfigure env vars for an app
  ancli list                     List all installed apps
  ancli repair                   Detect and repair DNS, permissions, and wrappers
  ancli --version                Show version
  ancli --help                   Show this help message

\033[1mExecution Backend:\033[0m
  All tools run inside the Ubuntu PRoot container at:
    /data/local/tmp/ancli/rootfs

\033[1mDirect Invocation (bypasses noexec):\033[0m
  sh /data/local/tmp/ancli/bin/agy
  sh /data/local/tmp/ancli/bin/claude
  sh /data/local/tmp/ancli/bin/mimo

\033[1mExamples:\033[0m
  ancli install aider
  ancli install agy
  ancli config aider
  ancli list
""")

# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            action = sys.argv[1]
            app_id = sys.argv[2] if len(sys.argv) > 2 else None

            if action in ("--help", "-h"):
                print_help()
                sys.exit(0)
            elif action == "--version":
                print(f"AnCLI v{VERSION}")
                sys.exit(0)

            registry = fetch_registry()
            if action == "install" and app_id:
                install_app(app_id, registry)
            elif action == "uninstall" and app_id:
                uninstall_app(app_id, registry)
            elif action == "update" and app_id:
                update_app(app_id, registry)
            elif action == "config" and app_id:
                reconfigure_app(app_id, registry)
            elif action == "repair":
                repair_env(registry)
            elif action == "list":
                installed = load_installed()
                if not installed:
                    print("\033[93m[i] No apps installed yet. Run 'ancli' to browse the App Store.\033[0m")
                else:
                    print("\033[1;36m=== Installed Apps ===\033[0m")
                    for aid, info in installed.items():
                        date      = info.get('installed_at', 'unknown')
                        local_ver = info.get('installed_version', 'unknown')

                        # Integrity check: verify binary exists inside PRoot
                        exec_name = info.get('executable', aid)
                        bin_exists = (
                            os.path.exists(f"{ROOTFS}/usr/local/bin/{exec_name}") or
                            os.path.exists(f"{ROOTFS}/usr/bin/{exec_name}") or
                            os.path.exists(f"{ROOTFS}/root/.local/bin/{exec_name}")
                        )
                        status_tag = "\033[92m[Active]\033[0m" if bin_exists else "\033[91m[Broken: Missing Binary]\033[0m"

                        # Check for update available
                        update_tag = ""
                        if registry and aid in registry['apps']:
                            cloud_ver = registry['apps'][aid].get('version', registry.get('version', 'unknown'))
                            if cloud_ver != 'unknown' and local_ver != 'unknown' and cloud_ver != local_ver:
                                update_tag = f" \033[93m(Update Available: v{cloud_ver})\033[0m"

                        print(f"  \033[92m{aid}\033[0m: {info.get('name', aid)} (v{local_ver}) {status_tag}{update_tag}")
                        print(f"    Installed: {date}")
                        persisted_keys = list(info.get('env', {}).keys())
                        if persisted_keys:
                            print(f"    Configured keys: {', '.join(persisted_keys)}")
            else:
                print_help()
        else:
            show_menu()
    except (KeyboardInterrupt, EOFError):
        print("\n\033[93m[!] Operation cancelled by user. Exiting.\033[0m")
        sys.exit(0)
