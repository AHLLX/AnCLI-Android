#!/usr/bin/env python3
import os
import sys
import glob
import json
import shlex
import time
import subprocess
import shutil
import urllib.request
import urllib.parse
import re
import ssl
import contextlib
import datetime
import datetime

# 2026-11-13: Keep TLS fallback scoped to individual requests; do not alter the
# process-wide SSL context because the PRoot CA store may be incomplete.
# The global ssl context is intentionally NOT monkey-patched to preserve security.

# Paths inside the PRoot environment
ANCLI_DIR = "/data/local/tmp/ancli"
ROOTFS = f"{ANCLI_DIR}/rootfs"
MOD_DIR = "/data/adb/modules/ancli"

# Optional dynamic bin paths (KSU/Apatch)
KSU_BIN = "/data/adb/ksu/bin"
AP_BIN  = "/data/adb/ap/bin"

VERSION = "1.2.608"

# 2026-11-13: Keep the application catalog in the flashed module. Official
# release checks use each app's own `latest` entry and never fetch a second
# AnCLI registry from the network.
INSTALLED_FILE = f"{ANCLI_DIR}/installed.json"
SECRETS_DIR    = f"{ANCLI_DIR}/secrets"   # Per-tool API key files (mode 0600)
CONFIG_FILE    = "/root/.ancli-config.json"

# Where a tool starts when the caller launched it from "/" (Termius, `su` after a
# fresh shell, ...). "/" makes every TUI walk the whole container plus the /data
# and /sdcard bindings at startup, so launches are redirected to a small, known
# directory. Users can point this at a project tree with
# `ancli config --set default_cwd=/sdcard/projects` (also editable in the WebUI).
DEFAULT_CWD    = "/root"
# 2026-11-13: These are the only global settings accepted by `ancli config`.
# Per-tool API keys and endpoints remain registry-defined and are stored in
# `/data/local/tmp/ancli/secrets/<tool>.env`.
GLOBAL_CONFIG_KEYS = ("default_cwd", "lang", "proxy_mode", "proxy_url")
# Directories that are bind-mounted into the rootfs at run time (they do not
# exist under $ROOTFS on disk), so an existence check must skip them.
BIND_MOUNT_ROOTS = ("/dev", "/proc", "/sys", "/sdcard", "/storage", "/mnt",
                    "/data", "/apex", "/system", "/linkerconfig")
# Official latest-version cache written by `ancli check` and merged into
# `ancli list --json`; it supplies offline fallback data after the WebUI login
# check completes.
UPDATE_CACHE   = f"{ANCLI_DIR}/.update_cache.json"
DNS_CACHE      = f"{ANCLI_DIR}/.dns_cache"   # real DNS servers, probed on the host by ancli_env.sh
CHECK_TTL      = 6 * 3600                 # cache hint exposed to older WebUI clients
CHECK_TTL_FAILED = 15 * 60                # shorter retry hint after a failed check
PROBE_TIMEOUT  = 20                       # seconds per `version_cmd` probe (a --version must be fast)

# Allowed command prefixes for security validation.
# 'env ' is used by pipe-script installs to inject env vars before 'bash'.
ALLOWED_CMD_PREFIXES = ("pip ", "npm ", "apt-get ", "apt ", "curl ", "rm ", "agy ", "bash ", "sh ", "env ")

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


def get_default_cwd():
    """Configured default working directory; "" means the built-in DEFAULT_CWD."""
    return (load_config().get("default_cwd") or "").strip()


def _cwd_visible_in_container(path):
    """True when `path` exists from inside the container.

    Bind-mounted roots do not exist under $ROOTFS on disk, so they are accepted
    without an existence check (the wrapper binds them at run time)."""
    if any(path == root or path.startswith(root + "/") for root in BIND_MOUNT_ROOTS):
        return True
    try:
        return os.path.isdir(ROOTFS + path)
    except OSError:
        return False


def set_default_cwd(value):
    """Persist the default working directory. Returns (ok, error)."""
    value = (value or "").strip()
    if value:
        if not value.startswith("/"):
            return False, "默认目录必须是绝对路径（例如 /sdcard 或 /sdcard/projects）"
        parts = [p for p in value.split("/") if p]
        if any(p in (".", "..") for p in parts):
            return False, "默认目录不能包含 . 或 .."
        if "\n" in value or "\x00" in value:
            return False, "默认目录包含非法字符"
        if not _cwd_visible_in_container(value):
            return False, f"容器里看不到这个目录：{value}（先创建它，或检查拼写）"
    cfg = load_config()
    cfg["default_cwd"] = value
    save_config(cfg)
    return True, ""


def set_lang(value):
    """Persist the menu language. Returns (ok, error)."""
    value = (value or "").strip().lower()
    if value not in STRINGS:
        return False, f"不支持的语言：{value}（可选：{', '.join(STRINGS)}）"
    cfg = load_config()
    cfg["lang"] = value
    save_config(cfg)
    return True, ""


def set_proxy_url(value):
    """Persist a manual HTTP proxy URL, or clear it to use system detection."""
    value = (value or "").strip()
    if value:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False, "代理地址必须是 http://host:port 或 https://host:port"
        if parsed.username or parsed.password or "\n" in value or "\x00" in value:
            return False, "代理地址不能包含认证信息或控制字符"
        if parsed.port is None or not (1 <= parsed.port <= 65535):
            return False, "代理端口必须在 1 到 65535 之间"
    cfg = load_config()
    cfg["proxy_url"] = value
    save_config(cfg)
    return True, ""


def set_proxy_mode(value):
    value = (value or "").strip().lower()
    if value not in ("system", "manual", "direct"):
        return False, "代理模式必须是 system、manual 或 direct"
    cfg = load_config()
    cfg["proxy_mode"] = value
    save_config(cfg)
    return True, ""


def settings_command(argv):
    """Handle `ancli config` without an app id (global settings only)."""
    errors = []
    memory = None
    changed_default_cwd = False
    for key, value in parse_set_env(argv).items():
        if key == "default_cwd":
            ok, err = set_default_cwd(value)
            changed_default_cwd = changed_default_cwd or ok
        elif key == "lang":
            ok, err = set_lang(value)
        elif key == "proxy_url":
            ok, err = set_proxy_url(value)
        elif key == "proxy_mode":
            ok, err = set_proxy_mode(value)
        else:
            ok, err = False, f"未知配置项：{key}（支持 {', '.join(GLOBAL_CONFIG_KEYS)}）"
        if not ok:
            errors.append(err)

    cfg = load_config()
    if changed_default_cwd:
        # Refresh the guest-global context after a configuration change. JSON
        # callers receive the result as data; human callers get the progress line.
        memory = setup_runtime_context(verbose="--json" not in argv)
    if "--json" in argv:
        payload = {
            "lang": cfg.get("lang", "zh"),
            "default_cwd": cfg.get("default_cwd", ""),
            "effective_cwd": cfg.get("default_cwd") or DEFAULT_CWD,
            "proxy_url": cfg.get("proxy_url", ""),
            "proxy_mode": cfg.get("proxy_mode", "system"),
            "supported_keys": list(GLOBAL_CONFIG_KEYS),
        }
        if memory is not None:
            payload["runtime_context"] = memory
        print(json.dumps(payload, ensure_ascii=False))
    else:
        configured = cfg.get("default_cwd", "")
        print(f"默认工作目录: {configured or DEFAULT_CWD}"
              f"{'' if configured else '（内置默认）'}")
        print("  从 / 启动工具时使用；设置示例："
              "ancli config --set default_cwd=/sdcard/projects")
        print(f"语言: {cfg.get('lang', 'zh')}")
        print(f"代理模式: {cfg.get('proxy_mode', 'system')}")
        print(f"代理地址: {cfg.get('proxy_url') or '未设置'}")
    for err in errors:
        print(f"\033[91m[X] {err}\033[0m")
    if errors:
        raise SystemExit(1)

CURRENT_CONFIG = load_config()
LANG = CURRENT_CONFIG.get("lang", "zh")

