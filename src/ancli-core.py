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
AP_BIN = "/data/adb/ap/bin"

# Termux Host backend paths
TERMUX_PREFIX = "/data/data/com.termux/files/usr"

VERSION = "1.0.1"

REGISTRY_URL = "https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/registry.json"
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
    # Atomic write: write to temp file first, then rename
    tmp_file = INSTALLED_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(installed, f, indent=2)
    os.replace(tmp_file, INSTALLED_FILE)

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

    # Injected env vars and dynamic WiFi system proxy auto-inheritance logic on Android Host
# Injected env vars and dynamic WiFi system proxy auto-inheritance logic on Android Host
    wrapper = f"""#!/system/bin/sh
# Dynamic Android Host WiFi system proxy detection & inheritance
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
exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} -b /sdcard -b "$PWD" -w "$PWD" /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin HOME=/root {executable} "$@"
"""
    _write_wrapper_to_paths(executable, wrapper)

def _write_wrapper_to_paths(executable, wrapper):
    """Write a wrapper script to the systemless module path and all instant-access paths."""
    # 1. Systemless module (post-reboot)
    sys_bin = f"{MOD_DIR}/system/bin"
    os.makedirs(sys_bin, exist_ok=True)
    sys_path = f"{sys_bin}/{executable}"
    try:
        if os.path.exists(sys_path):
            os.remove(sys_path)
        with open(sys_path, "w") as f:
            f.write(wrapper)
        os.chmod(sys_path, 0o755)
    except Exception as e:
        print(f"\033[93m[!] Warning: Could not write systemless wrapper to {sys_path}: {e}\033[0m")

    # 2. Instant access (KSU / APatch)
    for instant_bin in [KSU_BIN, AP_BIN]:
        if os.path.isdir(instant_bin):
            try:
                inst_path = f"{instant_bin}/{executable}"
                if os.path.exists(inst_path):
                    os.remove(inst_path)
                with open(inst_path, "w") as f:
                    f.write(wrapper)
                os.chmod(inst_path, 0o755)
            except Exception as e:
                # Log warning but do not crash the installer
                print(f"\033[93m[!] Warning: Could not write instant wrapper to {instant_bin}: {e}\033[0m")
                print(f"    (The tool will still work after next reboot)\033[0m")

def remove_wrapper(executable):
    paths = [
        f"{MOD_DIR}/system/bin/{executable}",
        f"{KSU_BIN}/{executable}",
        f"{AP_BIN}/{executable}",
    ]
    for p in paths:
        if os.path.exists(p):
            os.remove(p)

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

def repair_env(registry=None):
    """Detect and repair environment issues (DNS, permissions, missing wrappers)."""
    print("\033[96m[*] Starting environment diagnostics and repair...\033[0m")
    
    # 1. Repair DNS resolv.conf in rootfs
    try:
        resolv_dir = f"{ROOTFS}/etc"
        os.makedirs(resolv_dir, exist_ok=True)
        with open(f"{resolv_dir}/resolv.conf", "w") as f:
            f.write("nameserver 8.8.8.8\nnameserver 114.114.114.114\n")
        print("\033[92m[OK] Container DNS configuration repaired (/etc/resolv.conf).\033[0m")
    except Exception as e:
        print(f"\033[91m[X] Failed to repair DNS: {e}\033[0m")

    # 2. Repair executable permissions
    try:
        proot_path = f"{ANCLI_DIR}/bin/proot"
        if os.path.exists(proot_path):
            os.chmod(proot_path, 0o755)
        core_path = f"{ANCLI_DIR}/bin/ancli-core.py"
        if os.path.exists(core_path):
            os.chmod(core_path, 0o755)
        print("\033[92m[OK] Key binary executable permissions restored (0755).\033[0m")
    except Exception as e:
        print(f"\033[91m[X] Failed to repair permissions: {e}\033[0m")

    # 3. Repair missing application wrappers
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
            # Check if wrapper is missing in KSU, AP, or module path
            wrapper_paths = [
                f"{MOD_DIR}/system/bin/{exec_name}",
                f"{KSU_BIN}/{exec_name}",
                f"{AP_BIN}/{exec_name}"
            ]
            needs_recreate = False
            for wp in wrapper_paths:
                # If path parent exists, the file itself should exist
                if os.path.isdir(os.path.dirname(wp)) and not os.path.exists(wp):
                    needs_recreate = True
                    break
            
            if needs_recreate:
                print(f"  \033[93m[!] Wrapper for '{app_id}' missing, regenerating...\033[0m")
                app_reg = registry['apps'].get(app_id) if registry else None
                runtime_env = app_reg.get('runtime_env', []) if app_reg else []
                # Use stored environment config if available
                stored_env = info.get('env', {})
                generate_proot_wrapper(exec_name, stored_env if stored_env else None, runtime_env)
        print("\033[92m[OK] Wrapper integrity check complete.\033[0m")
    else:
        print("\033[90m[i] No installed apps to repair.\033[0m")

    print("\033[92m[OK] Repair complete! If problems persist, try: ancli config <app_id>\033[0m")

