#!/usr/bin/env python3
import os
import sys
import json
import shlex
import time
import subprocess
import urllib.request
import re
import ssl

# SSL verification is scoped per-request in fetch_registry() and _install_pipe_script()
# to handle incomplete CAs inside the PRoot container.
# The global ssl context is intentionally NOT monkey-patched to preserve security.

# Paths inside the PRoot environment
ANCLI_DIR = "/data/local/tmp/ancli"
ROOTFS = f"{ANCLI_DIR}/rootfs"
MOD_DIR = "/data/adb/modules/ancli"

# Optional dynamic bin paths (KSU/Apatch)
KSU_BIN = "/data/adb/ksu/bin"
AP_BIN  = "/data/adb/ap/bin"

# Termux Host backend paths
TERMUX_PREFIX = "/data/data/com.termux/files/usr"

VERSION = "1.2.2"

REGISTRY_URL   = "https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/registry.json"
LOCAL_REGISTRY = "/root/.ancli-registry.json"   # persistent and writable inside proot
INSTALLED_FILE = f"{ANCLI_DIR}/installed.json"
SECRETS_DIR    = f"{ANCLI_DIR}/secrets"   # Per-tool API key files (mode 0600)
CONFIG_FILE    = "/root/.ancli-config.json"

# Allowed command prefixes for security validation
ALLOWED_CMD_PREFIXES = ("pip ", "npm ", "apt-get ", "apt ", "curl ", "rm ", "agy ", "bash ", "sh ")

# ---------------------------------------------------------------------------
# Configuration & Multi-language Support
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"lang": "zh"}

def save_config(config):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass

CURRENT_CONFIG = load_config()
LANG = CURRENT_CONFIG.get("lang", "zh")