# 2026-11-13: Keep the terminal menu short; details belong in help and logs.
STRINGS = {
    "zh": {
        "title": "AnCLI",
        "repair_opt": "修复环境",
        "check_opt": "检查更新",
        "check_title": "检查更新",
        "uninstall_opt": "卸载",
        "lang_opt": "切换语言（中文）",
        "exit_opt": "退出",
        "choose_opt": "请选择一个选项: ",
        "installed": "[已安装]",
        "invalid_choice": "无效的选择。",
        
        "uninstall_menu_title": "卸载",
        "uninstall_specific": "卸载指定应用",
        "uninstall_all": "卸载全部应用（保留环境）",
        "uninstall_complete": "完全卸载",
        "cancel": "取消",
        "no_apps_installed": "尚未安装任何应用。",
        "select_app_uninstall": "选择要卸载的应用:",
        "enter_num_uninstall": "输入数字进行卸载 (0 取消): ",
        "confirm_uninstall_all": "确定要卸载所有应用吗？(y/n): ",
        "all_apps_uninstalled": "[OK] 所有应用已卸载。",
        "uninstall_instructions": "\n\033[91m完全卸载会删除容器、安装状态、密钥和配置。退出此菜单，在 root Android shell 中创建清除标记，再从 Magisk、KernelSU 或 APatch 管理器卸载模块：\033[0m\n\n    \033[1;33mtouch /data/local/tmp/ancli_force_purge\033[0m\n\n\033[90m卸载脚本会清理标记和动态 wrapper。\033[0m",
        
        "manage_title": "管理 {}",
        "manage_update": "更新",
        "manage_uninstall": "卸载",
        "manage_config": "配置环境变量",
        "manage_cancel": "取消",
        "action_prompt": "操作: ",
        
        "help_text": """\033[1;36mAnCLI (Android CLI) - 命令行环境管理器\033[0m

\033[1m用法:\033[0m
  ancli                          打开交互式菜单
  ancli install <app_id>         安装指定的应用
  ancli uninstall <app_id>       卸载指定的应用
  ancli update <app_id>          更新指定的应用
  ancli config <app_id>          重新配置应用的环境变量
  ancli config                   查看全局设置（目录、语言、代理）
  ancli config --help            查看全部全局配置项
  ancli config --json            输出全局配置和支持的配置键
  ancli config --set default_cwd=/sdcard/projects
                                 设置从 / 启动工具时的默认目录
  ancli config --set lang=zh      设置交互界面语言（或 en）
  ancli config --set proxy_mode=system
                                 也可设为 manual 或 direct
  ancli config --set proxy_url=http://host:port
                                 设置手动代理地址（manual 模式生效）
  ancli config <app_id> --set KEY=VALUE
                                 设置该工具声明的环境变量
  ancli list                     列出所有已安装的应用
  ancli check                    联网检查版本与可更新项
  ancli doctor --json            输出只读路径、挂载和工具诊断
  ancli skills                   统一各 Agent 的全局 skills 目录（~/.agents/skills）
  ancli agent-init               生成容器全局 Agent 记忆与环境指导
  ancli agent-init --refresh     仅更新此前由 AnCLI 生成的记忆文件
  ancli agent-init --finalize    写入首次实测环境并删除初始化提示
  ancli repair                   检测并修复 DNS、权限和封装
  ancli reset --yes              删除应用、密钥、配置、记忆和日志，恢复模块初始环境
  ancli reset --yes --reinstall  清理后重新安装清理前已安装的应用（不恢复密钥和代理）
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
        "installed_apps_title": "已安装应用",
        "no_apps_installed_msg": "\033[93m[i] 暂无已安装应用。\033[0m",
        "app_active": "[就绪]",
        "app_broken": "[缺少文件]",
        "app_update_available": "（可更新至 v{}）",
        "app_installed_at": "    安装时间: {}",
        "check_no_apps": "[i] 没有已安装的应用可检查。",
        "check_installed": "[OK] {}：已安装 v{}",
        "check_version_fail": "[!] {}：无法读取已安装版本（{}）",
        "check_registry_missing": "[!] {} 已安装但不在 registry 中，跳过。",
        "check_lookup_fail": "[!] {}：官方版本查询失败（{}）",
        "check_keep_version": "    保留之前解析的 v{}",
        "check_official": "[i] {}：官方 v{} [{}] {}",
        "check_no_source": "[i] {}：未配置官方版本来源",
        "check_update_available": "可更新",
        "check_up_to_date": "已是最新",
        "app_config_keys": "    配置: {}",
        "lang_switched": "\033[92m[OK] 已切换语言为：中文\033[0m",
    },
    "en": {
        "title": "AnCLI",
        "repair_opt": "Repair environment",
        "check_opt": "Check updates",
        "check_title": "Update check",
        "uninstall_opt": "Uninstall",
        "lang_opt": "Switch language (English)",
        "exit_opt": "Exit",
        "choose_opt": "Choose an option: ",
        "installed": "[Installed]",
        "invalid_choice": "Invalid choice.",
        
        "uninstall_menu_title": "Uninstall",
        "uninstall_specific": "Uninstall an app",
        "uninstall_all": "Uninstall all apps (keep environment)",
        "uninstall_complete": "Remove AnCLI and apps",
        "cancel": "Cancel",
        "no_apps_installed": "No apps installed yet.",
        "select_app_uninstall": "Select app to uninstall:",
        "enter_num_uninstall": "Enter number to uninstall (0 to cancel): ",
        "confirm_uninstall_all": "Are you sure you want to uninstall ALL apps? (y/n): ",
        "all_apps_uninstalled": "[OK] All apps uninstalled.",
        "uninstall_instructions": "\n\033[91mComplete removal deletes the container, install state, secrets, and config. Exit this menu, create the purge marker in a root Android shell, then uninstall the module from Magisk, KernelSU, or APatch:\033[0m\n\n    \033[1;33mtouch /data/local/tmp/ancli_force_purge\033[0m\n\n\033[90mThe uninstall script removes the marker and dynamic wrappers.\033[0m",
        
        "manage_title": "Manage {}",
        "manage_update": "Update",
        "manage_uninstall": "Uninstall",
        "manage_config": "Configure env vars",
        "manage_cancel": "Cancel",
        "action_prompt": "Action: ",
        
        "help_text": """\033[1;36mAnCLI (Android CLI) - CLI environment manager\033[0m

\033[1mUsage:\033[0m
  ancli                          Open the interactive menu
  ancli install <app_id>         Install an app from the registry
  ancli uninstall <app_id>       Uninstall an installed app
  ancli update <app_id>          Update an installed app
  ancli config <app_id>          Reconfigure env vars for an app
  ancli config                   Show global settings (directory, language, proxy)
  ancli config --help            Show all global configuration keys
  ancli config --json            Print global configuration and supported keys
  ancli config --set default_cwd=/sdcard/projects
                                 Where tools start when launched from /
  ancli config --set lang=zh      Set the interactive language (or en)
  ancli config --set proxy_mode=system
                                 Select system detection (or manual/direct)
  ancli config --set proxy_url=http://host:port
                                 Set the manual proxy URL (used in manual mode)
  ancli config <app_id> --set KEY=VALUE
                                 Set a registry-declared environment variable
  ancli list                     List all installed apps
  ancli check                    Check versions and available updates
  ancli doctor --json            Print read-only path, mount, and tool diagnostics
  ancli skills                   Unify the agent CLIs' global skills dir (~/.agents/skills)
  ancli agent-init               Create the guest-global Agent memory and environment guide
  ancli agent-init --refresh     Refresh only a memory file previously generated by AnCLI
  ancli agent-init --finalize    Record first-run facts and remove the bootstrap prompt
  ancli repair                   Detect and repair DNS, permissions, and wrappers
  ancli reset --yes              Delete apps, secrets, config, memory and logs; restore module baseline
  ancli reset --yes --reinstall  Reset, then reinstall the previous apps without secrets or proxy settings
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
        "installed_apps_title": "Installed apps",
        "no_apps_installed_msg": "\033[93m[i] No apps installed.\033[0m",
        "app_active": "[Ready]",
        "app_broken": "[Missing files]",
        "app_update_available": " (update to v{})",
        "app_installed_at": "    Installed: {}",
        "check_no_apps": "[i] No installed apps to check.",
        "check_installed": "[OK] {}: installed v{}",
        "check_version_fail": "[!] {}: could not read the installed version ({})",
        "check_registry_missing": "[!] {} is installed but not in the registry; skipping.",
        "check_lookup_fail": "[!] {}: official version lookup failed ({})",
        "check_keep_version": "    keeping the previously resolved v{}",
        "check_official": "[i] {}: official v{} [{}] {}",
        "check_no_source": "[i] {}: no official version source configured",
        "check_update_available": "update available",
        "check_up_to_date": "up to date",
        "app_config_keys": "    Config: {}",
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
# Bundled catalog, official version checks, and state
# ---------------------------------------------------------------------------

def _urlopen_verified(url, headers, timeout):
    """Open a URL with TLS certificate verification; falls back to an unverified
    context only on certificate validation errors (container CA store may be incomplete)."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except (ssl.SSLError, urllib.error.URLError) as e:
        if not _is_cert_verification_error(e):
            raise
        print(f"\033[93m[!] TLS certificate verification failed ({e}); retrying this request without verification.\033[0m")
        with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as response:
            return response.read()


def _is_cert_verification_error(e):
    """True only when the failure is TLS certificate *validation* (a MITM-able
    downgrade), not protocol/handshake errors. Keeps the unverified fallback
    as narrow as possible."""
    if isinstance(e, ssl.SSLCertVerificationError):
        return True
    return isinstance(e, urllib.error.URLError) and isinstance(e.reason, ssl.SSLCertVerificationError)


def _http_text(url, timeout=15):
    """Fetch a URL as text (TLS verified, unverified only on cert errors).
    urllib honours the http_proxy/https_proxy vars exported by ancli_env.sh,
    so this works behind the same Android system proxy as the installers."""
    return _urlopen_verified(url, {'User-Agent': 'AnCLI'}, timeout).decode('utf-8', errors='replace')


def _http_json(url, timeout=15):
    """Fetch a URL and decode it as JSON."""
    return json.loads(_http_text(url, timeout))



def _registry_paths():
    """Return the two module-owned catalog copies, newest first."""
    paths = [f"{ANCLI_DIR}/registry.json", f"{ANCLI_DIR}/bin/registry.json"]
    existing = []
    for path in paths:
        try:
            if os.path.exists(path):
                existing.append((os.path.getmtime(path), path))
        except OSError:
            continue
    existing.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in existing]


def _load_bundled_registry():
    """Load the catalog shipped with this module; never access the network."""
    for path in _registry_paths():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("apps"), dict):
                return data
        except (OSError, ValueError, TypeError):
            continue
    return None


def load_registry(required=True):
    """Load the module catalog and fail clearly when the payload is missing."""
    registry = _load_bundled_registry()
    if registry is not None:
        return registry
    message = "[X] Bundled application catalog is missing or invalid. Repair or reinstall the module."
    if required:
        print(message)
        sys.exit(1)
    return {}

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
    """Save installed metadata without persisting secret values."""
    safe_installed = {}
    for app_id, raw in (installed or {}).items():
        info = dict(raw) if isinstance(raw, dict) else {}
        env = info.pop("env", None)
        names = list(info.get("configured_env") or [])
        if isinstance(env, dict):
            names = list(dict.fromkeys(names + list(env.keys())))
        if names:
            info["configured_env"] = names
        safe_installed[app_id] = info
    installed = safe_installed
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

        # Replace atomically. os.replace() overwrites in one step: the old
        # remove()+rename() pair had a window where a crash/kill left the whole
        # database (records + configured env keys) missing.
        os.replace(tmp_file, INSTALLED_FILE)
        # Root-owned 0644: AnCLI is a root tool (`su -c ancli …` / the manager's
        # WebUI bridge), so the state file needs no shell-user write bit — only
        # readability for `ancli list`. It holds no secrets; those live in
        # SECRETS_DIR (0600, chowned to the shell uid so wrappers can source them).
        os.chmod(INSTALLED_FILE, 0o644)
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
        # Wrappers are executed by both root and the Android shell user (UID 2000).
        # chown the dir so the shell user can traverse it and source the secrets.
        if os.system(f"chown 2000:2000 {SECRETS_DIR} 2>/dev/null") != 0:
            print(f"\033[93m[!] Warning: chown failed on {SECRETS_DIR}; shell user may not be able to read secrets\033[0m")
    except Exception:
        pass

    secrets_path = f"{SECRETS_DIR}/{executable}.env"
    try:
        if os.path.exists(secrets_path):
            os.remove(secrets_path)
        with open(secrets_path, "w", encoding="utf-8") as f:
            for k, v in env_dict.items():
                # shlex.quote safely handles values containing quotes or special shell chars
                f.write(f"export {k}={shlex.quote(v)}\n")
        os.chmod(secrets_path, 0o600)
        # Same ownership fix as installed.json: shell user must be able to read it.
        if os.system(f"chown 2000:2000 {secrets_path} 2>/dev/null") != 0:
            print(f"\033[93m[!] Warning: chown failed on {secrets_path}; shell user may not be able to read secrets\033[0m")
        print(f"\033[92m[OK] Secrets stored securely: {secrets_path}\033[0m")
    except Exception as e:
        print(f"\033[93m[!] Warning: Could not write secrets file: {e}\033[0m")

def generate_proot_wrapper(executable, env_dict=None, runtime_env_list=None, proxy_mode="system"):
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
    existing_secrets = os.path.exists(f"{SECRETS_DIR}/{executable}.env")
    if env_dict:
        _write_secrets_file(executable, env_dict)
        secrets_line = f". {ANCLI_DIR}/secrets/{executable}.env 2>/dev/null || true"
    elif existing_secrets:
        secrets_line = f". {ANCLI_DIR}/secrets/{executable}.env 2>/dev/null || true"
    else:
        secrets_line = f"# No secrets configured. Run: ancli config {executable}"
    proxy_override = ""
    if proxy_mode == "direct":
        proxy_override = "# This tool opts out of the Android system proxy.\nunset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY\n"

    # Build the single PRoot wrapper used by every supported app.
    wrapper = f"""#!/system/bin/sh
# AnCLI wrapper for: {executable}

# 1. Load centralized proxy & environment variables
. {ANCLI_DIR}/bin/ancli_env.sh 2>/dev/null || true

# 2. Load tool-specific secrets (mode 0600, not embedded in this script)
{secrets_line}

# 3. Inject static runtime env vars (from registry)
{static_exports}
{proxy_override}
# 3.1 Configured default working directory (`ancli config --set default_cwd=...`,
#     also editable in the WebUI). Read at run time from the config file, so a
#     change takes effect without regenerating wrappers. Empty = built-in /root.
_ancli_default_cwd=""
_ancli_cfg={ROOTFS}/root/.ancli-config.json
if [ -f "$_ancli_cfg" ]; then
_ancli_default_cwd=$(sed -n 's/^[[:space:]]*"default_cwd"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_ancli_cfg" 2>/dev/null | head -n 1)
fi
# Accept it only when the container can really use it: bind roots always exist at
# run time, everything else has to exist under the rootfs.
case "$_ancli_default_cwd" in
"") ;;
/dev*|/proc*|/sys*|/sdcard*|/storage*|/mnt*|/data*|/apex*|/system*|/linkerconfig*) ;;
*) [ -d "{ROOTFS}$_ancli_default_cwd" ] || _ancli_default_cwd="" ;;
esac

# 4. Resolve PRoot working directory: fall back to the container HOME when
#    PWD is not visible inside the rootfs (e.g. an unbound host path like
#    /cache), which would otherwise make proot exit with 'unable to change
#    the current working directory'.
PROOT_CWD="$PWD"
case "$PROOT_CWD" in
/|/dev*|/proc*|/sys*|/sdcard*|/storage*|/mnt*|/data*|/apex*|/system*|/linkerconfig*)
    ;;
*)
    if [ ! -d "{ROOTFS}$PROOT_CWD" ]; then
        if [ -n "$_ancli_default_cwd" ]; then
            PROOT_CWD="$_ancli_default_cwd"
        else
            PROOT_CWD=/root
        fi
    fi
    ;;
esac
# Launching from / (or an unmapped path) means TUI tools (mimo/claude/opencode/...)
# scan the whole container filesystem at startup — rootfs plus the /data and
# /sdcard bindings (hundreds of thousands of files) — which hangs the TUI.
# Start in the configured default directory when there is one, else container HOME.
if [ "$PROOT_CWD" = "/" ]; then
if [ -n "$_ancli_default_cwd" ]; then
    PROOT_CWD="$_ancli_default_cwd"
else
    PROOT_CWD=/root
fi
fi
: "${{PROOT_CWD:=/root}}"
# 5. Provide /dev/shm: Android's /dev has no shm, and Node/Bun workers plus
#    some libraries require shared memory. Bound only when the dir is usable.
SHM_BIND=""
if mkdir -p {ANCLI_DIR}/shm 2>/dev/null; then
# 1777 (sticky, /tmp-style) so the shell user (uid 2000) can write inside
chmod 1777 {ANCLI_DIR}/shm 2>/dev/null || true
SHM_BIND="-b {ANCLI_DIR}/shm:/dev/shm"
fi
# 6. Open URLs for tools that ask. Android's own binaries (am, cmd) cannot execute
#    inside the glibc container, so the container-side xdg-open appends what it was
#    asked to open to this queue and we drain it from the host. The watcher lives
#    only as long as this wrapper (PPID) or 30 minutes, whichever comes first, and
#    drains once more at the end so short-lived tools are covered too.
ANCLI_OPEN_QUEUE={ANCLI_DIR}/.open_url
# Prefer a full browser over the system chooser: with no default browser set,
# `am start` raises "Open with", and a WebView-based pick (Via and friends) can
# drop the auth cookie `dsh web` sets on the token URL, leaving the page on
# "authentication required". Chrome first, then the other common engines.
ANCLI_BROWSERS="com.android.chrome com.chrome.beta com.microsoft.emmx org.mozilla.firefox com.brave.browser com.sec.android.app.sbrowser com.vivaldi.browser com.opera.browser"
ancli_open_browser() {{
for _ancli_pkg in $ANCLI_BROWSERS; do
    if /system/bin/pm list packages "$_ancli_pkg" 2>/dev/null | grep -q "^package:$_ancli_pkg$"; then
        /system/bin/am start -a android.intent.action.VIEW -d "$1" -p "$_ancli_pkg" >/dev/null 2>&1 && return 0
    fi
done
/system/bin/am start -a android.intent.action.VIEW -d "$1" >/dev/null 2>&1 && return 0
/system/bin/am start --user 0 -a android.intent.action.VIEW -d "$1" >/dev/null 2>&1
}}
ancli_open_drain() {{
[ -s "$ANCLI_OPEN_QUEUE" ] || return 0
while IFS= read -r _ancli_url; do
    case "$_ancli_url" in
        http://*|https://*)
            if ancli_open_browser "$_ancli_url"; then
                echo "[AnCLI] opened in the browser: $_ancli_url" >&2
            else
                # Never fail silently: the whole point of the bridge is that the
                # user can still reach the URL by hand.
                echo "[AnCLI] could not open a browser — open this yourself: $_ancli_url" >&2
                echo "[AnCLI] (use this URL, not the bare 127.0.0.1 address: the token is per run)" >&2
            fi
            ;;
    esac
done < "$ANCLI_OPEN_QUEUE"
: > "$ANCLI_OPEN_QUEUE" 2>/dev/null || true
}}
if [ -w "$(dirname "$ANCLI_OPEN_QUEUE")" ]; then
(
    _ancli_ticks=0
    while [ "$_ancli_ticks" -lt 1800 ]; do
        [ -s "$ANCLI_OPEN_QUEUE" ] && ancli_open_drain
        if [ -n "$PPID" ]; then
            # Exit with the tool (and do not linger on a hung/zombie parent: a
            # stuck proot once left this watcher behind for half an hour).
            if ! kill -0 "$PPID" 2>/dev/null; then break; fi
            case "$(sed 's/.*) //' /proc/$PPID/stat 2>/dev/null | cut -d' ' -f1)" in
                Z) break ;;
            esac
        fi
        _ancli_ticks=$((_ancli_ticks + 1))
        sleep 1
    done
    ancli_open_drain
) &
fi
# BROWSER is exported to the tool above: the npm `open` package prefers its own
# vendored freedesktop xdg-open over PATH, and without this it ends with
# "xdg-open: no method available for opening 'http://…'" (rc 3) on a headless
# container. Pointed here, it execs our hand-off shim instead.
# 7. Terminal state: a full-screen TUI (mimo, agy, opencode) puts the tty in raw
#    mode, and when it dies without restoring that the caller's shell looks dead —
#    no echo, Enter only moves the cursor, no command runs. The guest cannot fix
#    it once its process is gone, so this wrapper saves the settings first and
#    puts them back on every exit path. `exec` is deliberately not used anymore:
#    it would replace this shell, leaving nobody to restore the terminal.
_ancli_saved_tty=""
if [ -t 0 ]; then _ancli_saved_tty=$(stty -g 2>/dev/null) || _ancli_saved_tty=""; fi
ancli_restore_tty() {{
[ -n "$_ancli_saved_tty" ] && stty "$_ancli_saved_tty" 2>/dev/null
return 0
}}
trap 'ancli_restore_tty' EXIT

# 8. Android app-data channel: an app-launched shell lives in that app's mount
#    namespace, where Android hides other apps' /data/data behind a tmpfs. A
#    non-recursive bind of /data does not inherit that submount, so the real
#    tree becomes reachable at {ANDROID_DATA_MNT}/data/<pkg> — visible inside the
#    container because /mnt is bound. Best effort: needs root and a writable /mnt.
ANCLI_DATA_MNT={ANDROID_DATA_MNT}
if [ "$(id -u 2>/dev/null)" = "0" ]; then
if ! grep -qs " $ANCLI_DATA_MNT " /proc/mounts; then
    mkdir -p "$ANCLI_DATA_MNT" 2>/dev/null
    mount --bind /data "$ANCLI_DATA_MNT" 2>/dev/null
fi
fi

# 9. Launch PRoot with unified global binds.
# Bind every common Android tree that exists in this mount namespace. Some
# adb-root namespaces omit /sdcard even though /storage and /mnt are present;
# skipping only that absent path avoids a noisy proot warning while preserving
# the full physical-tree mapping wherever Android exposes it.
ANCLI_BIND_ARGS=""
for _ancli_bind in /sdcard /storage /mnt /data /apex /linkerconfig /system; do
    if [ -e "$_ancli_bind" ] || [ -d "$_ancli_bind" ]; then
        ANCLI_BIND_ARGS="$ANCLI_BIND_ARGS -b $_ancli_bind"
    fi
done
# 2026-11-13: Tie the Agent lifetime to the terminal session that launched it.
# Android may reparent a root shell to PID 1 after Termius is swiped away,
# leaving the PRoot and Agent alive with a dead PTY. Keep PRoot in the foreground
# so TUI programs retain the terminal's foreground process group, and use a
# side watchdog to terminate its direct children when the original parent
# disappears or the PID is reused.
ANCLI_PARENT_PID="$PPID"
ANCLI_PARENT_START=""
if [ -r "/proc/$ANCLI_PARENT_PID/stat" ]; then
    ANCLI_PARENT_START=$(sed 's/.*) //' "/proc/$ANCLI_PARENT_PID/stat" 2>/dev/null | cut -d' ' -f20)
fi
ancli_parent_alive() {{
    [ -r "/proc/$ANCLI_PARENT_PID/stat" ] || return 1
    if [ -n "$ANCLI_PARENT_START" ]; then
        [ "$(sed 's/.*) //' "/proc/$ANCLI_PARENT_PID/stat" 2>/dev/null | cut -d' ' -f20)" = "$ANCLI_PARENT_START" ] || return 1
    fi
    return 0
}}
ANCLI_WRAPPER_PID="$$"
(
    while ancli_parent_alive; do
        sleep 1
    done
    # The foreground PRoot is a direct child of this wrapper. Kill it and the
    # watchdog itself without putting the interactive child in the background.
    for _ancli_pid in $(pgrep -P "$ANCLI_WRAPPER_PID" 2>/dev/null); do
        kill -TERM "$_ancli_pid" 2>/dev/null
    done
    sleep 1
    for _ancli_pid in $(pgrep -P "$ANCLI_WRAPPER_PID" 2>/dev/null); do
        kill -KILL "$_ancli_pid" 2>/dev/null
    done
) &
ANCLI_WATCHDOG_PID=$!
{ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} \\
$ANCLI_BIND_ARGS \\
-b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb $SHM_BIND \\
-w "$PROOT_CWD" /usr/bin/env PATH=/usr/local/bin:/usr/bin:/bin SHELL=/bin/bash BROWSER=/usr/local/bin/xdg-open {executable} "$@"
_ancli_rc=$?
kill "$ANCLI_WATCHDOG_PID" 2>/dev/null || true
wait "$ANCLI_WATCHDOG_PID" 2>/dev/null || true
ancli_restore_tty
exit $_ancli_rc
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
        with open(ancli_bin_path, "w", encoding="utf-8") as f:
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
        with open(sys_path, "w", encoding="utf-8") as f:
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
        with open(tmp_wrapper, "w", encoding="utf-8") as f:
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


def _deploy_agy_host_bash_compat(verbose=True):
    """Install AGY's narrow Android-host ``bash -c`` compatibility entrypoint.

    AGY can ask its remote command runner to start ``bash`` before the request
    reaches the Ubuntu guest. Android normally has only ``/system/bin/sh``.
    The bundled script forwards POSIX-compatible arguments to that shell; it is
    intentionally not a second execution backend and never starts PRoot.
    Existing non-AnCLI ``bash`` files are preserved.
    """
    source = f"{ANCLI_DIR}/bash-host-compat"
    if not os.path.isfile(source):
        if verbose:
            print("\033[90m[i] AGY host bash compatibility file is not bundled.\033[0m")
        return []

    deployed = []
    tmp_file = f"{ANCLI_DIR}/bin/.bash-host-compat.tmp"
    try:
        with open(source, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        if "AnCLI AGY host bash compatibility" not in content:
            if verbose:
                print("\033[93m[!] Refusing an unmarked AGY host compatibility file.\033[0m")
            return []
        with open(tmp_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.chmod(tmp_file, 0o755)

        for instant_bin in (KSU_BIN, AP_BIN):
            if not os.path.isdir(instant_bin):
                continue
            target = f"{instant_bin}/bash"
            replace = not os.path.lexists(target)
            if not replace:
                try:
                    with open(target, "r", encoding="utf-8", errors="replace") as fh:
                        current = fh.read(512)
                    replace = (
                        "AnCLI AGY host bash compatibility" in current
                        or "AnCLI proot shim" in current
                    )
                except OSError:
                    # A real, unreadable user binary is safer to preserve.
                    replace = False
            if not replace:
                if verbose:
                    print(f"\033[90m[i] Preserved existing host bash: {target}\033[0m")
                continue
            rc = os.system(
                f"cp -f {shlex.quote(tmp_file)} {shlex.quote(target)} "
                f">/dev/null 2>&1 && chmod 755 {shlex.quote(target)} >/dev/null 2>&1"
            )
            if rc == 0:
                deployed.append(target)
                if verbose:
                    print(f"\033[92m[OK] AGY host bash compatibility: {target}\033[0m")
            elif verbose:
                print(f"\033[93m[!] Could not deploy AGY host bash compatibility: {target}\033[0m")
    except OSError as e:
        if verbose:
            print(f"\033[93m[!] Could not prepare AGY host bash compatibility: {e}\033[0m")
    finally:
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass
    return deployed

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def _strip_quoted_segments(cmd):
    """Neutralise quoted segments so operator checks apply only to text the
    shell will interpret. Single-quoted spans are fully literal and dropped
    (except ANSI-C `$'...'` quoting, where escapes still expand, so its
    content stays visible to the checks). Inside double quotes `;` `<` `>` `&`
    are also literal, so they are blanked out — but `$(` and backticks still
    execute there, so they remain visible to the checks."""
    cmd = re.sub(r"(?<!\$)'[^']*'", "", cmd)
    return re.sub(
        r'"([^"]*)"',
        lambda m: '"' + re.sub(r"[;&<>]", "", m.group(1)) + '"',
        cmd,
    )


def validate_cmd(cmd, allowed_prefixes=None, allow_operators=True):
    """Security: verify command starts with an allowed prefix and contains no
    shell metacharacters that could escalate to arbitrary command execution.
    `&&`, `||` and `|` are intentionally allowed for install_cmd chains and
    curl | bash fallbacks; pass `allow_operators=False` for anything that runs
    automatically without the user asking (registry version probes).
    Note this is defense-in-depth: commands starting with `bash`/`sh` may still
    run arbitrary script content.

    `allowed_prefixes` extends the whitelist for one call — used by registry
    version probes (`claude --version`), whose executable names come from the
    same registry that already supplies arbitrary install_cmd strings."""
    cmd_stripped = cmd.strip()
    # Drop benign multi-char operators and quoted content, so the standalone
    # dangerous forms (';', '>', '<', '&', newline, $(), ``) can be flagged
    # without rejecting legitimately quoted values (e.g. env K='a;b').
    probe = cmd_stripped
    if allow_operators:
        probe = probe.replace("&&", "").replace("||", "")
    stripped_ops = _strip_quoted_segments(probe)
    operators = ['`', '$(', ';', '>', '<', '&', '\n']
    if not allow_operators:
        operators.append('|')
    for operator in operators:
        if operator in stripped_ops:
            print(f"\033[91m[X] Blocked command with shell operator '{operator}': {cmd_stripped}\033[0m")
            return False
    prefixes = tuple(allowed_prefixes) if allowed_prefixes else ALLOWED_CMD_PREFIXES
    if not any(cmd_stripped.startswith(prefix) for prefix in prefixes):
        print(f"\033[91m[X] Blocked untrusted command: {cmd_stripped}\033[0m")
        print(f"\033[93m    Allowed prefixes: {', '.join(prefixes)}\033[0m")
        return False
    return True

def _registry_exe_prefixes(registry=None):
    """ALLOWED_CMD_PREFIXES plus every registry executable, so a registry-defined
    `version_cmd` (e.g. "claude --version") can run while no arbitrary command can."""
    prefixes = list(ALLOWED_CMD_PREFIXES)
    reg = registry if registry is not None else (_load_bundled_registry() or {})
    for app in (reg.get('apps') or {}).values():
        exe = str(app.get('executable', ''))
        if _is_safe_exec_name(exe):
            prefixes.append(f"{exe} ")
    return tuple(prefixes)

def _is_safe_exec_name(exe):
    """A bare command name (no path separators / traversal) we may whitelist."""
    exe = str(exe or '')
    return bool(exe) and not any(c in exe for c in '/\\') and '..' not in exe \
        and exe.replace('-', '').replace('_', '').isalnum()


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

def _capture_cmd(cmd, allowed_prefixes=None, timeout=None, merge_stderr=False, allow_operators=True):
    """Run a container command and capture its stdout.

    Used by the version probes (`version_cmd` in the registry): the child's raw
    output must never leak into `--json` output, so it is captured instead of
    inherited. `merge_stderr` also captures stderr, for tools that print their
    version there. Returns (ok, text); never raises."""
    if not validate_cmd(cmd, allowed_prefixes, allow_operators=allow_operators):
        return False, ""
    if timeout is None:
        timeout = PROBE_TIMEOUT
    try:
        result = subprocess.run(
            cmd, shell=True, executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return False, ""
    return result.returncode == 0, result.stdout.decode('utf-8', errors='replace')


def _version_on_named_line(text, exe):
    """Conservative parse for the stderr fallback.

    Only a version token on a line that names the tool or the word "version"
    counts — a plain `_first_version()` on stderr would happily read a runtime
    version out of an error banner (e.g. "requires node v20.11.0"), which would
    then be stored as the tool's version and hide real updates."""
    exe = str(exe or '').lower()
    for line in str(text or '').splitlines():
        low = line.lower()
        if (exe and exe in low) or 'version' in low:
            ver = _first_version(line)
            if ver:
                return ver
    return None


def _probe_prefixes(app, registry=None):
    """Whitelist for one probe: registry executables plus this app's own binary
    (self-sufficient on a fresh/offline install where no cache exists)."""
    prefixes = list(_registry_exe_prefixes(registry))
    exe = str(app.get('executable') or '')
    if _is_safe_exec_name(exe):
        prefixes.append(f"{exe} ")
    return tuple(prefixes)


def _probe_cmd_allowed(cmd, app, prefixes):
    """A `version_cmd` runs *automatically* (WebUI load, `ancli check`), so it is
    held to a stricter standard than install_cmd: the first token must be the
    app's own executable and no shell operator is tolerated."""
    tokens = cmd.split()
    if not tokens:
        return False
    exe = str(app.get('executable') or '')
    if not exe or tokens[0] != exe:
        return False
    return validate_cmd(cmd, prefixes, allow_operators=False)


def _probe_installed_version(app, registry=None):
    """Ask the installed tool for its own version via the registry `version_cmd`.

    The exit code is deliberately NOT trusted: some wrappers report a spurious
    non-zero status even after printing a valid version (grok exits 126 on
    device). A version found on stdout is the tool speaking for itself, so it
    wins; the stderr pass is a narrow fallback (stdout empty and the tool
    succeeded) parsed conservatively — see `_version_on_named_line`."""
    cmd = str(app.get('version_cmd') or '').strip()
    if not cmd:
        return None
    prefixes = _probe_prefixes(app, registry)
    if not _probe_cmd_allowed(cmd, app, prefixes):
        print(f"\033[93m[!] Refusing unsafe version_cmd for "
              f"{app.get('name', app.get('executable', '?'))}: {cmd!r} "
              "(must start with its own executable and use no shell operators)\033[0m")
        return None
    ok, out = _capture_cmd(cmd, prefixes, timeout=PROBE_TIMEOUT)
    ver = _first_version(out)
    if ver:
        return ver
    if ok and not out.strip():
        _, merged = _capture_cmd(cmd, prefixes, timeout=PROBE_TIMEOUT, merge_stderr=True)
        return _version_on_named_line(merged, app.get('executable'))
    return None

# ---------------------------------------------------------------------------
# Install / Uninstall / Update / Config / Repair
# ---------------------------------------------------------------------------

def _fix_config_permissions():
    """Hand auth config directories to the shell user (uid 2000) so both root and
    the shell user can read/write their own OAuth credentials.

    Ownership transfer + `u+rwX,go-rwx` instead of `chmod -R 755`: root ignores
    DAC so it keeps access, the shell user owns the files, and other apps can no
    longer read OAuth tokens out of the container."""
    root_dir = f"{ROOTFS}/root"
    if not os.path.isdir(root_dir):
        return
    # chmod the root home dir itself so shell user can enter it
    os.system(f"chmod 755 {root_dir} 2>/dev/null")
    for conf_dir in [".config", ".claude", ".gemini", ".local", ".agents",
                     ".dsh", ".grok", ".mimocode"]:
        full_path = f"{root_dir}/{conf_dir}"
        if os.path.exists(full_path):
            # Use numeric UID 2000 (Android shell) for reliability
            os.system(f"chown -R 2000:2000 {full_path} 2>/dev/null")
            os.system(f"chmod -R u+rwX,go-rwx {full_path} 2>/dev/null")


# Shared skill root read by mimo, agy, grok, dsh and opencode (verified from each
# binary's own path literals). ~/.claude/skills is the same convention for Claude
# Code, which is the one tool that does not read .agents.
SHARED_SKILL_DIR = "/root/.agents/skills"
SKILL_LINK_DIRS = (
    "/root/.claude/skills",
    "/root/.grok/skills",
    "/root/.mimocode/skills",
    "/root/.config/opencode/skills",
    "/root/.dsh/skills"
)

# 2026-11-13: Maintain one generated guest-global context file.  The configured
# default working directory controls tool launch location only; it is not a
# second memory-file location.  Keep the template in the module package so a
# release can update the guidance without embedding it in this core script.
RUNTIME_CONTEXT_NAME = "AGENTS.md"
RUNTIME_CONTEXT_MARKER = "<!-- ancli:runtime-context v1 -->"
RUNTIME_CONTEXT_PENDING_MARKER = "<!-- ancli:first-run:pending -->"
RUNTIME_CONTEXT_FINALIZED_MARKER = "<!-- ancli:first-run:finalized"


def _runtime_context_bootstrap_section():
    """Return the first-run protocol inserted into a fresh device context."""
    return f"""{RUNTIME_CONTEXT_PENDING_MARKER}
## 首次环境确认（仅首次运行）

第一次读取本文件时，先在当前容器内执行 `pwd`、`ancli doctor --json`、`ancli status --json`、`ancli list --json`，再用 `command -v` 和实际命令结果复核要使用的工具。检查 `/system`、`/data`、`/sdcard`、`/storage`、`/mnt`、`/apex`、`/linkerconfig`、`/data/adb` 和 `/mnt/ancli-data` 的存在性、挂载关系与读写能力，确认可用的 Android 宿主命令（例如 `getprop`、`pm`、`dumpsys`、`cmd`、`am`）。

根据探测结果补充本文件的“本设备实测环境”内容，写入真实路径、绑定关系、可用命令、工具版本和操作边界；不要写入 API key、cookie、token、代理地址或其他密钥。完成后执行 `ancli agent-init --finalize`，由 AnCLI 将探测结果写回本文件并删除本节。以后以实测内容为准，仍需在环境变化时重新运行探测。
"""


def setup_skill_dirs(verbose=True):
    """Point every installed agent CLI at one shared global skills directory.

    All agent tools except Claude Code already read `~/.agents/skills`, so that is
    the canonical root; the per-tool directories are symlinked to it (and Claude
    Code's `~/.claude/skills` too). Existing *non-empty* directories are left
    untouched — a user who keeps different skills per tool keeps them. Idempotent.

    Returns {'created': [...], 'linked': [...], 'kept': [...]}. Never raises."""
    result = {"created": [], "linked": [], "kept": []}
    try:
        if not os.path.isdir(SHARED_SKILL_DIR):
            os.makedirs(SHARED_SKILL_DIR, exist_ok=True)
            os.system(f"chown -R 2000:2000 {SHARED_SKILL_DIR} 2>/dev/null")
            os.chmod(SHARED_SKILL_DIR, 0o755)
            result["created"].append(SHARED_SKILL_DIR)
    except Exception as e:
        print(f"\033[93m[!] Could not create {SHARED_SKILL_DIR}: {e}\033[0m")
        return result

    for path in SKILL_LINK_DIRS:
        try:
            if os.path.islink(path):
                if os.path.realpath(path) == os.path.realpath(SHARED_SKILL_DIR):
                    result["linked"].append(path)      # already correct
                else:
                    result["kept"].append(path)        # points somewhere else on purpose
                continue
            if os.path.isdir(path):
                if os.listdir(path):
                    result["kept"].append(path)        # user's own skills: do not touch
                    continue
                os.rmdir(path)                         # empty placeholder -> replace
            elif os.path.exists(path):
                result["kept"].append(path)            # a file: leave it alone
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            os.symlink(SHARED_SKILL_DIR, path)
            result["linked"].append(path)
        except Exception as e:
            print(f"\033[93m[!] Could not link {path}: {e}\033[0m")

    if verbose:
        print(f"\033[92m[OK] Shared skills dir: {SHARED_SKILL_DIR}\033[0m")
        if result["linked"]:
            print(f"\033[92m[OK] Linked to it: {', '.join(result['linked'])}\033[0m")
        if result["kept"]:
            print(f"\033[93m[i] Left as-is (own skills or different target): "
                  f"{', '.join(result['kept'])}\033[0m")
        print("\033[96m[i] Put a skill at "
              f"{SHARED_SKILL_DIR}/<name>/SKILL.md (frontmatter: name + description) "
              "and every agent CLI sees it; project-local <repo>/.agents/skills also works.\033[0m")
    return result


def _effective_default_cwd():
    """Return the directory used when a wrapper is launched from `/`."""
    return get_default_cwd() or DEFAULT_CWD


def _runtime_context_template_paths():
    """Search the live AnCLI payload first, then the source tree used by tests."""
    module_template = os.path.join(ANCLI_DIR, "runtime-context", RUNTIME_CONTEXT_NAME)
    module_copy = os.path.join(MOD_DIR, "ancli", "runtime-context", RUNTIME_CONTEXT_NAME)
    source_copy = os.path.join(os.path.dirname(__file__), "runtime-context", RUNTIME_CONTEXT_NAME)
    return (module_template, module_copy, source_copy)


def _load_runtime_context_template():
    for path in _runtime_context_template_paths():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            if RUNTIME_CONTEXT_MARKER in text:
                return text
        except (OSError, UnicodeError):
            continue
    return None


def _runtime_context_target():
    """Return the single global runtime-context location inside the guest."""
    target_dir = os.path.join(ROOTFS, "root", ".agents")
    return target_dir, os.path.join(target_dir, RUNTIME_CONTEXT_NAME)


def _runtime_context_compat_targets():
    """Return vendor-specific discovery paths backed by the canonical context."""
    gemini_dir = os.path.join(ROOTFS, "root", ".gemini")
    # 2026-11-13: Antigravity builds in the field have used both names. Keep
    # both documented global entries pointed at the same AnCLI context so a
    # clean install and an upgraded container load identical guidance.
    return [
        os.path.join(gemini_dir, "AGENTS.md"),
        os.path.join(gemini_dir, "GEMINI.md"),
    ]


def _sync_runtime_context_compat(target_file, refresh=False):
    """Expose the canonical context at Agent clients' documented global paths."""
    result = []
    for path in _runtime_context_compat_targets():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.islink(path) and os.path.realpath(path) == os.path.realpath(target_file):
                result.append({"path": path, "status": "linked"})
                continue
            if os.path.lexists(path):
                if not refresh:
                    result.append({"path": path, "status": "kept"})
                    continue
                if os.path.islink(path):
                    os.unlink(path)
                elif os.path.isfile(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        if RUNTIME_CONTEXT_MARKER not in fh.read():
                            result.append({"path": path, "status": "kept"})
                            continue
                    os.remove(path)
                else:
                    result.append({"path": path, "status": "kept"})
                    continue
            os.symlink("../.agents/AGENTS.md", path)
            result.append({"path": path, "status": "linked"})
        except OSError as e:
            result.append({"path": path, "status": "error", "reason": str(e)})
    return result


def _remove_legacy_runtime_contexts():
    """Remove only files carrying AnCLI's marker from retired locations."""
    candidates = [
        os.path.join(ROOTFS, "root", RUNTIME_CONTEXT_NAME),
        os.path.join(ROOTFS, "root", ".dsh", RUNTIME_CONTEXT_NAME),
        os.path.join(ANCLI_DIR, "agent-memory", RUNTIME_CONTEXT_NAME),
    ]
    cwd = _effective_default_cwd().strip()
    if cwd.startswith("/") and cwd != "/":
        candidates.append(os.path.join(cwd, RUNTIME_CONTEXT_NAME))
    for path in candidates:
        try:
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                marked = RUNTIME_CONTEXT_MARKER in fh.read()
            if marked:
                os.remove(path)
        except OSError:
            continue


def _installed_memory_summary():
    installed = load_installed()
    if not installed:
        return "无（尚未安装工具）", "无"
    names = []
    env_names = []
    for app_id, info in installed.items():
        name = info.get("name") or app_id
        executable = info.get("executable") or app_id
        names.append(f"{name} (`{executable}`)")
        keys = info.get("configured_env") or list((info.get("env") or {}).keys())
        for key in keys:
            if key not in env_names:
                env_names.append(key)
    return ", ".join(names), ", ".join(env_names) if env_names else "无"


def _render_runtime_context(template):
    installed, configured_env = _installed_memory_summary()
    replacements = {
        "{{ANCLI_VERSION}}": VERSION,
        "{{DEFAULT_CWD}}": _effective_default_cwd(),
        "{{ROOTFS}}": ROOTFS,
        "{{ANCLI_DIR}}": ANCLI_DIR,
        "{{ANDROID_DATA_MNT}}": ANDROID_DATA_MNT,
        "{{SHARED_SKILL_DIR}}": SHARED_SKILL_DIR,
        "{{INSTALLED_APPS}}": installed,
        "{{CONFIGURED_ENV}}": configured_env,
    }
    for key, value in replacements.items():
        template = template.replace(key, str(value))
    return template


def _runtime_context_probe():
    """Collect non-secret facts for the device-specific first-run context."""
    installed = load_installed()
    commands = {}
    for name in ("bash", "python3", "node", "npm", "git", "curl", "java"):
        commands[name] = bool(shutil.which(name))
    android_paths = {}
    for path in (
        "/system", "/data", "/sdcard", "/storage", "/mnt", "/apex",
        "/linkerconfig", "/data/adb", ANDROID_DATA_MNT,
    ):
        android_paths[path] = {
            "present": os.path.exists(path),
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
        }
    host_commands = {}
    for name in ("getprop", "pm", "dumpsys", "cmd", "am"):
        path = f"/system/bin/{name}"
        host_commands[name] = {
            "path": path,
            "present": os.path.exists(path),
            "executable": os.access(path, os.X_OK),
        }
    apps = []
    for app_id, info in installed.items():
        apps.append({
            "id": app_id,
            "name": info.get("name") or app_id,
            "executable": info.get("executable") or app_id,
            "version": info.get("installed_version") or "unknown",
        })
    return {
        "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "uid": getattr(os, "getuid", lambda: None)(),
        "euid": getattr(os, "geteuid", lambda: None)(),
        "cwd": os.getcwd(),
        "execution_backend": "proot-ubuntu",
        "ancli_version": VERSION,
        "paths": {
            "rootfs": ROOTFS,
            "ancli_dir": ANCLI_DIR,
            "default_cwd": _effective_default_cwd(),
            "runtime_context": os.path.join(ROOTFS, "root", ".agents", RUNTIME_CONTEXT_NAME),
            "shared_skills": SHARED_SKILL_DIR,
            "data_channel": ANDROID_DATA_MNT,
        },
        "host_compatibility": {
            "bash": {
                "paths": [f"{KSU_BIN}/bash", f"{AP_BIN}/bash"],
                "installed": any(
                    os.path.isfile(f"{path}/bash") for path in (KSU_BIN, AP_BIN)
                ),
            },
        },
        "commands": commands,
        "android_paths": android_paths,
        "host_commands": host_commands,
        "android_data_mounted": _android_data_mounted(),
        "installed_apps": apps,
        "configured_env_names": sorted({
            key
            for info in installed.values()
            for key in (info.get("configured_env") or list((info.get("env") or {}).keys()))
        }),
    }


def _render_runtime_context_probe(probe):
    """Render a compact, secret-free device probe section."""
    lines = [
        f"{RUNTIME_CONTEXT_FINALIZED_MARKER} {probe['checked_at']} -->",
        "## 本设备实测环境",
        f"- 实测时间：{probe['checked_at']}",
        f"- 执行后端：{probe['execution_backend']}，AnCLI v{probe['ancli_version']}",
        f"- 当前身份：uid {probe['uid']}，euid {probe['euid']}；当前目录：{probe['cwd']}",
        f"- Android 数据通道：{probe['paths']['data_channel']}，挂载状态：{'可用' if probe['android_data_mounted'] else '未确认'}",
        "",
        "### 容器路径",
    ]
    for key, value in probe["paths"].items():
        lines.append(f"- {key}：{value}")
    lines.extend(["", "### 容器工具"])
    for name, available in probe["commands"].items():
        lines.append(f"- {name}：{'可用' if available else '未发现'}")
    lines.extend(["", "### Android 宿主路径"])
    for path, state in probe["android_paths"].items():
        if not state["present"]:
            label = "不存在或未绑定"
        else:
            rw = "读写" if state["writable"] else ("只读" if state["readable"] else "不可读")
            label = f"存在，{rw}"
        lines.append(f"- {path}：{label}")
    lines.extend(["", "### Android 宿主命令入口"])
    for name, state in probe["host_commands"].items():
        if state["present"]:
            label = "存在且可执行" if state["executable"] else "存在但未确认可执行"
        else:
            label = "未发现"
        lines.append(f"- {name}：{state['path']}，{label}")
    compat = probe.get("host_compatibility", {}).get("bash", {})
    compat_label = "已部署" if compat.get("installed") else "未发现"
    lines.extend(["", "### AGY 宿主兼容入口"])
    lines.append(f"- bash：{compat_label}；候选路径：{', '.join(compat.get('paths', []))}")
    lines.extend(["", "### 已安装工具"])
    if probe["installed_apps"]:
        for app in probe["installed_apps"]:
            lines.append(f"- {app['id']}：{app['name']}，入口 {app['executable']}，版本 {app['version']}")
    else:
        lines.append("- 无已安装工具")
    lines.extend([
        "",
        "### 运行边界",
        "- 工具通过宿主 wrapper 进入单层 Ubuntu PRoot；共享存储使用 /sdcard，跨 App 的真实 /data 使用 /mnt/ancli-data/data/<包名>。",
        "- Android 宿主命令必须先用实际结果确认；不要把容器内同名命令自动当成宿主能力。",
        "- 配置通过 ancli config，工具安装和更新通过 ancli install、ancli update；密钥只保存在对应 secrets 文件，不写入本记忆。",
    ])
    return "\n".join(lines)


def _inject_runtime_context_bootstrap(text):
    """Add the first-run protocol to an older generated context in place."""
    if RUNTIME_CONTEXT_PENDING_MARKER in text or RUNTIME_CONTEXT_FINALIZED_MARKER in text:
        return text
    anchor = "\n工具通过宿主 wrapper"
    index = text.find(anchor)
    if index < 0:
        return text
    return text[:index] + "\n\n" + _runtime_context_bootstrap_section().rstrip() + text[index:]


def _replace_runtime_context_bootstrap(text, probe):
    """Replace the first-run protocol with the device-specific probe section."""
    start = text.find(RUNTIME_CONTEXT_PENDING_MARKER)
    if start < 0:
        return None
    end = text.find("\n工具通过宿主 wrapper", start)
    if end < 0:
        return None
    return text[:start] + _render_runtime_context_probe(probe) + text[end:]


def finalize_runtime_context(verbose=True):
    """Write actual device facts into the pending context and remove its prompt."""
    _, target_file = _runtime_context_target()
    result = {"path": target_file, "status": "skipped", "reason": None}
    if not os.path.isfile(target_file) or os.path.islink(target_file):
        result["status"] = "error"
        result["reason"] = "运行时记忆文件不存在或不是普通文件"
        return result
    try:
        with open(target_file, "r", encoding="utf-8") as fh:
            current = fh.read()
        if RUNTIME_CONTEXT_FINALIZED_MARKER in current:
            result["status"] = "already_finalized"
            result["reason"] = "本设备实测内容已经写入"
            result["compatibility"] = _sync_runtime_context_compat(target_file, refresh=False)
            return result
        if RUNTIME_CONTEXT_PENDING_MARKER not in current:
            result["reason"] = "未找到首次环境确认提示，未改写用户文件"
            return result
        probe = _runtime_context_probe()
        updated = _replace_runtime_context_bootstrap(current, probe)
        if updated is None:
            result["reason"] = "首次环境确认区块格式不完整，未改写用户文件"
            return result
        tmp_file = f"{target_file}.ancli.tmp"
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        with open(tmp_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(updated)
        os.chmod(tmp_file, 0o644)
        os.replace(tmp_file, target_file)
        result["status"] = "finalized"
        result["probe"] = probe
        result["compatibility"] = _sync_runtime_context_compat(target_file, refresh=False)
        if verbose:
            print(f"\033[92m[OK] 已将本设备实测环境写入 Agent 记忆：{target_file}\033[0m")
            print("\033[96m[i] 已移除首次环境确认提示；后续会话直接使用实测内容。\033[0m")
    except OSError as e:
        result["status"] = "error"
        result["reason"] = str(e)
    finally:
        try:
            if os.path.exists(f"{target_file}.ancli.tmp"):
                os.remove(f"{target_file}.ancli.tmp")
        except OSError:
            pass
    return result


def setup_runtime_context(verbose=True, refresh=False):
    """Create the guest-global `AGENTS.md` without overwriting user guidance.

    The first run creates the file.  Later runs leave any existing file alone;
    `--refresh` may replace only a file carrying AnCLI's own marker, so a user
    project AGENTS.md can never be clobbered accidentally.  Returns a small
    status dict suitable for the `--json` command.
    """
    target_dir, target_file = _runtime_context_target()
    result = {
        "path": target_file or "",
        "default_cwd": _effective_default_cwd(),
        "status": "skipped",
        "reason": None,
    }
    template = _load_runtime_context_template()
    if template is None:
        result["reason"] = "模块中缺少 runtime-context/AGENTS.md 模板"
        if verbose:
            print(f"\033[93m[!] {result['reason']}，请重新刷入模块或运行开发部署。\033[0m")
        return result

    try:
        _remove_legacy_runtime_contexts()
        os.makedirs(target_dir, exist_ok=True)
        exists = os.path.lexists(target_file)
        # 2026-11-13: Older installs left an empty placeholder at the global
        # memory path. It contains no user guidance, so replace it with the
        # rendered runtime context instead of treating it as authoritative.
        empty_placeholder = (
            exists
            and os.path.isfile(target_file)
            and not os.path.islink(target_file)
            and os.path.getsize(target_file) == 0
        )
        if exists and not refresh and not empty_placeholder:
            with open(target_file, "r", encoding="utf-8", errors="replace") as fh:
                current = fh.read()
            if RUNTIME_CONTEXT_MARKER in current and not (
                RUNTIME_CONTEXT_PENDING_MARKER in current
                or RUNTIME_CONTEXT_FINALIZED_MARKER in current
            ):
                migrated = _inject_runtime_context_bootstrap(current)
                if migrated != current:
                    tmp_file = f"{target_file}.ancli.tmp"
                    with open(tmp_file, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(migrated)
                    os.chmod(tmp_file, 0o644)
                    os.replace(tmp_file, target_file)
                    result["status"] = "migrated"
                    result["reason"] = "已为旧版生成记忆补充首次环境确认协议"
                    result["compatibility"] = _sync_runtime_context_compat(target_file, refresh=False)
                    if verbose:
                        print(f"\033[92m[OK] 已补充首次环境确认协议：{target_file}\033[0m")
                        for item in result["compatibility"]:
                            if item["status"] == "linked":
                                print(f"\033[92m[OK] Agent 兼容记忆入口：{item['path']}\033[0m")
                    return result
            result["status"] = "kept"
            result["reason"] = "目标文件已存在，保留用户内容"
            result["compatibility"] = _sync_runtime_context_compat(target_file, refresh=False)
            if verbose:
                print(f"\033[96m[i] 已保留默认目录中的现有 Agent 记忆文件：{target_file}\033[0m")
                for item in result["compatibility"]:
                    if item["status"] == "linked":
                        print(f"\033[92m[OK] Agent 兼容记忆入口：{item['path']}\033[0m")
            return result
        if exists:
            if os.path.islink(target_file) or not os.path.isfile(target_file):
                result["status"] = "kept"
                result["reason"] = "目标路径不是普通文件，未覆盖"
                if verbose:
                    print(f"\033[93m[i] 未覆盖非普通文件：{target_file}\033[0m")
                return result
            with open(target_file, "r", encoding="utf-8", errors="replace") as fh:
                if RUNTIME_CONTEXT_MARKER not in fh.read():
                    if not empty_placeholder:
                        result["status"] = "kept"
                        result["reason"] = "目标文件不是 AnCLI 生成文件，未覆盖"
                        if verbose:
                            print(f"\033[93m[i] 已保留用户自己的 Agent 记忆文件：{target_file}\033[0m")
                        return result

        tmp_file = f"{target_file}.ancli.tmp"
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        with open(tmp_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_render_runtime_context(template))
        os.chmod(tmp_file, 0o644)
        os.replace(tmp_file, target_file)
        result["status"] = "refreshed" if exists else "created"
        result["compatibility"] = _sync_runtime_context_compat(target_file, refresh=refresh)
        if verbose:
            label = "已更新" if exists else "已创建"
            print(f"\033[92m[OK] {label}默认 Agent 记忆文件：{target_file}\033[0m")
            for item in result["compatibility"]:
                if item["status"] == "linked":
                    print(f"\033[92m[OK] Agent 兼容记忆入口：{item['path']}\033[0m")
    except Exception as e:
        result["status"] = "error"
        result["reason"] = str(e)
        if verbose:
            print(f"\033[93m[!] 无法写入默认 Agent 记忆文件：{e}\033[0m")
    finally:
        try:
            if os.path.exists(f"{target_file}.ancli.tmp"):
                os.remove(f"{target_file}.ancli.tmp")
        except Exception:
            pass
    return result

def _get_android_dns():
    """Resolve the device's real DNS servers, IPv4 preferred, deduplicated.

    The core runs inside the glibc container, where Android's `getprop`/`dumpsys`
    cannot execute at all — so the host-side wrapper (`ancli_env.sh`) probes them
    once per hour and writes `.dns_cache`. That cache is the primary source; the
    direct probes below only ever succeed when the core runs on the host itself
    (tests, or a future non-container path). Returns [] when nothing is usable."""
    import ipaddress
    servers = []

    def add(value):
        value = (value or "").strip().lstrip("/")
        if not value:
            return
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return
        # Skip junk that would break resolution inside the container.
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            return
        value = str(ip)
        if value not in servers:
            servers.append(value)

    try:
        with open(DNS_CACHE, "r") as handle:
            cached = handle.read().split()
        for value in cached[1:]:        # cached[0] is the capture timestamp
            add(value)
    except OSError:
        pass

    if not servers:
        for prop in ["net.dns1", "net.dns2"]:
            try:
                add(subprocess.check_output(f"getprop {prop}", shell=True).decode().strip())
            except Exception:
                continue
    if not servers:
        try:
            dump = subprocess.check_output("dumpsys connectivity", shell=True).decode(errors="replace")
        except Exception:
            dump = ""
        for group in re.findall(r"DnsAddresses:\s*\[([^\]]*)\]", dump):
            for token in group.split(","):
                add(token)

    # IPv4 first, and IPv6 only when the network offers nothing else: glibc reads
    # at most three nameservers, and spending slots on IPv6 resolvers that a
    # VPN-less container may not reach costs resilience.
    ipv4 = [value for value in servers if ":" not in value]
    return ipv4 or servers


def _write_resolv_conf():
    """Write a working /etc/resolv.conf into the container.
    Order: the device's real DNS first (matches the current network *and* any VPN),
    then China-friendly resolvers, then public ones. 8.8.8.8 is last on purpose:
    it is blocked or poisoned on many networks, and a leading unresolvable
    nameserver makes every lookup wait for its timeout."""
    resolv_path = f"{ROOTFS}/etc/resolv.conf"
    # Check if resolv.conf is a symlink, remove it if so to write actual file
    if os.path.islink(resolv_path):
        os.remove(resolv_path)
    nameservers = _get_android_dns()
    for fallback in ["223.5.5.5", "119.29.29.29", "1.1.1.1", "8.8.8.8"]:
        if len(nameservers) >= 3:  # glibc MAXNS: only the first 3 are read
            break
        if fallback not in nameservers:
            nameservers.append(fallback)
    with open(resolv_path, "w") as f:
        for ns in nameservers[:3]:
            f.write(f"nameserver {ns}\n")
    print(f"\033[92m[OK] Container DNS configured: {', '.join(nameservers[:3])}\033[0m")


# ---------------------------------------------------------------------------
# Android app-data channel
# ---------------------------------------------------------------------------
# An `su` started from an app (Termius, an SSH server app, ...) inherits that
# app's mount namespace, where Android hides *other* apps' data behind a tmpfs
# mounted over /data/data (mode 751, only the app's own dirs visible). A plain
# bind of /data does not inherit submounts, so binding it elsewhere exposes the
# real tree again. The wrapper mounts it on the host at ANDROID_DATA_MNT, which
# the container sees at the same path because /mnt is bound into the rootfs.
ANDROID_DATA_MNT = "/mnt/ancli-data"


def _android_data_mounted():
    """True when ANDROID_DATA_MNT is a mount point right now."""
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == ANDROID_DATA_MNT:
                    return True
    except OSError:
        pass
    return False


def ensure_android_data(verbose=True):
    """Expose the real /data at ANDROID_DATA_MNT (idempotent, best effort).

    Runs inside the container, but `mount` is not virtualised by proot: the bind
    lands in the caller's mount namespace, exactly like the manual
    `mount --bind /data /mnt/dtest` that proved the trick on device. Never
    raises — a non-root shell or a read-only /mnt just leaves the channel
    unavailable and the caller carries on."""
    if _android_data_mounted():
        if verbose:
            print(f"\033[92m[OK] App-data channel ready: {ANDROID_DATA_MNT}\033[0m")
        return True
    try:
        os.makedirs(ANDROID_DATA_MNT, exist_ok=True)
        # util-linux mount lives in /usr/bin; do not depend on the caller's PATH.
        for mount_bin in ("/usr/bin/mount", "/bin/mount", "mount"):
            rc = os.system(f"{mount_bin} --bind /data {ANDROID_DATA_MNT} >/dev/null 2>&1")
            if rc == 0 and _android_data_mounted():
                if verbose:
                    print(f"\033[92m[OK] App-data channel mounted: {ANDROID_DATA_MNT}\033[0m")
                return True
    except Exception:
        pass
    if verbose:
        print(f"\033[93m[!] Could not mount {ANDROID_DATA_MNT} (needs root); other apps' "
              f"/data/data stays hidden in this mount namespace.\033[0m")
    return False


def repair_env(registry):
    """Diagnose and fix container environment, resolv.conf, permissions, and wrappers."""
    print("\033[96m[*] Starting environment diagnostics and repair...\033[0m")
    if not isinstance(registry, dict):
        # A malformed/legacy registry must not crash the repair path.
        print("\033[93m[!] Registry payload is not an object; repairing without it.\033[0m")
        registry = {}

    # Guard: ROOTFS must be a real path before doing any recursive operations
    if not ROOTFS or not os.path.isabs(ROOTFS) or len(ROOTFS) < 10:
        print(f"\033[91m[X] Refusing to operate: ROOTFS path looks invalid: {ROOTFS}\033[0m")
        return

    # 0. Decode config values that older WebUI builds stored percent-encoded,
    #    then let the wrapper regeneration below rewrite the secrets files.
    try:
        migrate_encoded_env()
    except Exception as e:
        print(f"\033[93m[!] Could not migrate stored config values: {e}\033[0m")

    # 1. Fix resolv.conf DNS
    try:
        _write_resolv_conf()
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

    # 2.1 One shared global skills directory for every agent CLI
    print("\033[96m[*] Setting up the shared skills directory...\033[0m")
    setup_skill_dirs()

    # 2.2 Keep one concise, generated memory file beside the configured default
    # working directory.  Existing user guidance is intentionally preserved.
    print("\033[96m[*] Setting up the default Agent memory file...\033[0m")
    setup_runtime_context(refresh=True)

    # 2.3 AGY's remote command runner can invoke `bash -c` on Android before
    # entering the Ubuntu guest. Install the narrow host compatibility entrypoint
    # without restoring the removed native backend or any broad command shim.
    _deploy_agy_host_bash_compat()

    # 2.4 Android app-data channel: the real /data without the app namespace's
    #     /data/data mask (no-op when it is already mounted or we lack root).
    print("\033[96m[*] Checking the Android app-data channel...\033[0m")
    try:
        ensure_android_data()
    except Exception as e:
        print(f"\033[93m[!] App-data channel check failed: {e}\033[0m")

    # 3. Repair proot and ancli-core.py executable permissions
    try:
        proot_path = f"{ANCLI_DIR}/bin/proot"
        if os.path.exists(proot_path):
            os.chmod(proot_path, 0o755)
        # Fix binary installation directories. Avoid `chmod -R` here: it would
        # strip setuid bits (e.g. /bin/su, /usr/bin/sudo) inside the rootfs.
        # Instead fix the directories themselves plus each installed tool binary.
        installed_bins = set()
        for bin_dir in ["/usr/local/bin", "/usr/bin", "/bin"]:
            full_bin = f"{ROOTFS}{bin_dir}"
            if os.path.isdir(full_bin):
                os.chmod(full_bin, 0o755)
                try:
                    for entry in os.listdir(full_bin):
                        installed_bins.add(os.path.join(full_bin, entry))
                except OSError:
                    pass
        installed = load_installed()
        for app_id, info in installed.items():
            exec_name = info.get('executable', app_id)
            # The executable may live in any PATH dir of the container
            for candidate in installed_bins:
                if os.path.basename(candidate) == exec_name and os.path.isfile(candidate):
                    os.chmod(candidate, 0o755)
        print("\033[92m[OK] Key binary executable permissions restored (0755).\033[0m")
    except Exception as e:
        print(f"\033[91m[X] Failed to restore binary permissions: {e}\033[0m")

    # 3.1 Deploy xdg-open browser redirect wrapper inside container
    _deploy_xdg_open()
    print("\033[92m[OK] Browser redirect wrapper deployed (/usr/local/bin/xdg-open).\033[0m")

    # 3.2 Re-apply the dsh WebUI cookie patch (npm overwrites it on every update)
    patch_dsh_web_cookie()

    # 4. Repair missing application wrappers
    installed = load_installed()
    if installed:
        print(f"\033[96m[*] Verifying wrappers for installed apps: {', '.join(installed.keys())}...\033[0m")
        if not registry:
            try:
                registry = load_registry()
            except Exception:
                pass

        for app_id, info in installed.items():
            exec_name = info.get('executable', app_id)
            app_reg = registry['apps'].get(app_id) if registry and 'apps' in registry else None
            runtime_env = app_reg.get('runtime_env', []) if app_reg else []
            proxy_mode = app_reg.get('proxy_mode', 'system') if app_reg else 'system'
            stored_env = info.get('env', {})
            generate_proot_wrapper(exec_name, stored_env if stored_env else None, runtime_env,
                                   proxy_mode)
        print("\033[92m[OK] Wrappers updated to latest engine.\033[0m")
    else:
        print("\033[90m[i] No installed apps to repair.\033[0m")

    print("\033[92m[OK] Repair complete! If problems persist, try: ancli config <app_id>\033[0m")

def patch_dsh_web_cookie(verbose=True):
    """Let `dsh web`'s auth cookie survive an Android app-initiated navigation.

    The WebUI authenticates by setting an HttpOnly `SameSite=Strict` cookie on the
    token URL and redirecting to `/`. Chrome treats a navigation started by an
    external app — exactly what our `am start` hand-off does — as cross-site, so
    the Strict cookie is dropped on the redirect and the UI answers
    "authentication required". Typing the URL or opening it from a desktop browser
    works, which is why upstream never sees this. `Lax` still withholds the cookie
    from cross-site POSTs, so the protection that matters is kept.

    npm overwrites this file on every dsh install/update, so call this from repair
    and after installing/updating dsh. Idempotent. Returns the patched paths."""
    patched = []
    candidates = [
        f"{ROOTFS}/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/"
        "@deepseek-ai/dsh-client-connection/lib",
        f"{ROOTFS}/usr/local/lib/node_modules/@deepseek-ai/dsh-client-connection/lib",
    ]
    files = []
    for directory in candidates:
        if os.path.isdir(directory):
            files.extend(sorted(glob.glob(f"{directory}/*.js")))
    if not files:
        files = glob.glob(
            f"{ROOTFS}/usr/local/lib/node_modules/@deepseek-ai/**/dsh-client-connection/lib/*.js",
            recursive=True)

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
                content = f.read()
            if "SameSite=Strict" not in content:
                continue
            updated = content.replace("SameSite=Strict", "SameSite=Lax")
            mode = os.stat(path).st_mode
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write(updated)
            os.chmod(path, mode)
            patched.append(path)
        except Exception as e:
            if verbose:
                print(f"\033[93m[!] Could not patch {path}: {e}\033[0m")
    if verbose:
        if patched:
            print(f"\033[92m[OK] dsh WebUI auth cookie relaxed for Android "
                  f"(SameSite=Lax): {len(patched)} file(s)\033[0m")
        else:
            print("\033[96m[i] dsh WebUI cookie already patched (or dsh is not installed).\033[0m")
    return patched


def _deploy_xdg_open():
    """Give the container a working `xdg-open`.

    Android's own binaries (`am`, `cmd`) cannot execute inside the glibc guest —
    `/system/bin/am` fails with "cmd: inaccessible or not found" — so a shim that
    calls `am` there *looks* successful (exit 0) while opening nothing, which is
    exactly what `dsh web` (via the npm `open` package) hit. The container-side
    shim therefore only queues the URL; the generated wrapper on the host drains
    that queue into `am start`. The host-side copy still calls `am` directly,
    because all supported tools run inside the guest."""
    try:
        xdg_content = """#!/bin/sh
# AnCLI: hand the URL to the host wrapper, which opens it with `am start`.
# Android's binaries cannot run inside this glibc container, so calling `am`
# here would silently do nothing.
_queue="{ancli_dir}/.open_url"
for _arg in "$@"; do
    case "$_arg" in
        --version|-v) echo "xdg-open (AnCLI host bridge)"; exit 0 ;;
        -*) continue ;;
    esac
    case "$_arg" in
        http://*|https://*|mailto:*|tel:*)
            if printf '%s\\n' "$_arg" >> "$_queue" 2>/dev/null; then
                exit 0
            fi
            echo "[AnCLI] could not reach the host bridge; open this yourself: $_arg" >&2
            exit 1
            ;;
    esac
    echo "[AnCLI] cannot open '$_arg' here: Android only opens URLs; use am start on the host." >&2
    exit 1
done
exit 0
""".format(ancli_dir=ANCLI_DIR)
        for name in ("xdg-open", "sensible-browser"):
            target = f"{ROOTFS}/usr/local/bin/{name}"
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.exists(target) or os.path.islink(target):
                try:
                    os.remove(target)
                except Exception:
                    pass
            with open(target, "w") as f:
                f.write(xdg_content)
            os.chmod(target, 0o755)

            # Symlinks in /usr/bin and /bin for hardcoded lookups.
            for link_dir in ["/usr/bin", "/bin"]:
                link_path = f"{ROOTFS}{link_dir}/{name}"
                try:
                    if os.path.exists(link_path) or os.path.islink(link_path):
                        os.remove(link_path)
                    os.symlink(f"/usr/local/bin/{name}", link_path)
                except Exception:
                    pass

    except Exception:
        pass

def _record_installed_version(installed, app_id, app, registry=None):
    """Store the version the *tool itself* reports (registry `version_cmd`).

    Recording the registry's declared version instead is what made the WebUI's
    update badges useless: the record then equals the registry value, so a
    comparison can never see a newer upstream release. Falls back to the
    declared version only when the probe is unavailable — never to the AnCLI
    framework version, which would beat every real tool version."""
    probed = _probe_installed_version(app, registry)
    record = installed[app_id]
    if probed:
        record['installed_version'] = probed
        record['version_verified'] = True
    else:
        record['installed_version'] = record.get('installed_version') or app.get('version') or 'unknown'
        record['version_verified'] = False
    return probed

def _install_proot_common(app_id, app, registry=None):
    """Shared post-install bookkeeping: write wrapper and save state.

    Re-installing an already installed app must NOT lose its configuration:
    the stored env (API keys / proxy / base URL) is carried over and re-injected
    into the regenerated wrapper + secrets file."""
    runtime_env = app.get('runtime_env', [])
    proxy_mode = app.get('proxy_mode', 'system')
    installed = load_installed()
    existing = installed.get(app_id) or {}
    carried_env = existing.get('env') or {}
    carried_env_names = existing.get('configured_env') or list(carried_env.keys())
    if carried_env:
        print(f"\033[92m[i] Keeping the existing configuration: {', '.join(carried_env.keys())}\033[0m")
    generate_proot_wrapper(app['executable'], carried_env if carried_env else {}, runtime_env,
                           proxy_mode)
    _deploy_xdg_open()
    installed[app_id] = {
        "name": app['name'],
        "executable": app['executable'],
        "install_mode": "proot",
        "installed_version": existing.get('installed_version', 'unknown'),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "configured_env": carried_env_names,
    }
    probed = _record_installed_version(installed, app_id, app)
    save_installed(installed)
    # Fix permissions immediately so the tool is usable without a reboot
    _fix_config_permissions()
    setup_skill_dirs(verbose=False)
    # The installed-app summary is generated from installed.json. Refresh the
    # AnCLI-owned runtime context after every successful install so it cannot
    # keep saying that no tools are installed.
    setup_runtime_context(verbose=False, refresh=True)
    if app.get('executable') == 'dsh':
        # A fresh dsh install ships the unpatched cookie attribute again.
        patch_dsh_web_cookie(verbose=False)
    suffix = f" v{probed}" if probed else ""
    # Self-check: an installer whose command "succeeded" but left no runnable
    # binary (wrong runtime, missing file, half-extracted tarball) must not be
    # reported as a success.
    if not probed and not _binary_exists(app['executable']):
        print(f"\033[91m[X] {app['name']} does not seem to be installed: no executable "
              f"'{app['executable']}' was found in the container.\033[0m")
        return False
    print(f"\033[92m[OK] Successfully installed {app['name']}{suffix}! Type '{app['executable']}' to run.\033[0m")
    print(f"\033[93m[i] Configure API keys anytime with: ancli config {app_id}\033[0m")
    return True

def install_app(app_id, registry):
    if app_id not in registry['apps']:
        print(f"\033[91m[X] App {app_id} not found in registry.\033[0m")
        return False
    app = registry['apps'][app_id]

    print(f"\033[92m[*] Installing {app['name']}...\033[0m")
    print(f"\033[96m[i] Backend: proot\033[0m")
    return _install_proot(app_id, app, registry)

def _build_pipe_script_cmd(script_path, installer_env=None, installer_args=""):
    """Build the shell command that runs a downloaded installer script.

    `env KEY=VAL bash script ...` instead of a nested `bash -c '...'` wrapper:
    every token is individually shlex.quote()'d so values with spaces or quotes
    survive intact without shell-quote nesting bugs. Shared with the tests so
    they exercise the production builder instead of a copy of it."""
    cmd = f"bash {shlex.quote(script_path)}"
    if installer_env:
        env_prefix = " ".join(
            f"{shlex.quote(k)}={shlex.quote(v)}" for k, v in installer_env.items()
        )
        cmd = f"env {env_prefix} {cmd}"
    if installer_args:
        # shlex.split honors quoting inside installer_args (e.g. --dir "/a b")
        cmd += " " + " ".join(shlex.quote(a) for a in shlex.split(installer_args))
    return cmd


def _install_pipe_script(app_id, app, registry=None):
    """Install a tool by downloading its installer script via Python urllib, then executing it.
    This is registry-driven and bypasses the ADB shell pipe/escape issues that corrupt
    'curl | bash' style commands when run through adb ('|', '--', etc. get mangled)."""
    installer_url  = app.get('installer_url', '')
    installer_args = app.get('installer_args', '')
    installer_env  = app.get('installer_script_env', {})

    if not installer_url:
        print(f"\033[91m[X] No 'installer_url' in registry entry for {app_id}.\033[0m")
        return False

    script_path = f"/tmp/install_{app_id}.sh"
    try:
        print(f"\033[96m[*] Downloading {app['name']} installer via Python (bypasses pipe escaping)...\033[0m")

        # Propagate proxy if set in process environment
        proxy_url = (os.environ.get('http_proxy') or os.environ.get('https_proxy')
                     or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'))
        if proxy_url:
            handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
            opener  = urllib.request.build_opener(handler)
            urllib.request.install_opener(opener)

        # TLS: verify certs first; fall back to unverified only on cert errors
        # (the container CA store may be incomplete).
        content = _urlopen_verified(installer_url, {'User-Agent': 'Mozilla/5.0'}, timeout=30)
        os.makedirs("/tmp", exist_ok=True)
        with open(script_path, "wb") as f:
            f.write(content)

        cmd = _build_pipe_script_cmd(script_path, installer_env, installer_args)

        if run_cmd(cmd):
            return _install_proot_common(app_id, app, registry)
        print(f"\033[91m[X] Installer script failed for {app_id}.\033[0m")
        return False

    except Exception as e:
        print(f"\033[91m[X] Python downloader failed: {e}\033[0m")
        print("\033[93m[!] Falling back to registry install_cmd...\033[0m")
        if run_cmd(app.get('install_cmd', f"echo 'No install_cmd for {app_id}'" )):
            return _install_proot_common(app_id, app, registry)
        print(f"\033[91m[X] Installation failed.\033[0m")
        return False
    finally:
        # Do not leave downloaded installers behind in the container.
        try:
            if os.path.exists(script_path):
                os.remove(script_path)
        except Exception:
            pass


def _install_proot(app_id, app, registry=None):
    """Install a tool inside the Ubuntu PRoot container.
    Dispatches to the appropriate installer based on the registry 'install_method' field:
      'pipe_script' — downloads an installer script via Python urllib (bypasses ADB escaping)
      'cmd'         — runs install_cmd directly inside the container (default)
    Returns True only when the tool was actually installed."""
    # Ensure 'curl' and 'ca-certificates' exist in container before executing any install commands.
    # The rootfs ships a minimal apt keyring and a half-configured systemd, so a
    # plain `apt-get install` fails signature checks — use the same permissive
    # flags customize.sh uses for its bootstrap.
    if not shutil.which("curl"):
        print("\033[96m[i] Container is missing 'curl'. Auto-installing dependencies via apt...\033[0m")
        apt_cmd = ("apt-get update -qy -o Acquire::AllowInsecureRepositories=true && "
                   "apt-get install -qy --allow-unauthenticated "
                   "-o Acquire::AllowInsecureRepositories=true "
                   "-o Acquire::AllowUnauthenticated=true curl ca-certificates")
        if not run_cmd(apt_cmd):
            print("\033[91m[X] Failed to install 'curl' inside container. Installation might fail.\033[0m")
        else:
            print("\033[92m[OK] 'curl' and certificates installed successfully.\033[0m")

    # Dispatch based on install_method defined in registry
    install_method = app.get('install_method', 'cmd')
    if install_method == 'pipe_script':
        return _install_pipe_script(app_id, app, registry)

    # Generic direct command install path
    if run_cmd(app['install_cmd']):
        return _install_proot_common(app_id, app, registry)
    print(f"\033[91m[X] Installation failed.\033[0m")
    return False


def uninstall_app(app_id, registry):
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return False
    app = registry['apps'].get(app_id, {})
    cmd = app.get('uninstall_cmd', f"echo 'No uninstall cmd for {app_id}'")
    print(f"\033[93m[*] Uninstalling {app.get('name', app_id)}...\033[0m")

    ok = run_cmd(cmd)

    remove_wrapper(app.get('executable', app_id))
    del installed[app_id]
    save_installed(installed)
    setup_runtime_context(verbose=False, refresh=True)
    # Forget the removed app's upstream version so it is not shown as installed.
    cache = _load_update_cache()
    if app_id in (cache.get('latest') or {}):
        cache['latest'].pop(app_id, None)
        _save_update_cache(cache)
    if not ok:
        print("\033[93m[!] The tool's own uninstall command failed, but its wrappers and "
              "install record were removed.\033[0m")
        return False
    print(f"\033[92m[OK] Successfully uninstalled.\033[0m")
    return True


def _reset_remove_path(path):
    """Remove one reset-owned path without following directory symlinks."""
    try:
        if os.path.islink(path) or os.path.isfile(path):
            os.remove(path)
            return True
        if os.path.isdir(path):
            shutil.rmtree(path)
            return True
    except OSError as e:
        print(f"\033[93m[!] Could not remove {path}: {e}\033[0m")
    return False


def _reset_registry_executables():
    """Collect known app executables without a network fetch."""
    names = set()
    try:
        for info in load_installed().values():
            if isinstance(info, dict) and info.get("executable"):
                names.add(str(info["executable"]))
    except Exception:
        pass
    for path in _registry_paths():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for info in (data.get("apps") or {}).values():
                if isinstance(info, dict) and info.get("executable"):
                    names.add(str(info["executable"]))
            if names:
                break
        except (OSError, ValueError, TypeError):
            continue
    return {name for name in names if re.fullmatch(r"[A-Za-z0-9._-]+", name)}


def reset_environment(keep_log=None, verbose=True, reinstall=False):
    """Delete user-installed AnCLI state and recreate the module baseline.

    The immutable module payload and Ubuntu rootfs remain in place. Everything
    generated by app installation, credentials, user config, skills, runtime
    memory, update cache, and WebUI history is removed. ``keep_log`` is used by
    the WebUI so the reset operation can keep its own new output log while
    deleting all previous logs. When ``reinstall`` is true, the pre-reset app
    IDs are installed again from the bundled registry, with
    no old secrets or proxy configuration restored.
    """
    root_home = os.path.join(ROOTFS, "root")
    keep_paths = set()
    if keep_log:
        keep_log = os.path.realpath(str(keep_log))
        ancli_root = os.path.realpath(ANCLI_DIR) + os.sep
        if not keep_log.startswith(ancli_root):
            return {"status": "error", "reason": "keep log must be inside ANCLI_DIR"}
        keep_paths.update({keep_log, keep_log + ".exit", keep_log + ".meta"})

    before = list(load_installed().keys())
    executables = _reset_registry_executables()
    removed = 0

    # Remove every known wrapper and its per-tool secret first. Keep `ancli`
    # itself so the caller can finish the reset and immediately use the module.
    for executable in executables:
        remove_wrapper(executable)

    # Delete app binaries and package trees while preserving the Ubuntu base
    # system. A future install will bootstrap Node/pip payloads again.
    app_paths = [
        os.path.join(ROOTFS, "usr", "local", "lib", "node_modules"),
        os.path.join(ROOTFS, "usr", "local", "lib", "python3.12", "dist-packages"),
        os.path.join(ROOTFS, "usr", "local", "lib", "python3.11", "dist-packages"),
        os.path.join(ROOTFS, "root", ".npm"),
        os.path.join(ROOTFS, "root", ".cache"),
        os.path.join(ROOTFS, "root", ".local"),
        os.path.join(ROOTFS, "root", ".config"),
        os.path.join(ROOTFS, "root", ".claude"),
        os.path.join(ROOTFS, "root", ".dsh"),
        os.path.join(ROOTFS, "root", ".gemini"),
        os.path.join(ROOTFS, "root", ".grok"),
        os.path.join(ROOTFS, "root", ".mimocode"),
    ]
    for pattern in ("python*/dist-packages", "python*/site-packages"):
        app_paths.extend(glob.glob(os.path.join(ROOTFS, "usr", "local", "lib", pattern)))
    for path in app_paths:
        if os.path.lexists(path) and _reset_remove_path(path):
            removed += 1

    # Remove all files placed in /usr/local/bin by app installers, then restore
    # AnCLI's browser hand-off shims below. The directory itself is retained.
    local_bin = os.path.join(ROOTFS, "usr", "local", "bin")
    if os.path.isdir(local_bin):
        for name in os.listdir(local_bin):
            path = os.path.join(local_bin, name)
            if _reset_remove_path(path):
                removed += 1
    os.makedirs(local_bin, exist_ok=True)

    # Remove user files under the guest HOME while retaining Ubuntu's standard
    # shell startup files. Project files under /sdcard are host data and are
    # intentionally outside this reset boundary.
    if os.path.isdir(root_home):
        preserved = {".bashrc", ".profile", ".bash_logout"}
        for name in os.listdir(root_home):
            if name in preserved:
                continue
            path = os.path.join(root_home, name)
            if _reset_remove_path(path):
                removed += 1
    else:
        os.makedirs(root_home, exist_ok=True)

    # Clear AnCLI's mutable state but keep core, proot, registry copies, hosts,
    # runtime-context template, and rootfs itself.
    mutable_files = (
        INSTALLED_FILE, UPDATE_CACHE, DNS_CACHE,
        os.path.join(ROOTFS, "root", ".ancli-registry.json"),
        f"{ANCLI_DIR}/.proxy_cache", f"{ANCLI_DIR}/.proxy_cache_last",
        f"{ANCLI_DIR}/.open_url", f"{ANCLI_DIR}/agent-memory/AGENTS.md",
    )
    for path in mutable_files:
        if os.path.lexists(path) and _reset_remove_path(path):
            removed += 1
    if os.path.lexists(SECRETS_DIR):
        if _reset_remove_path(SECRETS_DIR):
            removed += 1
    # Remove generated wrappers left by an old registry while keeping the core
    # runtime files that make the next install possible.
    bin_dir = os.path.join(ANCLI_DIR, "bin")
    preserved_bin = {"proot", "ancli-core.py", "ancli_env.sh", "registry.json"}
    if os.path.isdir(bin_dir):
        for name in os.listdir(bin_dir):
            if name in preserved_bin:
                continue
            if _reset_remove_path(os.path.join(bin_dir, name)):
                removed += 1
    agent_memory_dir = os.path.join(ANCLI_DIR, "agent-memory")
    if os.path.lexists(agent_memory_dir) and _reset_remove_path(agent_memory_dir):
        removed += 1
    for path in glob.glob(f"{ANCLI_DIR}/.webui_*.log*"):
        if os.path.realpath(path) in keep_paths:
            continue
        if _reset_remove_path(path):
            removed += 1

    os.makedirs(SECRETS_DIR, exist_ok=True)
    try:
        os.chmod(SECRETS_DIR, 0o700)
    except OSError:
        pass
    _deploy_xdg_open()
    setup_runtime_context(verbose=False, refresh=True)
    setup_skill_dirs(verbose=False)
    _fix_config_permissions()
    try:
        with open(f"{ANCLI_DIR}/hosts", "w", encoding="utf-8") as fh:
            fh.write("127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n")
        os.chmod(f"{ANCLI_DIR}/hosts", 0o644)
    except OSError:
        pass

    result = {
        "status": "ok",
        "installed_apps_removed": before,
        "removed_paths": removed,
        "runtime_context": os.path.join(ROOTFS, "root", ".agents", RUNTIME_CONTEXT_NAME),
        "logs_cleared": True,
        "reinstall_requested": bool(reinstall),
        "reinstalled_apps": [],
        "reinstall_failures": [],
    }
    if reinstall and before:
        try:
            registry = load_registry()
            for app_id in before:
                if install_app(app_id, registry):
                    result["reinstalled_apps"].append(app_id)
                else:
                    result["reinstall_failures"].append(app_id)
        except SystemExit as e:
            result["reinstall_failures"] = list(before)
            result["reinstall_error"] = f"registry unavailable (exit {e.code})"
        except Exception as e:
            result["reinstall_failures"] = list(before)
            result["reinstall_error"] = str(e)
        if result["reinstall_failures"]:
            result["status"] = "ok_with_reinstall_errors"
    if verbose:
        print(f"\033[92m[OK] Reset complete: removed {len(before)} installed app record(s) "
              f"and {removed} generated path(s).\033[0m")
        print(f"\033[92m[OK] Fresh Agent memory restored: {result['runtime_context']}\033[0m")
        if reinstall:
            print(f"\033[96m[i] Reinstall requested: {len(result['reinstalled_apps'])} succeeded, "
                  f"{len(result['reinstall_failures'])} failed. Old secrets and proxy settings were not restored.\033[0m")
        if not reinstall:
            print("\033[96m[i] Ubuntu rootfs and the AnCLI module payload were kept; install tools again from the WebUI.\033[0m")
        elif result["reinstall_failures"]:
            print("\033[93m[!] Some apps were not reinstalled; inspect the task log and retry them from the WebUI.\033[0m")
    return result

def update_app(app_id, registry):
    """Update an installed app, regenerate the wrapper with cached env, and record
    the version the tool now reports. Returns True only on a successful update, so
    a failed download surfaces as a non-zero exit instead of a fake success."""
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return False
    app = registry['apps'].get(app_id, {})
    cmd = app.get('update_cmd', f"echo 'No update cmd for {app_id}'")
    name = app.get('name', app_id)
    print(f"\033[93m[*] Updating {name}...\033[0m")

    if not run_cmd(cmd):
        print(f"\033[91m[X] Update failed for {name} — '{cmd}' did not succeed.\033[0m")
        print("\033[93m[!] The installed version is unchanged. Check your network/proxy, "
              "then retry or inspect the log above.\033[0m")
        return False

    cached_env = installed[app_id].get('env', {})
    if cached_env:
        print(f"\033[92m[i] Re-injecting stored configuration keys: {', '.join(cached_env.keys())}\033[0m")

    runtime_env = app.get('runtime_env', [])
    proxy_mode = app.get('proxy_mode', 'system')
    generate_proot_wrapper(app.get('executable', app_id), cached_env if cached_env else None, runtime_env,
                           proxy_mode)

    probed = _record_installed_version(installed, app_id, app, registry)
    installed[app_id]['installed_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_installed(installed)
    setup_runtime_context(verbose=False, refresh=True)
    _fix_config_permissions()
    if app.get('executable') == 'dsh':
        # npm just replaced the packaged files: re-apply the Android cookie patch.
        patch_dsh_web_cookie(verbose=False)

    # The update command always pulls the vendor's latest channel, so immediately
    # re-resolve the official version: the badge/status is correct without waiting
    # for the next full `ancli check`.
    official, source, err = _latest_version(app, registry)
    cache = _load_update_cache()
    latest = dict(cache.get('latest') or {})
    latest[app_id] = {"version": official, "source": source, "error": err}
    _save_update_cache({"ts": cache.get('ts', 0), "latest": latest})

    if probed:
        print(f"\033[92m[OK] Successfully updated {name} to v{probed}.\033[0m")
    else:
        print(f"\033[92m[OK] Successfully updated {name}.\033[0m")
        print(f"\033[93m[!] Could not read the new version ('{app.get('version_cmd', '')}'); "
              "run 'ancli check' to refresh the update status.\033[0m")
    if err:
        print(f"\033[93m[!] Official version lookup failed after the update ({err}); "
              "the update itself succeeded.\033[0m")
    elif official and (app.get('latest') or app.get('version')):
        if probed and _update_available(probed, official, True):
            print(f"\033[93m[!] {name} reports v{probed} but the official latest is v{official} "
                  f"[{source}] — the source may lag behind the vendor; retry later.\033[0m")
        else:
            print(f"\033[96m[i] {name} is on the official latest (v{official}, {source}).\033[0m")
    return True

def reconfigure_app(app_id, registry, set_env=None):
    """Reconfigure env vars and regenerate wrapper for an installed app.
    With set_env (dict from `config <id> --set K=V`), runs non-interactively
    (used by the WebUI); otherwise prompts interactively as before."""
    installed = load_installed()
    if app_id not in installed:
        print(f"\033[93m[!] App {app_id} is not installed.\033[0m")
        return
    app = registry['apps'].get(app_id, {})
    all_vars = app.get('env_vars', []) + app.get('optional_env_vars', [])
    if not all_vars and not set_env:
        # No configurable vars: drop any stale secrets file from earlier
        # versions (e.g. mimo's OPENAI_API_KEY, which the tool ignores).
        secrets_path = f"{SECRETS_DIR}/{app_id}.env"
        if os.path.exists(secrets_path):
            try:
                os.remove(secrets_path)
                print(f"\033[93m[i] Removed stale secrets file for {app_id} (no env vars configured).\033[0m")
            except Exception:
                pass
        print(f"\033[93m[!] No configurable env vars for {app_id}.\033[0m")
        return
    print(f"\033[96m[*] Reconfiguring {app.get('name', app_id)}...\033[0m")

    env_dict = {}
    if set_env:
        # Non-interactive: keep existing values, overlay the --set pairs.
        env_dict = dict(installed[app_id].get('env', {}))
        env_dict.update(set_env)
    else:
        for var in all_vars:
            prev_val = installed[app_id].get('env', {}).get(var, '')
            hint = f" [{prev_val}]" if prev_val else ""
            val = input(f"\033[96mEnter {var}{hint} (leave blank to keep/skip): \033[0m").strip()

            if val:
                env_dict[var] = val
            elif prev_val:
                env_dict[var] = prev_val  # Keep existing value if skipped

    runtime_env = app.get('runtime_env', [])
    proxy_mode = app.get('proxy_mode', 'system')
    generate_proot_wrapper(app.get('executable', app_id), env_dict if env_dict else None, runtime_env,
                           proxy_mode)

    installed[app_id]['env'] = env_dict
    save_installed(installed)
    print(f"\033[92m[OK] Reconfigured and wrapper regenerated.\033[0m")

# ---------------------------------------------------------------------------
# WebUI JSON API (non-interactive, machine-readable output)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _stdout_to_stderr():
    """Keep `--json` stdout parseable: progress/diagnostics go to stderr.

    Redirects at BOTH levels: the Python-level `sys.stdout` (our own prints and
    official version checks) and file descriptor 1 (anything a child process
    writes — `os.system`/`subprocess` helpers, which ignore `sys.stdout`)."""
    sys.stdout.flush()
    saved_fd = None
    try:
        saved_fd = os.dup(1)
        os.dup2(2, 1)
    except OSError:
        # fd 1 closed/duplicated away: fall back to the Python-level redirect
        # rather than failing the whole `--json` command.
        if saved_fd is not None:
            try:
                os.close(saved_fd)
            except OSError:
                pass
            saved_fd = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        if saved_fd is not None:
            try:
                os.dup2(saved_fd, 1)
            finally:
                os.close(saved_fd)


def _binary_exists(exec_name):
    """Check whether a tool's binary exists inside the container."""
    return (os.path.exists(f"{ROOTFS}/usr/local/bin/{exec_name}") or
            os.path.exists(f"{ROOTFS}/usr/bin/{exec_name}") or
            os.path.exists(f"{ROOTFS}/root/.local/bin/{exec_name}"))


def _first_version(text):
    """Extract the first version-like token from arbitrary tool output:
    'v2.1.283' -> '2.1.283', 'grok 1.0.0 (3cd0d0cbce)' -> '1.0.0',
    '0.1.7-rc.2' -> '0.1.7-rc.2' (a prerelease suffix stays attached).
    Up to four numeric segments are kept. None if nothing parses."""
    m = re.search(r'(\d+(?:\.\d+){1,3})(?:-([0-9A-Za-z.\-]+))?', str(text or ''))
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)


def _ver_key(v):
    """Total-order sortable key for a version string, prerelease-aware.

    Semver-ish ordering matters here because some upstreams (DeepSeek Harness)
    ship only prereleases: '0.1.7-rc.2' < '0.1.7-rc.3' < '0.1.7'. Comparing bare
    numeric cores would call all three equal and freeze the update badge.
    Returns None when no version can be parsed (callers treat that as unknown)."""
    m = re.search(r'(\d+(?:\.\d+){0,3})(?:[-+]([0-9A-Za-z.\-]+))?', str(v or ''))
    if not m:
        return None
    core = [int(x) for x in m.group(1).split('.')]
    core = (core + [0, 0, 0, 0])[:4]
    prerelease = m.group(2)
    if not prerelease:
        return tuple(core) + (1, 0, 0, 0, 0)
    nums = [int(x) for x in re.findall(r'\d+', prerelease)][:4]
    nums = (nums + [0, 0, 0, 0])[:4]
    return tuple(core) + (0,) + tuple(nums)


def _update_available(local_ver, cloud_ver, local_trusted=True):
    """True when the upstream version is newer than the installed one.

    `local_trusted=False` marks install records written before version probing
    existed: they hold the AnCLI framework version (e.g. 1.2.2), which compares
    "newer" than a tool's real version and would silently hide every update.
    For those records any difference counts as a candidate — one `ancli check`
    (or `ancli update`) rewrites the record with the tool's real version."""
    cv = _ver_key(cloud_ver)
    if cv is None:
        return False
    lv = _ver_key(local_ver)
    if lv is None:
        return True
    if not local_trusted:
        return lv != cv
    return lv < cv


def _json_path(data, path):
    """Resolve a dotted path ('info.version') inside parsed JSON; None when missing."""
    cur = data
    for part in str(path or '').split('.'):
        if not part:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _latest_version(app, registry):
    """Resolve a tool's *official* latest version from its upstream source.

    Registry `latest` spec (see AGENTS.md §6):
      {"source": "github", "repo": "owner/repo"}       -> releases/latest tag_name
      {"source": "pypi",   "package": "name"}          -> info.version
      {"source": "npm",    "package": "name"}          -> version of the latest dist-tag
      {"source": "json",   "url": URL, "path": "a.b"}  -> dotted path in a JSON document
      {"source": "text",   "urls": [URL, ...]}         -> first version token in a text body
      {"source": "static"}                             -> the registry's declared "version"
      {"source": "none"}                               -> detection unavailable by design

    Returns (version|None, source_label, error|None). Never raises: a network
    failure must not break `list`/`check` — it is reported per app instead."""
    spec = app.get('latest') if isinstance(app.get('latest'), dict) else {}
    src = spec.get('source')
    # Human/machine label used in logs, the update cache and the WebUI.
    label = src or 'unknown'
    if src == 'github':
        label = f"github:{spec.get('repo', '')}"
    elif src in ('pypi', 'npm'):
        label = f"{src}:{spec.get('package', '')}"
    elif src == 'json':
        label = 'manifest'
    elif src == 'text':
        label = 'channel'
    known_sources = ('github', 'pypi', 'npm', 'json', 'text', 'static', 'none', None)
    if src not in known_sources:
        return None, label, f"unknown latest.source {src!r}"
    try:
        if src == 'github':
            data = _http_json(f"https://api.github.com/repos/{spec.get('repo', '')}/releases/latest")
            return _first_version(data.get('tag_name')), label, None
        if src == 'pypi':
            data = _http_json(f"https://pypi.org/pypi/{spec.get('package', '')}/json")
            return _first_version(_json_path(data, 'info.version')), label, None
        if src == 'npm':
            pkg = str(spec.get('package', ''))
            # A scoped name is one path segment on the registry: @scope%2Fname
            data = _http_json(f"https://registry.npmjs.org/{pkg.replace('/', '%2F')}/latest")
            return _first_version(_json_path(data, 'version')), label, None
        if src == 'json':
            data = _http_json(spec.get('url', ''))
            return _first_version(_json_path(data, spec.get('path', 'version'))), label, None
        if src == 'text':
            last_err = None
            for url in (spec.get('urls') or [spec.get('url')]):
                if not url:
                    continue
                try:
                    body = _http_text(url)
                except Exception as e:
                    last_err = e
                    continue
                # A channel pointer is a bare version; require it to be one, so an
                # HTML error page can never be parsed into a bogus "version".
                ver = _strict_channel_version(body)
                if ver:
                    return ver, label, None
                last_err = last_err or ValueError('no version token in channel response')
            return None, label, str(last_err) if last_err else 'no version in channel response'
    except Exception as e:
        return None, label, str(e)

    if src == 'none':
        return None, 'none', None
    # 'static' (or a legacy entry without a spec): the registry's declared version.
    # Never fall back to the AnCLI framework version — it would compare "newer"
    # than every real tool version and hide updates.
    declared = app.get('version')
    if declared:
        return declared, 'registry', None
    return None, label, "no declared version and no latest source"


def _strict_channel_version(body):
    """First line of a channel pointer that is *only* a version number."""
    for line in str(body or '').splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.fullmatch(r'v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.\-]+)?)', line)
        return m.group(1) if m else None
    return None


def _load_update_cache():
    """Results of the last `ancli check` (never fatal: {} when absent/corrupt)."""
    try:
        with open(UPDATE_CACHE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if not isinstance(data.get('latest'), dict):
                # A hand-edited/older cache must not crash the CLI later.
                data['latest'] = {}
            data.setdefault('ts', 0)
            return data
    except Exception:
        pass
    return {"ts": 0, "latest": {}}


def _save_update_cache(cache):
    """Write the update cache atomically; failure is a warning, never an error."""
    tmp = f"{UPDATE_CACHE}.tmp"
    try:
        # Only clear a stale temp file: os.replace() swaps the cache in one step,
        # so a reader never observes a missing file (the old remove+rename pair
        # left such a window).
        if os.path.exists(tmp):
            os.remove(tmp)
        with open(tmp, "w") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, UPDATE_CACHE)
        # Root-owned 0644, like installed.json: AnCLI runs under su, and the cache
        # only holds public version strings (no secrets).
        os.chmod(UPDATE_CACHE, 0o644)
        return True
    except Exception as e:
        print(f"\033[93m[!] Could not write update cache: {e}\033[0m")
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def _merge_installed_fields(updates):
    """Persist {app_id: {field: value}} without clobbering concurrent writers.

    `ancli check` can run for a minute or more (probes + upstream queries) while
    the user installs/updates from the WebUI. Writing back a snapshot taken at the
    start would roll those records back, or delete an app installed in the
    meantime. So re-read the database right before writing and merge only the
    fields this run is authoritative for. Returns the number of fields changed."""
    if not updates:
        return 0
    current = load_installed()
    changed = 0
    for aid, fields in updates.items():
        record = current.get(aid)
        if not isinstance(record, dict):
            continue          # uninstalled meanwhile -> do not resurrect it
        for key, value in fields.items():
            if record.get(key) != value:
                record[key] = value
                changed += 1
    if changed:
        save_installed(current)
    return changed


def check_updates(registry=None, probe=True):
    """Refresh both halves of update detection:

      1. the *installed* version of every installed tool (registry `version_cmd`),
      2. the tool's *official latest* version (registry `latest` source),

    and persist (2) in UPDATE_CACHE so `ancli list --json` / the WebUI stay fast
    and offline-safe. Returns the cache dict (with `failed`/`succeeded` counts)."""
    registry = registry if registry is not None else load_registry()
    installed = load_installed()
    # Repair config values that older WebUI builds stored percent-encoded. Merged
    # (not saved wholesale) together with the probes at the end of this run.
    try:
        migrated_env = migrate_encoded_env(installed, save=False)
    except Exception as e:
        print(f"\033[93m[!] Could not migrate stored config values: {e}\033[0m")
        migrated_env = {}

    cache = _load_update_cache()
    latest = dict(cache.get('latest') or {})
    pending = {aid: {'env': env} for aid, env in migrated_env.items()}
    failed = 0
    succeeded = 0

    if not installed:
        print(f"\033[93m{_t('check_no_apps')}\033[0m")

    for aid in list(installed.keys()):
        app = (registry.get('apps') or {}).get(aid)
        if not app:
            print(f"\033[93m{_t('check_registry_missing', aid)}\033[0m")
            continue
        name = app.get('name', aid)

        if probe:
            probed = _probe_installed_version(app, registry)
            if probed:
                pending.setdefault(aid, {}).update(
                    {'installed_version': probed, 'version_verified': True})
                print(f"\033[92m{_t('check_installed', name, probed)}\033[0m")
            else:
                print(f"\033[93m{_t('check_version_fail', name, app.get('version_cmd', '') or 'version_cmd')}\033[0m")

        ver, src, err = _latest_version(app, registry)
        previous = latest.get(aid) if isinstance(latest.get(aid), dict) else {}
        if err:
            failed += 1
            # Keep the last known-good version: a transient outage must not wipe it
            # (the UI would fall back to the registry's declared value for 6 hours).
            latest[aid] = {
                "version": previous.get('version'),
                "source": previous.get('source') or src,
                "error": err,
            }
            print(f"\033[93m{_t('check_lookup_fail', name, err)}\033[0m")
            if previous.get('version'):
                print(f"\033[90m{_t('check_keep_version', previous['version'])}\033[0m")
        elif ver:
            succeeded += 1
            latest[aid] = {"version": ver, "source": src, "error": None}
            local = pending.get(aid, {}).get('installed_version') or installed[aid].get('installed_version', 'unknown')
            trusted = 'installed_version' in pending.get(aid, {}) or not app.get('version_cmd')
            mark = _t('check_update_available') if _update_available(local, ver, trusted) else _t('check_up_to_date')
            print(f"\033[96m{_t('check_official', name, ver, src, mark)}\033[0m")
        else:
            latest[aid] = {"version": None, "source": src, "error": None}
            print(f"\033[90m{_t('check_no_source', name)}\033[0m")

    # Drop entries for tools that are gone (kept small and honest).
    latest = {aid: entry for aid, entry in latest.items() if aid in installed}

    _merge_installed_fields(pending)

    cache = {
        "ts": int(time.time()),
        "latest": latest,
        "succeeded": succeeded,
        "failed": failed,
    }
    if not _save_update_cache(cache):
        # The caller may still use the in-memory result.
        print("\033[93m[!] Update results are in effect for this run only "
              "(the cache could not be written; a SELinux-restricted shell can do that).\033[0m")
    return cache


def _app_record(aid, app, installed_flag, info, entry=None):
    """One WebUI/JSON record: registry metadata + install state + update verdict."""
    entry = entry if isinstance(entry, dict) else {}
    exec_name = info.get('executable', app.get('executable', aid))
    local_ver = info.get('installed_version', 'unknown')
    # Records written before version probing hold the AnCLI framework version and
    # must not be trusted blindly (that is what hid real updates).
    verified = bool(info.get('version_verified'))
    trusted = verified or not app.get('version_cmd')
    spec = app.get('latest') if isinstance(app.get('latest'), dict) else {}
    declared_source = spec.get('source') or 'registry'

    # Prefer the official version resolved by `ancli check`; otherwise fall back to
    # the registry's declared value and say so (source 'registry', not verified).
    checked = False
    if entry.get('version'):
        cloud_ver = entry['version']
        source = entry.get('source') or declared_source
        # A 'registry' source means the value came from the registry file itself
        # (static/declared fallback), not from a live upstream lookup.
        checked = source != 'registry'
    elif declared_source == 'none':
        cloud_ver = None
        source = 'none'
    else:
        cloud_ver = app.get('version', 'unknown')
        source = 'registry'

    update_avail = bool(
        installed_flag
        and source != 'none'
        and cloud_ver not in ('unknown', '', None)
        and _update_available(local_ver, cloud_ver, trusted)
    )
    return {
        "id": aid,
        "name": app.get('name', aid),
        "description": app.get('description', ''),
        "installed": installed_flag,
        "active": _binary_exists(exec_name) if installed_flag else False,
        "installed_version": local_ver,
        "version_verified": verified,
        "cloud_version": cloud_ver,
        "cloud_source": source,
        "cloud_checked": checked,
        "cloud_error": entry.get('error'),
        "update_available": update_avail,
        "update_cmd": app.get('update_cmd', ''),
        # Only expose an explicitly declared, non-interactive launcher to the
        # WebUI.  Terminal agents need a real TTY and must remain terminal-only.
        "launch": app.get('launch') if isinstance(app.get('launch'), dict) else None,
        "required_env_vars": app.get('env_vars', []),
        "optional_env_vars": app.get('optional_env_vars', []),
        "configured_keys": info.get('configured_env') or list((info.get('env') or {}).keys()),
    }


def list_apps_json():
    """Bundled catalog + install state + last known official versions as JSON.

    Offline-safe by design: it reads the module catalog and the
    `.update_cache.json` written by `ancli check`."""
    with _stdout_to_stderr():
        registry = _load_bundled_registry() or {}
        installed = load_installed()
        cache = _load_update_cache()
        latest = cache.get('latest') or {}
        apps = []
        for aid, app in (registry.get('apps') or {}).items():
            apps.append(_app_record(aid, app, aid in installed, installed.get(aid, {}), latest.get(aid)))
    print(json.dumps({
        "ancli_version": VERSION,
        "apps": apps,
        "last_check": cache.get('ts', 0),
        # Shorten the auto-check window after a failed run so a transient outage
        # (or GitHub rate limiting) is retried instead of frozen for 6 hours.
        "check_ttl": CHECK_TTL if not cache.get('failed') else CHECK_TTL_FAILED,
        "check_failed": cache.get('failed', 0),
    }, ensure_ascii=False))


def check_updates_json():
    """`ancli check --json`: refresh installed + official versions, emit the verdict."""
    with _stdout_to_stderr():
        registry = load_registry()
        cache = check_updates(registry)
        installed = load_installed()
        latest = cache.get('latest') or {}
        apps = []
        for aid, app in (registry.get('apps') or {}).items():
            if aid in installed:
                apps.append(_app_record(aid, app, True, installed[aid], latest.get(aid)))
    print(json.dumps({
        "ancli_version": VERSION,
        "checked_at": cache.get('ts', 0),
        "updates": sum(1 for a in apps if a['update_available']),
        "succeeded": cache.get('succeeded', 0),
        "failed": cache.get('failed', 0),
        "apps": apps,
    }, ensure_ascii=False))
    return cache



def status_json():
    """Container/module status as pure JSON (used by the WebUI)."""
    with _stdout_to_stderr():
        payload = {
            "ancli_version": VERSION,
            "rootfs_ready": os.path.exists(f"{ROOTFS}/bin/bash"),
            "proot_deployed": os.path.exists(f"{ANCLI_DIR}/bin/proot"),
            "installed_count": len(load_installed()),
            "android_data": _android_data_mounted(),
            "host_bash_compat": any(
                os.path.isfile(f"{path}/bash") for path in (KSU_BIN, AP_BIN)
            ),
        }
    print(json.dumps(payload, ensure_ascii=False))


def doctor_json():
    """Return a read-only path and runtime capability report for agents."""
    with _stdout_to_stderr():
        installed = load_installed()
        paths = {
            "rootfs": ROOTFS,
            "ancli_dir": ANCLI_DIR,
            "default_cwd": _effective_default_cwd(),
            "runtime_context": os.path.join(ROOTFS, "root", ".agents", RUNTIME_CONTEXT_NAME),
            "shared_skills": SHARED_SKILL_DIR,
            "data_channel": ANDROID_DATA_MNT,
        }
        commands = {}
        for name in ("bash", "python3", "node", "npm", "git", "curl", "java"):
            commands[name] = bool(shutil.which(name))
        payload = {
            "ancli_version": VERSION,
            "execution_backend": "proot-ubuntu",
            "rootfs_ready": os.path.exists(f"{ROOTFS}/bin/bash"),
            "proot_deployed": os.path.exists(f"{ANCLI_DIR}/bin/proot"),
            "android_data": _android_data_mounted(),
            "paths": paths,
            "commands": commands,
            "installed_apps": sorted(installed.keys()),
            "configured_env_names": sorted({
                key
                for info in installed.values()
                for key in (info.get("configured_env") or list((info.get("env") or {}).keys()))
            }),
        }
    print(json.dumps(payload, ensure_ascii=False))


def parse_set_env(argv):
    """Parse `--set KEY=VALUE` pairs from argv into a dict.

    Values arrive percent-encoded from the WebUI (`encodeURIComponent`) because
    they are pasted into a shell command string; decode them here. `unquote` is
    idempotent for values without %XX, so hand-typed input still works."""
    result = {}
    i = 0
    while i < len(argv):
        if argv[i] == "--set" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition('=')
            if k:
                result[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
            i += 2
        else:
            i += 1
    return result


def migrate_encoded_env(installed=None, save=True):
    """Repair env values stored by earlier builds as literal percent-encoding.

    The WebUI always sent `--set KEY=<encodeURIComponent(value)>`, but the core
    stored the value verbatim until now, so tools received
    'https%3A%2F%2Fapi.deepseek.com%2Fanthropic' instead of a URL. Decoding is
    idempotent for clean values. Affected secrets files are rewritten so the
    wrappers, which source them at runtime, pick the fixed value up immediately.

    Returns {app_id: fixed_env} for the apps that changed; with save=False the
    caller persists the result itself (see `_merge_installed_fields`)."""
    installed = installed if installed is not None else load_installed()
    fixed_envs = {}
    for aid, info in (installed or {}).items():
        if not isinstance(info, dict):
            continue
        env = info.get('env') or {}
        if not isinstance(env, dict):
            continue
        fixed = {k: urllib.parse.unquote(str(v)) for k, v in env.items()}
        if fixed != env:
            info['env'] = fixed
            fixed_envs[aid] = fixed
            try:
                _write_secrets_file(info.get('executable', aid), fixed)
            except Exception:
                pass
    if fixed_envs:
        if save:
            save_installed(installed)
        print(f"\033[92m[OK] Decoded previously mis-encoded config values for: "
              f"{', '.join(fixed_envs)}\033[0m")
    return fixed_envs


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def show_menu():
    global LANG
    registry = load_registry()

    while True:
        installed = load_installed()
        print(f"\n\033[1;36m{_t('title')}\033[0m")
        apps = list(registry['apps'].keys())
        for i, app_id in enumerate(apps, 1):
            app = registry['apps'][app_id]
            mode_tag = f"\033[90m[{app.get('install_mode', 'proot')}]\033[0m"
            status = f"\033[92m{_t('installed')}\033[0m" if app_id in installed else ""
            print(f"[{i}] {app['name']} {mode_tag} - {app['description']} {status}")

        print(f"[c] {_t('check_opt')}")
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
        elif choice.lower() == 'c':
            print(f"\n\033[1;36m{_t('check_title')}\033[0m")
            check_updates(registry)
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

            elif action == "reset":
                # Full reset is intentionally offline and requires an explicit
                # flag. The WebUI adds --keep-log so the reset itself remains
                # visible while every previous task log is removed.
                if "--yes" not in sys.argv:
                    print("[X] Refusing full reset without --yes.")
                    print("    This deletes installed apps, secrets, config, memory and logs.")
                    sys.exit(2)
                keep_log = None
                reinstall = "--reinstall" in sys.argv[2:]
                for arg in sys.argv[2:]:
                    if arg.startswith("--keep-log="):
                        keep_log = arg.split("=", 1)[1]
                        break
                if "--json" in sys.argv:
                    with _stdout_to_stderr():
                        result = reset_environment(keep_log=keep_log, reinstall=reinstall, verbose=False)
                    print(json.dumps(result, ensure_ascii=False))
                    sys.exit(0 if result.get("status") == "ok" else 1)
                result = reset_environment(keep_log=keep_log, reinstall=reinstall, verbose=True)
                sys.exit(0 if result.get("status") == "ok" else 1)

            # 'list' reads the bundled catalog and local state only.
            # 'check' refreshes installed versions and each vendor's official version.
            # Install/update/config/repair use the same bundled catalog.
            if action == "list":
                if "--json" in sys.argv:
                    list_apps_json()
                    sys.exit(0)
                registry  = _load_bundled_registry()  # offline-safe, no network
                installed = load_installed()
                latest_map = (_load_update_cache().get('latest') or {})
                if not installed:
                    print(_t("no_apps_installed_msg"))
                else:
                    print(f"\033[1;36m{_t('installed_apps_title')}\033[0m")
                    for aid, info in installed.items():
                        date      = info.get('installed_at', 'unknown')
                        local_ver = info.get('installed_version', 'unknown')

                        # Integrity check: verify binary exists inside PRoot
                        exec_name = info.get('executable', aid)
                        bin_exists = _binary_exists(exec_name)
                        status_tag = f"\033[92m{_t('app_active')}\033[0m" if bin_exists else f"\033[91m{_t('app_broken')}\033[0m"

                        # Update hint: prefer the upstream version from `ancli check`,
                        # else the registry's declared version (bundled fallback).
                        app_reg = (registry or {}).get('apps', {}).get(aid, {})
                        entry = latest_map.get(aid) or {}
                        cloud_ver = entry.get('version') or app_reg.get('version', 'unknown')
                        source = entry.get('source') or 'registry'
                        trusted = bool(info.get('version_verified')) or not app_reg.get('version_cmd')
                        update_tag = ""
                        if source != 'none' and _update_available(local_ver, cloud_ver, trusted):
                            update_tag = f" \033[93m{_t('app_update_available', cloud_ver)}\033[0m \033[90m({source})\033[0m"

                        verified_tag = "" if info.get('version_verified') or not app_reg.get('version_cmd') else " \033[90m(unverified — run 'ancli check')\033[0m"
                        print(f"  \033[92m{aid}\033[0m: {info.get('name', aid)} (v{local_ver}){verified_tag} {status_tag}{update_tag}")
                        print(_t("app_installed_at", date))
                        persisted_keys = info.get('configured_env') or list((info.get('env') or {}).keys())
                        if persisted_keys:
                            print(_t("app_config_keys", ', '.join(persisted_keys)))
            elif action == "skills":
                # One shared global skills dir + per-tool links (idempotent).
                if "--json" in sys.argv:
                    with _stdout_to_stderr():
                        result = setup_skill_dirs(verbose=False)
                        memory = setup_runtime_context(verbose=False)
                    print(json.dumps({"shared": SHARED_SKILL_DIR,
                                      "linked": result["linked"],
                                      "kept": result["kept"],
                                      "created": result["created"],
                                      "runtime_context": memory}, ensure_ascii=False))
                else:
                    setup_skill_dirs()
                    setup_runtime_context()
                sys.exit(0)
            elif action == "agent-init":
                refresh = "--refresh" in sys.argv
                finalize = "--finalize" in sys.argv
                if "--json" in sys.argv:
                    with _stdout_to_stderr():
                        result = (finalize_runtime_context(verbose=False)
                                  if finalize else setup_runtime_context(verbose=False, refresh=refresh))
                    print(json.dumps(result, ensure_ascii=False))
                else:
                    result = (finalize_runtime_context()
                              if finalize else setup_runtime_context(refresh=refresh))
                sys.exit(1 if result.get("status") == "error" else 0)
            elif action == "status":
                # No catalog needed: loading one would add work before this status
                # on stdout and break the WebUI's JSON.parse (observed on device).
                if "--json" in sys.argv:
                    status_json()
                    sys.exit(0)
                print(f"AnCLI v{VERSION}")
                print(f"rootfs ready: {os.path.exists(f'{ROOTFS}/bin/bash')}")
                print(f"proot deployed: {os.path.exists(f'{ANCLI_DIR}/bin/proot')}")
                print(f"installed apps: {len(load_installed())}")
                print(f"app-data channel: "
                      f"{'ready at ' + ANDROID_DATA_MNT if _android_data_mounted() else 'not mounted'}")
                sys.exit(0)
            elif action == "doctor":
                doctor_json()
                sys.exit(0)
            elif action == "config" and app_id in ("--help", "-h"):
                # 2026-11-13: Keep config help offline and independent of the
                # registry; app-specific keys are listed by `ancli list --json`.
                print_help()
                sys.exit(0)
            elif action == "config" and (not app_id or app_id.startswith("-")):
                # Global settings, not a per-app env dialog: no registry needed,
                # so it is dispatched before the catalog and version-check paths.
                settings_command(sys.argv[2:])
                sys.exit(0)
            elif action == "check":
                if "--json" in sys.argv:
                    cache = check_updates_json()
                    # Non-zero when nothing could be resolved, so the WebUI's
                    # background log does not claim success on a dead network.
                    sys.exit(1 if (cache.get('failed') and not cache.get('succeeded')) else 0)
                print(f"\033[1;36m=== AnCLI update check ===\033[0m")
                result = check_updates()
                if result.get('succeeded'):
                    print("\033[92m[OK] Update status refreshed. See 'ancli list' for the verdict.\033[0m")
                elif result.get('failed'):
                    # Every upstream lookup failed: report it instead of a fake OK.
                    print(f"\033[91m[X] No official version could be resolved "
                          f"({result['failed']} lookup(s) failed).\033[0m")
                    print("\033[93m[!] Check your network/proxy, then retry.\033[0m")
                    sys.exit(1)
                else:
                    print("\033[93m[i] Nothing to check (no installed apps).\033[0m")
                sys.exit(0)
            else:
                registry = load_registry()
                ok = True
                if action == "install" and app_id:
                    ok = install_app(app_id, registry)
                elif action == "uninstall" and app_id:
                    ok = uninstall_app(app_id, registry)
                elif action == "update" and app_id:
                    ok = update_app(app_id, registry)
                elif action == "config" and app_id:
                    set_env = parse_set_env(sys.argv[3:])
                    reconfigure_app(app_id, registry, set_env if set_env else None)
                elif action == "repair":
                    repair_env(registry)
                else:
                    print_help()
                # Non-zero exit on a failed install/update/uninstall so the WebUI
                # reports the failure instead of logging a bogus success.
                if ok is False:
                    sys.exit(1)
        else:
            show_menu()
    except (KeyboardInterrupt, EOFError):
        print("\n\033[93m[!] Operation cancelled by user. Exiting.\033[0m")
        sys.exit(0)