def install_app(app_id, registry):
    if app_id not in registry['apps']:
        print(f"\033[91m[X] App {app_id} not found in registry.\033[0m")
        return
    app = registry['apps'][app_id]
    install_mode = app.get('install_mode', 'proot')

    print(f"\033[92m[*] Installing {app['name']}...\033[0m")
    print(f"\033[96m[i] Backend: proot\033[0m")
    # Fetch registry version if available (registry may have version info, e.g. registry['version'] or we assume unknown)
    # The registry version is often stored at registry['version'] but let's check registry['apps'][app_id].get('version')
    reg_ver = app.get('version', registry.get('version', 'unknown'))
    _install_proot(app_id, app, reg_ver)

def _install_termux(app_id, app):
    """Install a Node.js tool via the Termux host runtime."""
    termux_bin, npm_path = check_termux()
    if not termux_bin:
        print(f"\033[91m[X] Termux not found at {TERMUX_PREFIX}\033[0m")
        print(f"\033[93m[!] Node.js tools require Termux. Please:\033[0m")
        print(f"\033[93m    1. Install Termux from https://termux.dev\033[0m")
        print(f"\033[93m    2. Open Termux and run: pkg install nodejs\033[0m")
        print(f"\033[93m    3. Then re-run: ancli install {app_id}\033[0m")
        return

    if run_termux_cmd(app['install_cmd']):
        generate_termux_wrapper(app['executable'], {})
        installed = load_installed()
        installed[app_id] = {
            "name": app['name'],
            "executable": app['executable'],
            "install_mode": "termux_host",
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "env_keys": [],
        }
        save_installed(installed)
        print(f"\033[92m[OK] Successfully installed {app['name']}! Type '{app['executable']}' to run.\033[0m")
        print(f"\033[93m[i] Configure API keys anytime with: ancli config {app_id}\033[0m")
    else:
        print(f"\033[91m[X] Installation failed.\033[0m")