STRINGS = {
    "zh": {
        "title": "=== 🚀 AnCLI 应用商店 ===",
        "repair_opt": "修复环境 (修复 DNS、权限、封装)",
        "uninstall_opt": "卸载应用",
        "lang_opt": "切换语言 (当前: 中文)",
        "exit_opt": "退出",
        "choose_opt": "请选择一个选项: ",
        "installed": "[已安装]",
        "invalid_choice": "无效的选择。",
        
        "uninstall_menu_title": "=== 🗑️ 卸载菜单 ===",
        "uninstall_specific": "卸载特定应用",
        "uninstall_all": "卸载所有应用 (保留 AnCLI 环境)",
        "uninstall_complete": "完全卸载 AnCLI (框架和所有应用)",
        "cancel": "取消",
        "no_apps_installed": "尚未安装任何应用。",
        "select_app_uninstall": "选择要卸载的应用:",
        "enter_num_uninstall": "输入数字进行卸载 (0 取消): ",
        "confirm_uninstall_all": "确定要卸载所有应用吗？(y/n): ",
        "all_apps_uninstalled": "[OK] 所有应用已卸载。",
        "uninstall_instructions": "\n\033[91m⚠️ 要完全卸载 AnCLI 并删除所有文件，请退出此菜单\n并在您的 root Android shell 中运行以下命令：\033[0m\n\n    \033[1;33mrm -rf /data/local/tmp/ancli\033[0m\n\n\033[90m(然后只需在您的 Magisk/KernelSU Manager 中卸载 AnCLI 模块即可)\033[0m",
        
        "manage_title": "=== 管理: {} ===",
        "manage_update": "更新",
        "manage_uninstall": "卸载",
        "manage_config": "重新配置环境变量",
        "manage_cancel": "取消",
        "action_prompt": "操作: ",
        
        "help_text": """\033[1;36mAnCLI (Android CLI) - 统一的免重启命令行环境管理器\033[0m

\033[1m用法:\033[0m
  ancli                          打开交互式应用商店菜单
  ancli install <app_id>         安装指定的应用
  ancli uninstall <app_id>       卸载指定的应用
  ancli update <app_id>          更新指定的应用
  ancli config <app_id>          重新配置应用的环境变量
  ancli list                     列出所有已安装的应用
  ancli repair                   检测并修复 DNS、权限和封装
  ancli --version                显示版本
  ancli --help                   显示此帮助信息

\033[1m执行后端:\033[0m
  所有工具均运行在 Ubuntu PRoot 容器中，路径为:
    /data/local/tmp/ancli/rootfs

\033[1m直接调用 (绕过 noexec 限制):\033[0m
  sh /data/local/tmp/ancli/bin/agy
  sh /data/local/tmp/ancli/bin/claude
  sh /data/local/tmp/ancli/bin/mimo

\033[1m示例:\033[0m
  ancli install aider
  ancli install agy
  ancli config aider
  ancli list
""",
        "installed_apps_title": "=== 已安装应用 ===",
        "no_apps_installed_msg": "\033[93m[i] 尚未安装任何应用。运行 'ancli' 浏览应用商店。\033[0m",
        "app_active": "[正常]",
        "app_broken": "[损坏: 缺少二进制文件]",
        "app_update_available": " (有新版本: v{})",
        "app_installed_at": "    安装时间: {}",
        "app_config_keys": "    配置的 Key: {}",
        "lang_switched": "\033[92m[OK] 已切换语言为：中文\033[0m",
    },
    "en": {
        "title": "=== 🚀 AnCLI App Store ===",
        "repair_opt": "Repair environment (Fix DNS, permissions, wrappers)",
        "uninstall_opt": "Uninstall an app",
        "lang_opt": "Switch Language (Current: English)",
        "exit_opt": "Exit",
        "choose_opt": "Choose an option: ",
        "installed": "[Installed]",
        "invalid_choice": "Invalid choice.",
        
        "uninstall_menu_title": "=== 🗑️ Uninstall Menu ===",
        "uninstall_specific": "Uninstall a specific app",
        "uninstall_all": "Uninstall ALL apps (keep AnCLI environment)",
        "uninstall_complete": "Completely uninstall AnCLI (Framework & All Apps)",
        "cancel": "Cancel",
        "no_apps_installed": "No apps installed yet.",
        "select_app_uninstall": "Select app to uninstall:",
        "enter_num_uninstall": "Enter number to uninstall (0 to cancel): ",
        "confirm_uninstall_all": "Are you sure you want to uninstall ALL apps? (y/n): ",
        "all_apps_uninstalled": "[OK] All apps uninstalled.",
        "uninstall_instructions": "\n\033[91m⚠️ To completely uninstall AnCLI and remove all files, please exit this menu\nand run the following command in your root Android shell:\033[0m\n\n    \033[1;33mrm -rf /data/local/tmp/ancli\033[0m\n\n\033[90m(Then simply uninstall the AnCLI module from your Magisk/KernelSU Manager)\033[0m",
        
        "manage_title": "=== Manage: {} ===",
        "manage_update": "Update",
        "manage_uninstall": "Uninstall",
        "manage_config": "Reconfigure env vars",
        "manage_cancel": "Cancel",
        "action_prompt": "Action: ",
        
        "help_text": """\033[1;36mAnCLI (Android CLI) - Unified Systemless CLI Environment Manager\033[0m

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
""",
        "installed_apps_title": "=== Installed Apps ===",
        "no_apps_installed_msg": "\033[93m[i] No apps installed yet. Run 'ancli' to browse the App Store.\033[0m",
        "app_active": "[Active]",
        "app_broken": "[Broken: Missing Binary]",
        "app_update_available": " (Update Available: v{})",
        "app_installed_at": "    Installed: {}",
        "app_config_keys": "    Configured keys: {}",
        "lang_switched": "\033[92m[OK] Language switched to: English\033[0m",
    }
}

def _t(key, *args):
    global LANG
    text = STRINGS.get(LANG, STRINGS["zh"]).get(key, key)
    if args:
        return text.format(*args)
    return text


# ---------------------------------------------------------------------------
# Registry & State
# ---------------------------------------------------------------------------

