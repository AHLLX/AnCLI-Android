# AI Agent Guidelines (AGENTS.md)

Welcome, fellow AI Assistant. If you are reading this, you have been tasked with modifying or maintaining the **AnCLI (Android CLI)** project. This document contains critical context, constraints, and architectural details to help you immediately understand the codebase and avoid common Android-specific pitfalls.

## 1. Project Context & Purpose
AnCLI is a unified environment manager and plugin-based installer designed to bring full Linux command-line tools (like Node.js, Go, or Python-based AI agents) to rooted Android devices.
Because Android uses the Bionic C library, native Linux binaries often crash. AnCLI solves this by using `proot` to run a pure Ubuntu `glibc` base system transparently on Android.

## 2. Core Architecture
You must understand these three pillars before writing any code:

1. **`src/install.sh` (The Bootstrap Installer)**: 
   - A shell script executed as root (`su`).
   - Downloads a minimal `ubuntu-base` tarball and extracts it to `/data/local/tmp/ancli/rootfs`.
   - Uses `proot` to run `apt-get` and install `python3`, `nodejs`, `npm`, and `git`.
2. **`src/ancli-core.py` (The Package Manager Brain)**: 
   - Runs *inside* the Proot Ubuntu container.
   - Fetches `registry.json` from the cloud.
   - Generates Bash wrappers and injects them into the Android host.
   - **CONSTRAINT**: Must rely strictly on the Python Standard Library (e.g., use `urllib.request`, NOT `requests`). It cannot assume pip packages are pre-installed.
3. **`src/registry.json` (The Plugin Schema)**:
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
5. **Do NOT force delete container data during Manager uninstall (CRITICAL)**:
   - Magisk/KSU Manager uninstallations trigger `uninstall.sh` in non-interactive background. 
   - You MUST detect TTY redirection and preserve the `/data/local/tmp/ancli/rootfs` and `installed.json` by default (only wipe `bin/` scripts), unless explicitly forced by `/data/local/tmp/ancli_force_purge`. This ensures Python dependencies (like `gitpython`) and API keys are not lost during module upgrades.

## 5. Physical Layout Mapping
When debugging paths or writing cleanup logic, refer to this exact physical mapping (Host Android perspective):

- **Ubuntu Rootfs**: `/data/local/tmp/ancli/rootfs/`
- **NPM Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/node_modules/`
- **Pip Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/python3.12/dist-packages/`
- **Core Script**: `/data/local/tmp/ancli/bin/ancli-core.py`
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
  "executable": "command_name_to_bind"
}
```
*Note: If `env_vars` is provided, `ancli-core.py` will automatically prompt the user for them and inject them into the wrapper via `export KEY="VALUE"`.*

## 7. Testing Constraints
- The user **does not** currently have a local Android emulator attached. Do not attempt to use `adb shell` or run `install.sh` to verify your code. Rely on static analysis, linting, and your fundamental understanding of shell/Python scripts.
