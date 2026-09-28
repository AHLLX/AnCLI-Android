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
import contextlib

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

VERSION = "1.2.3"

REGISTRY_URL   = "https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/registry.json"
LOCAL_REGISTRY = "/root/.ancli-registry.json"   # persistent and writable inside proot
INSTALLED_FILE = f"{ANCLI_DIR}/installed.json"
SECRETS_DIR    = f"{ANCLI_DIR}/secrets"   # Per-tool API key files (mode 0600)
CONFIG_FILE    = "/root/.ancli-config.json"
# Upstream "official latest version" cache written by `ancli check` and merged
# into `ancli list --json` (so the WebUI shows update badges without needing a
# network round-trip on every page load).
UPDATE_CACHE   = f"{ANCLI_DIR}/.update_cache.json"
DNS_CACHE      = f"{ANCLI_DIR}/.dns_cache"   # real DNS servers, probed on the host by ancli_env.sh
CHECK_TTL      = 6 * 3600                 # seconds; the WebUI auto-checks when older
CHECK_TTL_FAILED = 15 * 60                # shorter retry window when a check had failures
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

CURRENT_CONFIG = load_config()
LANG = CURRENT_CONFIG.get("lang", "zh")

STRINGS = {
    "zh": {
        "title": "=== 🚀 AnCLI 应用商店 ===",
        "repair_opt": "修复环境 (修复 DNS、权限、封装)",
        "check_opt": "检查官方更新 (联网查询工具最新版本)",
        "check_title": "=== 🔄 官方版本检查 ===",
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
  ancli check                    联网检查官方最新版本与可更新项
  ancli skills                   统一各 Agent 的全局 skills 目录（~/.agents/skills）
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
        "check_opt": "Check for updates (query official latest versions)",
        "check_title": "=== 🔄 Official version check ===",
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
  ancli check                    Check official latest versions and available updates
  ancli skills                   Unify the agent CLIs' global skills dir (~/.agents/skills)
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



def _fetch_registry_once(req):
    """Perform one registry fetch with cert verification enabled; fall back to an
    unverified context only when certificate validation fails (the PRoot container
    may ship an incomplete CA store). Plain network errors propagate to the retry loop."""
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except (ssl.SSLError, urllib.error.URLError) as e:
        if not _is_cert_verification_error(e):
            raise
        # Cert validation failed -> warn and retry once without verification,
        # scoped to this request.
        print(f"\033[93m[!] TLS certificate verification failed ({e}); retrying this request without verification.\033[0m")
        with urllib.request.urlopen(req, timeout=15, context=ssl._create_unverified_context()) as response:
            return json.loads(response.read().decode())


def fetch_registry():
    # Local test mode fallback
    if os.path.exists(f"{ANCLI_DIR}/test_mode"):
        # Offline local-registry mode (device testing): newest copy wins, so a
        # stale file cannot shadow a freshly deployed one.
        for p in _registry_cache_candidates() or _registry_cache_paths():
            if os.path.exists(p):
                try:
                    with open(p, "r") as f:
                        return json.load(f)
                except Exception:
                    pass

    last_net_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(REGISTRY_URL, headers={'User-Agent': 'Mozilla/5.0'})
            data = _fetch_registry_once(req)
            # Try to cache locally; silently ignore if the filesystem is read-only
            # (e.g. SELinux denies writes from inside the PRoot context).
            try:
                with open(LOCAL_REGISTRY, "w") as f:
                    json.dump(data, f)
            except OSError:
                pass
            return data
        except urllib.error.HTTPError as e:
            # 4xx means the URL/branch is wrong — retrying cannot fix it, and
            # silently serving a stale cache is how users end up with an app
            # "not found in registry". Fail loudly and immediately.
            if 400 <= e.code < 500 and e.code != 429:
                print(f"\033[91m[X] Registry fetch rejected with HTTP {e.code}: {REGISTRY_URL}\033[0m")
                print("\033[93m    The registry URL/branch is broken; fix it instead of retrying.\033[0m")
                last_net_err = e
                break
            last_net_err = e
            if attempt < 2:
                print(f"\033[93m[!] Retry {attempt+1}/3: {e}\033[0m")
                time.sleep(2)
        except Exception as e:
            last_net_err = e
            if attempt < 2:
                print(f"\033[93m[!] Retry {attempt+1}/3: {e}\033[0m")
                time.sleep(2)
    # All retries exhausted, fall back to the newest local copy: the cache
    # written by an earlier successful fetch, else the registry shipped inside
    # the module ZIP (needed for a first boot with no network at all).
    candidates = _registry_cache_candidates()
    cached = _load_local_registry_cache()
    if cached is not None:
        source = candidates[0] if candidates else 'unknown'
        kind = 'fetched cache' if os.path.basename(source) == os.path.basename(LOCAL_REGISTRY) \
            else 'bundled registry'
        print(f"\033[93m[!] Using local {kind} ({source}) — network unavailable: {last_net_err}\033[0m")
        return cached

    print(f"\033[91m[X] Failed to fetch registry, no cache, and no fallback found: {last_net_err}\033[0m")
    sys.exit(1)

# Registry copies that can exist on disk (resolved at call time so tests and
# path overrides stay valid). The core reads 'bin/registry.json' (shipped by
# customize.sh); 'registry.json' is accepted too because older module builds
# deployed it there.
def _registry_cache_paths():
    return (LOCAL_REGISTRY, f"{ANCLI_DIR}/bin/registry.json", f"{ANCLI_DIR}/registry.json")

def _registry_cache_candidates():
    """Existing registry files, newest modification time first.

    Choosing by mtime means a stale leftover can never shadow a freshly
    fetched/deployed registry (the bug that hid new per-app versions before)."""
    found = []
    for p in _registry_cache_paths():
        try:
            if os.path.exists(p):
                found.append((os.path.getmtime(p), p))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in found]

def _load_local_registry_cache():
    """Load registry from local disk cache only — no network request.
    Returns None if no cache is available."""
    for p in _registry_cache_candidates():
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
        with open(secrets_path, "w") as f:
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

def generate_proot_wrapper(executable, env_dict=None, runtime_env_list=None, native=False):
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
    if native:
        # Native mode: the tool is a statically-linked binary (Go/Rust) that
        # runs directly on Android without proot/glibc. The binary lives in the
        # container filesystem but is executed by the host kernel directly.
        wrapper = f"""#!/system/bin/sh
# AnCLI wrapper for: {executable} (native — runs directly on Android, no proot)

# 1. Load host-mode environment (Android PATH, proxy, locale, TZ, ...)
. {ANCLI_DIR}/bin/ancli_env.sh host 2>/dev/null || true

# 2. Load tool-specific secrets (mode 0600, not embedded in this script)
{secrets_line}

# 3. Inject static runtime env vars (from registry)
{static_exports}
# 4. Browser redirect for OAuth logins (deployed by 'ancli repair')
if [ -x {KSU_BIN}/xdg-open ]; then
    export BROWSER={KSU_BIN}/xdg-open
fi

# 5. Run the static binary straight from the container filesystem.
#    The binary is statically linked, so it needs no glibc or proot.
#    Search the candidate install locations: some installer versions place
#    the binary under /root/.local/bin instead of /usr/local/bin.
for _cand in \
    {ROOTFS}/usr/local/bin/{executable} \
    {ROOTFS}/usr/bin/{executable} \
    {ROOTFS}/root/.local/bin/{executable}; do
    if [ -x "$_cand" ]; then
        exec "$_cand" "$@"
    fi
done
echo "[AnCLI] {executable} binary not found inside the container." >&2
echo "        Run 'ancli install {executable}' to install it." >&2
exit 127
"""
    else:
        wrapper = f"""#!/system/bin/sh
# AnCLI wrapper for: {executable}

# 1. Load centralized proxy & environment variables
. {ANCLI_DIR}/bin/ancli_env.sh 2>/dev/null || true

# 2. Load tool-specific secrets (mode 0600, not embedded in this script)
{secrets_line}

# 3. Inject static runtime env vars (from registry)
{static_exports}
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
            PROOT_CWD=/root
        fi
        ;;
esac
# Launching from / (or an unmapped path) means TUI tools (mimo/claude/opencode/...)
# scan the whole container filesystem at startup — rootfs plus the /data and
# /sdcard bindings (hundreds of thousands of files) — which hangs the TUI.
# Redirect to the container HOME so startup stays fast.
if [ "$PROOT_CWD" = "/" ]; then
    PROOT_CWD=/root
    echo "[AnCLI] Launched from /; using /root (container HOME) as working directory." >&2
    echo "        cd to a project dir (e.g. /sdcard/...) to work there." >&2
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
# 7. Launch PRoot with unified global binds
# By binding all common Android root directories (/sdcard, /storage, /mnt, /data, /apex, /system),
# we prevent Node.js fs.realpath and other symlink-following logic from breaking.
exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} \\
    -b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system \\
    -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb $SHM_BIND \\
    -w "$PROOT_CWD" /usr/bin/env BROWSER=/usr/local/bin/xdg-open {executable} "$@"
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
    reg = registry if registry is not None else (_load_local_registry_cache() or {})
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
    "/root/.dsh/skills",
)


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
            generate_proot_wrapper(exec_name, stored_env if stored_env else None, runtime_env,
                                   app_reg.get('native', False) if app_reg else False)
        print("\033[92m[OK] Wrappers updated to latest engine.\033[0m")
    else:
        print("\033[90m[i] No installed apps to repair.\033[0m")

    # 5. Deploy native-mode toolchain shims (git/bash/curl bridge into the
    #    container) so native tools can shell out to them on the host.
    _deploy_native_shims()

    print("\033[92m[OK] Repair complete! If problems persist, try: ancli config <app_id>\033[0m")

def _deploy_native_shims():
    """Create proot shims (git/bash/curl) in ANCLI_DIR/bin so native-mode
    tools (agy/grok, which run directly on Android) can reach the container
    toolchain when the Android host lacks those commands.
    Each shim re-enters the container via proot; the container PATH is set so
    the tool's own subprocesses (ssh, pager, ...) resolve inside the rootfs."""
    shim_template = f"""#!/system/bin/sh
# AnCLI proot shim: runs the container's equivalent of this command.
# Bridges native-mode tools to the container toolchain (git/bash/curl).
TOOL=$(basename "$0")
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
exec {ANCLI_DIR}/bin/proot -r {ROOTFS} -b /dev -b /proc -b /sys -b {ANCLI_DIR} \\
    -b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system \\
    -b {ANCLI_DIR}/hosts:/etc/hosts -b /data/adb \\
    -w "$PWD" /usr/bin/env "$TOOL" "$@"
"""
    for tool in ["git", "bash", "curl"]:
        shim_path = f"{ANCLI_DIR}/bin/{tool}"
        try:
            if os.path.exists(shim_path):
                os.remove(shim_path)
            with open(shim_path, "w") as f:
                f.write(shim_template)
            os.chmod(shim_path, 0o755)
        except Exception:
            pass
    print(f"\033[92m[OK] Native toolchain shims deployed (git/bash/curl) in {ANCLI_DIR}/bin\033[0m")

def _deploy_xdg_open():
    """Give the container a working `xdg-open`.

    Android's own binaries (`am`, `cmd`) cannot execute inside the glibc guest —
    `/system/bin/am` fails with "cmd: inaccessible or not found" — so a shim that
    calls `am` there *looks* successful (exit 0) while opening nothing, which is
    exactly what `dsh web` (via the npm `open` package) hit. The container-side
    shim therefore only queues the URL; the generated wrapper on the host drains
    that queue into `am start`. The host-side copy still calls `am` directly,
    because native-mode tools (agy/grok) run on the host."""
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

        # Host-side opener (native-mode tools run on the host, where `am` works).
        host_content = """#!/system/bin/sh
PATH="/system/bin:$PATH" /system/bin/am start -a android.intent.action.VIEW -d "$1" >/dev/null 2>&1
"""
        tmp_xdg = f"{ANCLI_DIR}/bin/.xdg-open.tmp"
        try:
            with open(tmp_xdg, "w") as f:
                f.write(host_content)
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
    installed = load_installed()
    existing = installed.get(app_id) or {}
    carried_env = existing.get('env') or {}
    if carried_env:
        print(f"\033[92m[i] Keeping the existing configuration: {', '.join(carried_env.keys())}\033[0m")
    generate_proot_wrapper(app['executable'], carried_env if carried_env else {}, runtime_env,
                           app.get('native', False))
    _deploy_xdg_open()
    installed[app_id] = {
        "name": app['name'],
        "executable": app['executable'],
        "install_mode": "proot",
        "installed_version": existing.get('installed_version', 'unknown'),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "env": carried_env,
    }
    probed = _record_installed_version(installed, app_id, app)
    save_installed(installed)
    # Fix permissions immediately so the tool is usable without a reboot
    _fix_config_permissions()
    setup_skill_dirs(verbose=False)
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
    import shutil
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
    generate_proot_wrapper(app.get('executable', app_id), cached_env if cached_env else None, runtime_env,
                           app.get('native', False))

    probed = _record_installed_version(installed, app_id, app, registry)
    installed[app_id]['installed_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_installed(installed)
    _fix_config_permissions()

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
    generate_proot_wrapper(app.get('executable', app_id), env_dict if env_dict else None, runtime_env,
                           app.get('native', False))

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
    `fetch_registry` retries) and file descriptor 1 (anything a child process
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


def _ver_tuple(v):
    """Parse 'v1.2.3' / '1.2.3' / '1.2.3.4' / 'grok-dev@1.1.7' into an int tuple;
    None if unparsable. Four segments are kept — truncating at three made
    '1.0.0.1' compare equal to '1.0.0' and hid updates."""
    m = re.search(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?', str(v))
    if not m:
        return None
    parts = [int(x) for x in m.groups() if x is not None]
    return tuple(parts) or None


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
    registry = registry if registry is not None else fetch_registry()
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
        print("\033[93m[i] No installed apps to check.\033[0m")

    for aid in list(installed.keys()):
        app = (registry.get('apps') or {}).get(aid)
        if not app:
            print(f"\033[93m[!] {aid} is installed but not in the registry; skipping.\033[0m")
            continue
        name = app.get('name', aid)

        if probe:
            probed = _probe_installed_version(app, registry)
            if probed:
                pending.setdefault(aid, {}).update(
                    {'installed_version': probed, 'version_verified': True})
                print(f"\033[92m[OK] {name}: installed v{probed}\033[0m")
            else:
                print(f"\033[93m[!] {name}: could not read the installed version "
                      f"(no usable '{app.get('version_cmd', '') or 'version_cmd'}')\033[0m")

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
            print(f"\033[93m[!] {name}: official version lookup failed ({err})\033[0m")
            if previous.get('version'):
                print(f"\033[90m    keeping the previously resolved v{previous['version']}\033[0m")
        elif ver:
            succeeded += 1
            latest[aid] = {"version": ver, "source": src, "error": None}
            local = pending.get(aid, {}).get('installed_version') or installed[aid].get('installed_version', 'unknown')
            trusted = 'installed_version' in pending.get(aid, {}) or not app.get('version_cmd')
            mark = "-> update available" if _update_available(local, ver, trusted) else "(up to date)"
            print(f"\033[96m[i] {name}: official v{ver} [{src}] {mark}\033[0m")
        else:
            latest[aid] = {"version": None, "source": src, "error": None}
            print(f"\033[90m[i] {name}: no official version source configured\033[0m")

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
        "native": bool(app.get('native', False)),
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
        "required_env_vars": app.get('env_vars', []),
        "optional_env_vars": app.get('optional_env_vars', []),
        "configured_keys": list(info.get('env', {}).keys()),
    }


def list_apps_json():
    """Registry + install state + last known official versions as pure JSON.

    Offline-safe by design: it reads the local registry cache and the
    `.update_cache.json` written by `ancli check` (the WebUI triggers a check
    when that cache is older than CHECK_TTL)."""
    with _stdout_to_stderr():
        registry = _load_local_registry_cache() or {}
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
        registry = fetch_registry()
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

            # 'list' reads local state only — no network needed.
            # 'check' refreshes both the cloud registry and the upstream versions.
            # All write-ops (install/update/config/repair) fetch the latest cloud registry.
            if action == "list":
                if "--json" in sys.argv:
                    list_apps_json()
                    sys.exit(0)
                registry  = _load_local_registry_cache()  # offline-safe, no network
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
                        # else the registry's declared version (local cache, no network).
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
                        persisted_keys = list(info.get('env', {}).keys())
                        if persisted_keys:
                            print(_t("app_config_keys", ', '.join(persisted_keys)))
            elif action == "skills":
                # One shared global skills dir + per-tool links (idempotent).
                if "--json" in sys.argv:
                    with _stdout_to_stderr():
                        result = setup_skill_dirs(verbose=False)
                    print(json.dumps({"shared": SHARED_SKILL_DIR,
                                      "linked": result["linked"],
                                      "kept": result["kept"],
                                      "created": result["created"]}, ensure_ascii=False))
                else:
                    setup_skill_dirs()
                sys.exit(0)
            elif action == "status":
                # No registry needed: fetching one would let retry diagnostics land
                # on stdout and break the WebUI's JSON.parse (observed on device).
                if "--json" in sys.argv:
                    status_json()
                    sys.exit(0)
                print(f"AnCLI v{VERSION}")
                print(f"rootfs ready: {os.path.exists(f'{ROOTFS}/bin/bash')}")
                print(f"proot deployed: {os.path.exists(f'{ANCLI_DIR}/bin/proot')}")
                print(f"installed apps: {len(load_installed())}")
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
                registry = fetch_registry()
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
