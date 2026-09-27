# AI Agent Guidelines (AGENTS.md)

Welcome, fellow AI Assistant. If you are reading this, you have been tasked with modifying or maintaining the **AnCLI** project. This document contains critical context, constraints, and architectural details to help you immediately understand the codebase and avoid common Android-specific pitfalls.

## 1. Project Context & Purpose
AnCLI is a unified, systemless environment manager and plugin-based installer designed to bring full Linux command-line tools (like Node.js, Go, or Python-based AI agents) to rooted Android devices.
Because Android uses the Bionic C library, native Linux binaries often crash. AnCLI solves this by using `proot` to run a pure Ubuntu `glibc` base system transparently on Android.

## 2. Core Architecture
You must understand these three pillars before writing any code:

1. **`src/module/customize.sh` (The Magisk Module Installer)**: 
   - Replaces the old `install.sh`. This script is executed by the Android Root Manager (Magisk/KernelSU) when the module ZIP is flashed.
   - Downloads PRoot and a minimal `ubuntu-base` tarball, extracting it to `/data/local/tmp/ancli/rootfs`.
   - Bootstraps APT dependencies (`python3`, `nodejs`, `npm`) during the flash process.
2. **`src/ancli-core.py` (The Package Manager Brain)**: 
   - Runs *inside* the Proot Ubuntu container.
   - Fetches `registry.json` from the cloud.
   - Generates Bash wrappers and injects them into the Android host.
   - `ancli check` resolves each installed tool's *real* version (`version_cmd`) and the
     vendor's *official* latest version (`latest` in the registry), caching both in
     `/data/local/tmp/ancli/.update_cache.json` for the WebUI.
   - **CONSTRAINT**: Must rely strictly on the Python Standard Library (e.g., use `urllib.request`, NOT `requests`).
3. **`src/module/ancli_env.sh` (The Environment Bridger)**:
   - Sourced dynamically by the wrapper script before PRoot execution.
   - Detects Android system proxies (via `dumpsys`) and exports them.
   - Fixes permission locks and mounts `TMPDIR=/tmp`.
4. **`src/registry.json` (The Plugin Schema)**:
   - Contains the installation logic (`pip`, `npm`, `apt`) for third-party CLIs.

## 3. The "Dual-Injection" Systemless Hack (CRITICAL)
Android's `/system/bin` is strictly Read-Only. To expose a newly installed tool (like `aider`) globally to the user's `$PATH`, you **CANNOT** write to `/system/bin`.
Instead, `ancli-core.py` uses a Dual-Injection method:
- **Path A**: `/data/adb/modules/ancli/system/bin/<tool>` (Standard Systemless Module path, takes effect after the phone reboots).
- **Path B**: `/data/adb/ksu/bin/<tool>` or `/data/adb/ap/bin/<tool>` (Dynamic KernelSU/Apatch paths, takes effect instantly without reboot).

*Rule: If you are generating a new wrapper or shortcut, it MUST be written to both Path A and Path B.*

## 4. Technical Constraints & Red Lines (CRITICAL)
When modifying the script execution logic or registry installer commands, you MUST strictly adhere to the following rules:

1. **NO Nested PRoot execution**:
   - `ancli-core.py` runs *inside* the PRoot container. Therefore, do NOT wrap any commands executed by `run_cmd` inside another `proot` invocation block. Doing so causes nested virtualization ptrace lockups, leading to crashes like `/system/bin/sh: bash: inaccessible or not found`.
2. **Bypass ADB Shell Escaping using Python**:
   - Complex installation scripts (e.g. `curl | bash -s -- --dir`) fail when run through adb due to shell escaping bugs where characters like `|` or `--` get corrupted. If a tool installation script contains complex pipe execution, override it to download the script file natively via Python's `urllib.request` inside `/tmp` and then execute it.
3. **Always Force-Delete files before writing wrappers**:
   - Android's root and shell users create files with conflicting ownerships. When generating wrapper scripts, you MUST check if the file already exists and explicitly delete it using `os.remove` before recreating, otherwise the operation will fail with `[Errno 13] Permission denied`.
