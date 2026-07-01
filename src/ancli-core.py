#!/usr/bin/env python3
import os
import sys
import json
import shlex
import time
import urllib.request
import subprocess

# Paths inside the PRoot environment
ANCLI_DIR = "/data/local/tmp/ancli"
ROOTFS = f"{ANCLI_DIR}/rootfs"
MOD_DIR = "/data/adb/modules/ancli"

# Optional dynamic bin paths (KSU/Apatch)
KSU_BIN = "/data/adb/ksu/bin"
AP_BIN = "/data/adb/ap/bin"

VERSION = "1.0.0"

REGISTRY_URL = "https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/registry.json"
LOCAL_REGISTRY = f"{ANCLI_DIR}/registry.json"
INSTALLED_FILE = f"{ANCLI_DIR}/installed.json"

# Allowed command prefixes for security validation
ALLOWED_CMD_PREFIXES = ("pip ", "npm ", "apt-get ", "apt ", "curl ", "rm ", "agy ")

def fetch_registry():
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(REGISTRY_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())
                with open(LOCAL_REGISTRY, "w") as f:
                    json.dump(data, f)
                return data
        except Exception as e:
            last_err = e
            if attempt < 2:
                print(f"\033[93m[!] Retry {attempt+1}/3: {e}\033[0m")
                time.sleep(2)
    # All retries exhausted, fall back to local cache
    if os.path.exists(LOCAL_REGISTRY):
        print(f"\033[93m[!] Using local cache (network unavailable: {last_err})\033[0m")
        with open(LOCAL_REGISTRY, "r") as f:
            return json.load(f)
    print(f"\033[91m[X] Failed to fetch registry and no local cache found: {last_err}\033[0m")
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

def generate_wrapper(executable, env_dict=None):
    # Sanitize executable name to prevent path traversal
    if '/' in executable or '\\' in executable or '..' in executable:
        print(f"\033[91m[X] Invalid executable name: {executable}\033[0m")
        return

    exports = ""
    if env_dict:
        for k, v in env_dict.items():
            exports += f'export {k}={shlex.quote(v)}\n'
            
    wrapper = f'#!/system/bin/sh\n{exports}export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin\nexport HOME=/root\nexec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} -b /sdcard -w /root /usr/bin/env {executable} "$@"\n'
    
    # 1. Systemless module (post-reboot)
    sys_bin = f"{MOD_DIR}/system/bin"
    os.makedirs(sys_bin, exist_ok=True)
    sys_path = f"{sys_bin}/{executable}"
    with open(sys_path, "w") as f:
        f.write(wrapper)
    os.chmod(sys_path, 0o755)

    # 2. Instant access
    for instant_bin in [KSU_BIN, AP_BIN]:
        if os.path.isdir(instant_bin):
            inst_path = f"{instant_bin}/{executable}"
            with open(inst_path, "w") as f:
                f.write(wrapper)
            os.chmod(inst_path, 0o755)

def remove_wrapper(executable):
    paths = [
        f"{MOD_DIR}/system/bin/{executable}",
        f"{KSU_BIN}/{executable}",
        f"{AP_BIN}/{executable}"
    ]
    for p in paths:
        if os.path.exists(p):
            os.remove(p)

def validate_cmd(cmd):
    """Security: verify command starts with an allowed prefix and has no shell operators."""
    cmd_stripped = cmd.strip()
    # Block shell execution operators
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
    # Validate command against whitelist before execution
    if not validate_cmd(cmd):
        return False
    print(f"\033[96m> {cmd}\033[0m")
    result = subprocess.run(cmd, shell=True)
    return result.returncode == 0