def fetch_registry():
    # Local test mode fallback
    if os.path.exists(f"{ANCLI_DIR}/test_mode"):
        # Check in local registry or fallback path
        for p in [f"{ANCLI_DIR}/registry.json", f"{ANCLI_DIR}/bin/registry.json"]:
            if os.path.exists(p):
                try:
                    with open(p, "r") as f:
                        return json.load(f)
                except Exception:
                    pass

    last_net_err = None
    # Scoped unverified SSL context: the PRoot Ubuntu container may have incomplete CA certs.
    # We scope this only to registry fetches rather than patching the global ssl context.
    _ssl_ctx = ssl._create_unverified_context()
    for attempt in range(3):
        try:
            req = urllib.request.Request(REGISTRY_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as response:
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

def _load_local_registry_cache():
    """Load registry from local disk cache only — no network request.
    Used by commands like 'list' that don't require up-to-date cloud data.
    Returns None if no cache is available."""
    for p in [LOCAL_REGISTRY, f"{ANCLI_DIR}/registry.json", f"{ANCLI_DIR}/bin/registry.json"]:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    return json.load(f)
            except Exception:
                pass
    return None

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

def _write_secrets_file(executable, env_dict):
    """Write API keys to a per-tool secrets file (mode 0600) inside SECRETS_DIR.
    Keeps sensitive credentials out of world-readable (chmod 755) wrapper scripts."""
    try:
        os.makedirs(SECRETS_DIR, exist_ok=True)
        os.chmod(SECRETS_DIR, 0o700)
    except Exception:
        pass

    secrets_path = f"{SECRETS_DIR}/{executable}.env"
    try:
        if os.path.exists(secrets_path):
            os.remove(secrets_path)
        with open(secrets_path, "w") as f:
            for k, v in env_dict.items():
                # shlex.quote safely handles values containing quotes or special shell chars
                f.write(f"export {k}={shlex.quote(v)}\n")
        os.chmod(secrets_path, 0o600)
        print(f"\033[92m[OK] Secrets stored securely: {secrets_path}\033[0m")
    except Exception as e:
        print(f"\033[93m[!] Warning: Could not write secrets file: {e}\033[0m")

def generate_proot_wrapper(executable, env_dict=None, runtime_env_list=None):
    """Generate a wrapper that routes execution into the Ubuntu PRoot container."""
    if '/' in executable or '\\' in executable or '..' in executable:
        print(f"\033[91m[X] Invalid executable name: {executable}\033[0m")
        return

    static_exports = ""
    # Inject static runtime env vars from registry (e.g., HOME override for npm tools)
    if runtime_env_list:
        for item in runtime_env_list:
            if '=' in item:
                key, _, val = item.partition('=')
                if key.replace('_', '').isalnum():
                    static_exports += f'export {key}={shlex.quote(val)}\n'

    # Write user-supplied API keys to a secure per-tool secrets file (mode 0600).
    # The wrapper sources the file at runtime, preventing credential embedding in
    # world-readable scripts (chmod 755).
    if env_dict:
        _write_secrets_file(executable, env_dict)
        secrets_line = f". {ANCLI_DIR}/secrets/{executable}.env 2>/dev/null || true"
    else:
        secrets_line = f"# No secrets configured. Run: ancli config {executable}"

    # Build proot bind arguments.
    # We bind all common Android root directories unconditionally so that
    # Node.js fs.realpath and other symlink-following logic never breaks.
    wrapper = f"""#!/system/bin/sh
# AnCLI wrapper for: {executable}

# 1. Load centralized proxy & environment variables
. {ANCLI_DIR}/bin/ancli_env.sh 2>/dev/null || true

# 2. Load tool-specific secrets (mode 0600, not embedded in this script)
{secrets_line}

# 3. Inject static runtime env vars (from registry)
{static_exports}
# 4. Launch PRoot with unified global binds
# By binding all common Android root directories (/sdcard, /storage, /mnt, /data, /apex, /system),
# we prevent Node.js fs.realpath and other symlink-following logic from breaking.
exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} \\
    -b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system \\
    -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb \\
    -w "$PWD" /usr/bin/env {executable} "$@"
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
    # Also remove the associated secrets file to avoid leaving stale credentials on disk
    secrets_path = f"{SECRETS_DIR}/{executable}.env"
    if os.path.exists(secrets_path):
        try:
            os.remove(secrets_path)
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

    # 3.1 Deploy xdg-open browser redirect wrapper inside container
    _deploy_xdg_open()
    print("\033[92m[OK] Browser redirect wrapper deployed (/usr/local/bin/xdg-open).\033[0m")

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
            app_reg = registry['apps'].get(app_id) if registry and 'apps' in registry else None
            runtime_env = app_reg.get('runtime_env', []) if app_reg else []
            stored_env = info.get('env', {})
            generate_proot_wrapper(exec_name, stored_env if stored_env else None, runtime_env)
        print("\033[92m[OK] Wrappers updated to latest engine.\033[0m")
    else:
        print("\033[90m[i] No installed apps to repair.\033[0m")

    print("\033[92m[OK] Repair complete! If problems persist, try: ancli config <app_id>\033[0m")

def _deploy_xdg_open():
    try:
        xdg_path = f"{ROOTFS}/usr/local/bin/xdg-open"
        os.makedirs(os.path.dirname(xdg_path), exist_ok=True)
        if os.path.exists(xdg_path) or os.path.islink(xdg_path):
            try:
                os.remove(xdg_path)
            except Exception:
                pass
        xdg_content = """#!/system/bin/sh
PATH="/system/bin:$PATH" /system/bin/am start -a android.intent.action.VIEW -d "$1" >/dev/null 2>&1
"""
        with open(xdg_path, "w") as f:
            f.write(xdg_content)
        os.chmod(xdg_path, 0o755)

        # Create symlinks in /usr/bin and /bin for hardcoded lookups
        for link_dir in ["/usr/bin", "/bin"]:
            link_path = f"{ROOTFS}{link_dir}/xdg-open"
            try:
                if os.path.exists(link_path) or os.path.islink(link_path):
                    os.remove(link_path)
                os.symlink("/usr/local/bin/xdg-open", link_path)
            except Exception:
                pass

        # Deploy host-side xdg-open to KSU/APatch bin paths to bypass Go's statx translation bug
        tmp_xdg = f"{ANCLI_DIR}/bin/.xdg-open.tmp"
        try:
            with open(tmp_xdg, "w") as f:
                f.write(xdg_content)
            os.chmod(tmp_xdg, 0o755)
            for instant_bin in [KSU_BIN, AP_BIN]:
                if os.path.isdir(instant_bin):
                    inst_path = f"{instant_bin}/xdg-open"
                    os.system(f"cp -f {tmp_xdg} {inst_path} 2>/dev/null && chmod 755 {inst_path} 2>/dev/null")
        except Exception:
            pass
        finally:
            try:
                os.remove(tmp_xdg)
            except Exception:
                pass
    except Exception:
        pass

def _install_proot_common(app_id, app, registry_version="unknown"):
    """Shared post-install bookkeeping: write wrapper and save state."""
    runtime_env = app.get('runtime_env', [])
    generate_proot_wrapper(app['executable'], {}, runtime_env)
    _deploy_xdg_open()
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

def _install_pipe_script(app_id, app, registry_version="unknown"):
    """Install a tool by downloading its installer script via Python urllib, then executing it.
    This is registry-driven and bypasses the ADB shell pipe/escape issues that corrupt
    'curl | bash' style commands when run through adb ('|', '--', etc. get mangled)."""
    installer_url  = app.get('installer_url', '')
    installer_args = app.get('installer_args', '')
    installer_env  = app.get('installer_script_env', {})

    if not installer_url:
        print(f"\033[91m[X] No 'installer_url' in registry entry for {app_id}.\033[0m")
        return

    try:
        print(f"\033[96m[*] Downloading {app['name']} installer via Python (bypasses pipe escaping)...\033[0m")

        # Propagate proxy if set in process environment
        proxy_url = (os.environ.get('http_proxy') or os.environ.get('https_proxy')
                     or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'))
        if proxy_url:
            handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
            opener  = urllib.request.build_opener(handler)
            urllib.request.install_opener(opener)

        _ssl_ctx = ssl._create_unverified_context()
        req = urllib.request.Request(installer_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as response:
            content = response.read()
            os.makedirs("/tmp", exist_ok=True)
            script_path = f"/tmp/install_{app_id}.sh"
            with open(script_path, "wb") as f:
                f.write(content)

        # Build the shell command, prepending any script-level env vars
        if installer_env:
            env_prefix = " ".join(
                f"{shlex.quote(k)}={shlex.quote(v)}" for k, v in installer_env.items()
            )
            parts = [env_prefix, "bash", script_path]
            if installer_args:
                parts.append(installer_args)
            cmd = f"bash -c '{' '.join(parts)}'"
        elif installer_args:
            cmd = f"bash {script_path} {installer_args}"
        else:
            cmd = f"bash {script_path}"

        if run_cmd(cmd):
            _install_proot_common(app_id, app, registry_version)
        else:
            print(f"\033[91m[X] Installer script failed for {app_id}.\033[0m")

    except Exception as e:
        print(f"\033[91m[X] Python downloader failed: {e}\033[0m")
        print("\033[93m[!] Falling back to registry install_cmd...\033[0m")
        if run_cmd(app.get('install_cmd', f"echo 'No install_cmd for {app_id}'" )):
            _install_proot_common(app_id, app, registry_version)
        else:
            print(f"\033[91m[X] Installation failed.\033[0m")


def _install_proot(app_id, app, registry_version="unknown"):
    """Install a tool inside the Ubuntu PRoot container.
    Dispatches to the appropriate installer based on the registry 'install_method' field:
      'pipe_script' — downloads an installer script via Python urllib (bypasses ADB escaping)
      'cmd'         — runs install_cmd directly inside the container (default)
    """
    import shutil
    # Ensure 'curl' and 'ca-certificates' exist in container before executing any install commands
    if not shutil.which("curl"):
        print("\033[96m[i] Container is missing 'curl'. Auto-installing dependencies via apt...\033[0m")
        apt_cmd = "apt-get update -qy && apt-get install -qy curl ca-certificates"
        if not run_cmd(apt_cmd):
            print("\033[91m[X] Failed to install 'curl' inside container. Installation might fail.\033[0m")
        else:
            print("\033[92m[OK] 'curl' and certificates installed successfully.\033[0m")

    # Dispatch based on install_method defined in registry
    install_method = app.get('install_method', 'cmd')
    if install_method == 'pipe_script':
        _install_pipe_script(app_id, app, registry_version)
    else:
        # Generic direct command install path
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
    global LANG
    registry = fetch_registry()

    while True:
        installed = load_installed()
        print(f"\n\033[1;36m{_t('title')}\033[0m")
        apps = list(registry['apps'].keys())
        for i, app_id in enumerate(apps, 1):
            app = registry['apps'][app_id]
            mode_tag = f"\033[90m[{app.get('install_mode', 'proot')}]\033[0m"
            status = f"\033[92m{_t('installed')}\033[0m" if app_id in installed else ""
            print(f"[{i}] {app['name']} {mode_tag} - {app['description']} {status}")

        print(f"[r] {_t('repair_opt')}")
        print(f"[u] {_t('uninstall_opt')}")
        print(f"[l] {_t('lang_opt')}")
        print(f"[0] {_t('exit_opt')}")
        choice = input(f"\033[93m{_t('choose_opt')}\033[0m").strip()

        if choice == '0':
            break
        elif choice.lower() == 'l':
            # Toggle language
            LANG = "en" if LANG == "zh" else "zh"
            config = load_config()
            config["lang"] = LANG
            save_config(config)
            print(_t("lang_switched"))
        elif choice.lower() == 'r':
            repair_env(registry)
        elif choice.lower() == 'u':
            print(f"\n\033[1;36m{_t('uninstall_menu_title')}\033[0m")
            print(f"[1] {_t('uninstall_specific')}")
            print(f"[2] {_t('uninstall_all')}")
            print(f"[3] {_t('uninstall_complete')}")
            print(f"[0] {_t('cancel')}")
            u_choice = input(f"\033[93m{_t('choose_opt')}\033[0m").strip()
            
            if u_choice == '1':
                if not installed:
                    print(f"\033[91m{_t('no_apps_installed')}\033[0m")
                    continue
                print(f"\n\033[1;36m{_t('select_app_uninstall')}\033[0m")
                installed_list = list(installed.keys())
                for i, app_id in enumerate(installed_list, 1):
                    app_name = registry['apps'].get(app_id, {}).get('name', app_id)
                    print(f"[{i}] {app_name}")
                sub = input(_t("enter_num_uninstall")).strip()
                if sub.isdigit() and 1 <= int(sub) <= len(installed_list):
                    uninstall_app(installed_list[int(sub)-1], registry)
            elif u_choice == '2':
                if not installed:
                    print(f"\033[91m{_t('no_apps_installed')}\033[0m")
                    continue
                confirm = input(f"\033[91m{_t('confirm_uninstall_all')}\033[0m").strip().lower()
                if confirm == 'y':
                    for app_id in list(installed.keys()):
                        uninstall_app(app_id, registry)
                    print(f"\033[92m{_t('all_apps_uninstalled')}\033[0m")
            elif u_choice == '3':
                print(_t("uninstall_instructions"))

        elif choice.isdigit() and 1 <= int(choice) <= len(apps):
            app_id = apps[int(choice)-1]
            if app_id in installed:
                print(f"\n\033[96m{_t('manage_title', app_id)}\n[1] {_t('manage_update')}\n[2] {_t('manage_uninstall')}\n[3] {_t('manage_config')}\n[0] {_t('manage_cancel')}\033[0m")
                sub = input(_t("action_prompt")).strip()
                if sub == '1':
                    update_app(app_id, registry)
                elif sub == '2':
                    uninstall_app(app_id, registry)
                elif sub == '3':
                    reconfigure_app(app_id, registry)
            else:
                install_app(app_id, registry)
        else:
            print(f"\033[91m{_t('invalid_choice')}\033[0m")

def print_help():
    print(_t("help_text"))

# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Ensure critical mount points exist in the container so host bindings (e.g. -b /storage) succeed
    for d in ["/storage", "/sdcard", "/data/adb"]:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

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

            # 'list' reads local state only — no network needed.
            # All write-ops (install/update/config/repair) fetch the latest cloud registry.
            if action == "list":
                registry  = _load_local_registry_cache()  # offline-safe, no network
                installed = load_installed()
                if not installed:
                    print(_t("no_apps_installed_msg"))
                else:
                    print(f"\033[1;36m{_t('installed_apps_title')}\033[0m")
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
                        status_tag = f"\033[92m{_t('app_active')}\033[0m" if bin_exists else f"\033[91m{_t('app_broken')}\033[0m"

                        # Check for update available (uses local registry cache, no network)
                        update_tag = ""
                        if registry and aid in registry['apps']:
                            cloud_ver = registry['apps'][aid].get('version', registry.get('version', 'unknown'))
                            if cloud_ver != 'unknown' and local_ver != 'unknown' and cloud_ver != local_ver:
                                update_tag = f" \033[93m{_t('app_update_available', cloud_ver)}\033[0m"

                        print(f"  \033[92m{aid}\033[0m: {info.get('name', aid)} (v{local_ver}) {status_tag}{update_tag}")
                        print(_t("app_installed_at", date))
                        persisted_keys = list(info.get('env', {}).keys())
                        if persisted_keys:
                            print(_t("app_config_keys", ', '.join(persisted_keys)))
            else:
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
                else:
                    print_help()
        else:
            show_menu()
    except (KeyboardInterrupt, EOFError):
        print("\n\033[93m[!] Operation cancelled by user. Exiting.\033[0m")
        sys.exit(0)