4. **Command Whitelist Validation**:
   - Any execution inside the container must be explicitly whitelisted in `ALLOWED_CMD_PREFIXES` in `ancli-core.py`. Add `bash ` and `sh ` if running local shell script installers.
   - Registry `version_cmd` probes (`claude --version`) go through `_capture_cmd(..., _registry_exe_prefixes())`: the executable names come from the registry and extend the whitelist for that one call only (`validate_cmd` default stays strict).
5. **Do NOT force delete container data during Manager uninstall (CRITICAL)**:
   - Magisk/KSU Manager uninstallations trigger `uninstall.sh` in non-interactive background. 
   - You MUST detect TTY redirection and preserve the `/data/local/tmp/ancli/rootfs` and `installed.json` by default (only wipe `bin/` scripts), unless explicitly forced by `/data/local/tmp/ancli_force_purge`. This ensures Python dependencies (like `gitpython`) and API keys are not lost during module upgrades.

## 5. Physical Layout Mapping
When debugging paths or writing cleanup logic, refer to this exact physical mapping (Host Android perspective):

- **Ubuntu Rootfs**: `/data/local/tmp/ancli/rootfs/`
- **NPM Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/node_modules/`
- **Pip Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/python3.12/dist-packages/`
- **Core Script**: `/data/local/tmp/ancli/bin/ancli-core.py`
- **Update Cache**: `/data/local/tmp/ancli/.update_cache.json` (official latest versions from `ancli check`)
- **Magisk Module**: `/data/adb/modules/ancli/`

## 6. Adding New Apps to Registry
To add support for a new CLI tool, do not modify `ancli-core.py`. Instead, add a new JSON object to `registry.json`.

**Required Schema:**
```json
"app_id": {
  "name": "Human Readable Name",
  "install_cmd": "npm install -g something",
  "update_cmd": "npm update -g something",
  "uninstall_cmd": "npm uninstall -g something",
  "env_vars": ["ANY_REQUIRED_API_KEY"], 
  "optional_env_vars": ["ANY_PROXY_BASE_URL"],
  "executable": "command_name_to_bind",
  "version_cmd": "something --version",
  "latest": { "source": "npm", "package": "something" },
  "version": "1.2.3"
}
```
*Note: If `env_vars` is provided, `ancli-core.py` will automatically prompt the user for them and inject them into the wrapper via `export KEY="VALUE"`.*