def install_app(app_id, registry):
    if app_id not in registry['apps']:
        print(f"\033[91m[X] App {app_id} not found in registry.\033[0m")
        return
    app = registry['apps'][app_id]
    
    print(f"\033[92m[*] Installing {app['name']}...\033[0m")
    
    if run_cmd(app['install_cmd']):
        generate_wrapper(app['executable'], {})
        installed = load_installed()
        installed[app_id] = {
            "name": app['name'],
            "executable": app['executable'],
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "env_keys": []
        }
        save_installed(installed)
        print(f"\033[92m[OK] Successfully installed {app['name']}! You can now type '{app['executable']}' directly.\033[0m")
        print(f"\033[93m[i] Note: You can configure API keys/endpoints later using option [3] or running 'ancli config {app_id}'.\033[0m")
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
    exe = app.get('executable', app_id)
    remove_wrapper(exe)
    
    del installed[app_id]
    save_installed(installed)
    print(f"\033[92m[OK] Successfully uninstalled.\033[0m")

def show_menu():
    registry = fetch_registry()
    
    while True:
        installed = load_installed()  # Refresh on each loop iteration
        print("\n\033[1;36m=== 🚀 AnCLI App Store ===\033[0m")
        apps = list(registry['apps'].keys())
        for i, app_id in enumerate(apps, 1):
            app = registry['apps'][app_id]
            status = "\033[92m[Installed]\033[0m" if app_id in installed else ""
            print(f"[{i}] {app['name']} - {app['description']} {status}")
        
        print("[0] Exit")
        choice = input("\033[93mChoose an option (number): \033[0m").strip()
        
        if choice == '0':
            break
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

def update_app(app_id, registry):
    """Update an installed app and regenerate its wrapper."""
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return
    app = registry['apps'].get(app_id, {})
    cmd = app.get('update_cmd', f"echo 'No update cmd for {app_id}'")
    print(f"\033[93m[*] Updating {app.get('name', app_id)}...\033[0m")
    if run_cmd(cmd):
        # Regenerate wrapper with existing env vars
        env_dict = {}
        existing_keys = installed[app_id].get('env_keys', [])
        if existing_keys:
            print(f"\033[93m[i] Current env vars: {', '.join(existing_keys)}\033[0m")
            print(f"\033[93m    (Leave blank to keep current values)\033[0m")
        generate_wrapper(app.get('executable', app_id), env_dict if env_dict else None)
        installed[app_id]['installed_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_installed(installed)
        print(f"\033[92m[OK] Successfully updated {app.get('name', app_id)}.\033[0m")

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
        val = input(f"\033[96mEnter {var} (leave blank to skip): \033[0m").strip()
        if val:
            env_dict[var] = val
    generate_wrapper(app.get('executable', app_id), env_dict if env_dict else None)
    installed[app_id]['env_keys'] = list(env_dict.keys())
    save_installed(installed)
    print(f"\033[92m[OK] Reconfigured and wrapper regenerated.\033[0m")

def print_help():
    print("""\033[1;36mAnCLI (Android CLI) - Environment Manager\033[0m

\033[1mUsage:\033[0m
  ancli                          Open interactive App Store menu
  ancli install <app_id>         Install an app from the registry
  ancli uninstall <app_id>       Uninstall an installed app
  ancli update <app_id>          Update an installed app
  ancli config <app_id>          Reconfigure env vars for an app
  ancli list                     List all installed apps
  ancli --version                Show version
  ancli --help                   Show this help message

\033[1mExamples:\033[0m
  ancli install aider
  ancli config claude-code
  ancli list
""")

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            # CLI Mode
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
            elif action == "list":
                installed = load_installed()
                if not installed:
                    print("\033[93m[i] No apps installed yet. Run 'ancli' to browse the App Store.\033[0m")
                else:
                    print("\033[1;36m=== Installed Apps ===\033[0m")
                    for aid, info in installed.items():
                        date = info.get('installed_at', 'unknown')
                        print(f"  \033[92m{aid}\033[0m: {info.get('name', aid)} (installed: {date})")
            else:
                print_help()
        else:
            # Interactive Menu Mode
            show_menu()
    except (KeyboardInterrupt, EOFError):
        print("\n\033[93m[!] Operation cancelled by user. Exiting.\033[0m")
        sys.exit(0)