def _install_proot(app_id, app, registry_version="unknown"):
    """Install a tool inside the Ubuntu PRoot container."""
    import shutil
    # Ensure 'curl' and 'ca-certificates' exist in container before executing any install commands
    if not shutil.which("curl"):
        print("\033[96m[i] Container is missing 'curl'. Auto-installing dependencies via apt...\033[0m")
        # Run silent apt-get update & install
        apt_cmd = "apt-get update -qy && apt-get install -qy curl ca-certificates"
        if not run_cmd(apt_cmd):
            print("\033[91m[X] Failed to install 'curl' inside container. Installation might fail.\033[0m")
        else:
            print("\033[92m[OK] 'curl' and certificates installed successfully.\033[0m")

    # Specific override for agy to bypass ADB shell piping / escaping bugs
    if app_id == "agy":
        try:
            print("\033[96m[*] Downloading Antigravity installer via Python to bypass escaping...\033[0m")
            installer_url = "https://antigravity.google/cli/install.sh"
            
            # Setup urllib with proxy if available in process environment
            proxy_url = os.environ.get('http_proxy') or os.environ.get('https_proxy') or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
            if proxy_url:
                handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
                opener = urllib.request.build_opener(handler)
                urllib.request.install_opener(opener)
                
            req = urllib.request.Request(installer_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read()
                os.makedirs("/tmp", exist_ok=True)
                with open("/tmp/install.sh", "wb") as f:
                    f.write(content)
            
            # Execute the script locally
            install_cmd = "bash /tmp/install.sh --dir /usr/local/bin"
            if run_cmd(install_cmd):
                generate_proot_wrapper(app['executable'], {}, app.get('runtime_env', []))
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
                print(f"\033[92m[OK] Successfully installed {app['name']}! Type '{app['executable']}' to run.\033[0m")
                print(f"\033[93m[i] Configure API keys anytime with: ancli config {app_id}\033[0m")
                return
        except Exception as e:
            print(f"\033[91m[X] Python downloader failed: {e}\033[0m")
            print("\033[93m[!] Falling back to standard shell installer...\033[0m")

    runtime_env = app.get('runtime_env', [])
    if run_cmd(app['install_cmd']):
        generate_proot_wrapper(app['executable'], {}, runtime_env)
        installed = load_installed()
        installed[app_id] = {
            "name": app['name'],
            "executable": app['executable'],
            "install_mode": "proot",
            "installed_version": registry_version,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "env": {},  # Stores persisted user API keys / configs
        }
        save_installed(installed)
        print(f"\033[92m[OK] Successfully installed {app['name']}! Type '{app['executable']}' to run.\033[0m")
        print(f"\033[93m[i] Configure API keys anytime with: ancli config {app_id}\033[0m")
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
        # Extract persisted env keys
        cached_env = installed[app_id].get('env', {})
        if cached_env:
            print(f"\033[92m[i] Re-injecting stored configuration keys: {', '.join(cached_env.keys())}\033[0m")
        
        runtime_env = app.get('runtime_env', [])
        # Regenerate wrapper, baking in the cached user keys
        generate_proot_wrapper(app.get('executable', app_id), cached_env if cached_env else None, runtime_env)
        
        # Update metadata
        reg_ver = app.get('version', registry.get('version', 'unknown'))
        installed[app_id]['installed_version'] = reg_ver
        installed[app_id]['installed_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_installed(installed)
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
        # Prompt, showing previous value as hint
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
        print("[0] Exit")
        choice = input("\033[93mChoose an option (number): \033[0m").strip()

        if choice == '0':
            break
        elif choice.lower() == 'r':
            repair_env(registry)
        elif choice.isdigit() and 1 <= int(choice) <= len(apps):
            app_id = apps[int(choice)-1]
            if app_id in installed:
                print(f"\n\033[96m[1] Update {app_id}\n[2] Uninstall {app_id}\n[3] Reconfigure env vars\033[0m")
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
    print("""\033[1;36mAnCLI (Android CLI) - Dual-Mode Environment Manager\033[0m

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

\033[1mExecution Backends:\033[0m
  proot       Python/Go tools run inside Ubuntu PRoot container
  termux_host Node.js tools run natively via Termux (requires Termux + nodejs)

\033[1mExamples:\033[0m
  ancli install aider
  ancli install opencode
  ancli config claude-code
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
                        date = info.get('installed_at', 'unknown')
                        local_ver = info.get('installed_version', 'unknown')
                        
                        # 1. Integrity Check: verify if executable exists inside PRoot's /usr/local/bin
                        exec_name = info.get('executable', aid)
                        bin_path = f"{ROOTFS}/usr/local/bin/{exec_name}"
                        # For aider/mimo it might be in standard path /usr/bin or /usr/local/bin, let's check both
                        bin_exists = os.path.exists(bin_path) or os.path.exists(f"{ROOTFS}/usr/bin/{exec_name}")
                        status_tag = "\033[92m[Active]\033[0m" if bin_exists else "\033[91m[Broken: Missing Binary]\033[0m"
                        
                        # 2. Check for update available online
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