**Update detection fields (never omit them):**
- `version_cmd` — the **installed** version is read from the tool itself inside the container
  (ask the vendor's real binary; verified prefixes are auto-whitelisted via `_registry_exe_prefixes`).
  Do not gate the probe on the exit code: `grok --version` prints the version but exits 126 on
  device, and some tools print it on stderr — `_probe_installed_version` accepts any version found
  in the output (stdout first, then a stderr-merged retry).
- `latest` — where the **official latest** version comes from. Supported sources:
  `github` (`repo`), `pypi` (`package`), `npm` (`package`), `json` (`url` + `path`),
  `text` (`urls[]`, first version token wins), `static` (no public API — uses `version`),
  `none` (detection unavailable; the UI says so).
- `version` — declared fallback used when the network is down or the source is `static`.
- Rule: **never** treat a hand-written `version` as the official latest, and never write the
  registry/AnCLI version into `installed_version` — that silently disables update detection
  (see CHANGELOG: "WebUI could not detect official versions").

## 7. Testing Constraints
- There is **no Android emulator** for this project — and an emulator could not verify this module anyway (it needs a rooted real device).
- Only use `adb` when a device is actually attached (`adb devices` lists one). Even then, stick to read-only inspection (`ls`, `cat`, `settings get`, `logcat`) unless the user explicitly asks you to flash/test.
- **NEVER** claim the module was flashed or verified when it was not, and never flash/uninstall on your own. Fall back to `sh -n` syntax checks, `python -m pytest tests/`, and static reasoning.
- Update detection has two runnable checks: `python -m pytest tests/` (offline unit suite) and `python scripts/smoke_update_check.py` (hits the real upstreams and prints the WebUI payload for the known device state).

## 8. Network Proxy & VPN Constraints (CRITICAL)
- **VPN Bypass**: Android VPNs in TUN mode (e.g. Clash, v2rayNG) bypass Root (UID 0) traffic by default. If a Python or curl script runs as root and the proxy is not explicitly set, it will ignore the VPN and fail to connect to domains like `github.com`.
- **Proxy Override**: Always parse the Android system proxy via `dumpsys connectivity`. Extract the `HttpProxy` IP and port. 
- **DO NOT hardcode 127.0.0.1**: Android users often run proxy software on other devices (e.g. PC at `192.168.1.x`) and set the Android proxy to that IP. Always use the IP dynamically returned by `dumpsys`.
- **Go Socket Binds**: Go binaries (like `agy`) crash when binding to `198.18.0.x` virtual IPs created by Clash TUN. Ensure `ancli_env.sh` pre-binds these to the loopback interface (`ip addr add 198.18.0.x/32 dev lo`).

## 9. PRoot Path Resolution & TUI Crashes
- **The `/sdcard` Trap**: Android's `/sdcard` is a complex chain of symlinks pointing to `/mnt/user/0/...` or `/storage/emulated/0`. 
- **Node.js/Bun TUI Panics**: If you run Node.js-based tools (like `mimo`) inside PRoot while inside `/sdcard`, the tool will attempt to resolve its absolute physical path. If PRoot is missing `-b /mnt` or `-b /data` binds, the physical path resolution will fail, causing the Event Loop or TUI library to silently crash (you won't even see an error, the TUI simply won't launch).
- **The Solution**: NEVER use dynamic path binding heuristics (e.g. `case "$PWD"`). Instead, ALWAYS bind the entire physical tree unconditionally when generating wrappers: `-b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig`. This ensures all underlying symlink targets exist within the container.

## 10. Version & Release Checklist (CRITICAL)
Releases are consumed by Magisk/KernelSU/APatch managers through `update.json`, so **every version number lives in three places and must move together**:

| Where | Field | Must match |
| :--- | :--- | :--- |
| `src/module/module.prop` | `version` / `versionCode` | source of truth; `versionCode` must always increase |
| `update.json` | `version` / `versionCode` / `zipUrl` / `changelog` | same version, and `zipUrl` must point at the **exact** asset name of the new Release |
| GitHub Release asset | `ancli-<version>.zip` | file name must equal the basename in `zipUrl` (case included) |

Release procedure (copy-paste, in order):

```bash
# 1) bump version: src/module/module.prop (version=vX.Y.Z, versionCode=N+1) and update.json (same + zipUrl/changelog)
# 2) update CHANGELOG.md (managers display it via update.json -> changelog URL)
# 3) build the flashable zip (build.sh syncs src/* into src/module/ancli/ first)
sh build.sh
unzip -l ancli-vX.Y.Z.zip | head          # sanity: module.prop must be at the ZIP ROOT, not nested
# 4) commit + tag + push
git add -A && git commit -m "release: vX.Y.Z (versionCode N)"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main && git push origin vX.Y.Z
# 5) publish the asset (name must equal update.json zipUrl basename)
gh release create vX.Y.Z ancli-vX.Y.Z.zip --title "vX.Y.Z" --notes-file CHANGELOG.md
# 6) confirm the manager-visible manifest is live and points to the new asset
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/update.json
```

Release rules:

- **Never reuse or decrease `versionCode`**: managers compare it to decide there is an update.
- **Never ship a zip whose root is not the module root** (no extra folder around `module.prop`), and keep the module directory name equal to `id` in `module.prop`.
- **Upgrade must not lose user data**: `uninstall.sh` preserves `/data/local/tmp/ancli/rootfs` and `installed.json` (see section 4.5). Re-check that rule before every release that touches install/uninstall logic.
- **Pre-release checks you can actually run here**: `sh -n` on every `src/module/*.sh`, `python -m pytest tests/`, and a manual read of the diff for `/system` writes (must not exist — see section 3 dual-injection).
- **On a real device only** (when `adb devices` shows one, and only with the user's go-ahead): flash the zip, then verify `su -c "ls -la /data/adb/modules/ancli"`, `su -c "ancli --version"` (or the tool's own `--version`), a reboot that keeps the command available, and that a manager uninstall leaves `/data/local/tmp/ancli/rootfs` intact.
