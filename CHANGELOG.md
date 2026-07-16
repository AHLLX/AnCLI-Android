## AnCLI v1.2.2 — Security Hardening & Architecture Refactor

### Security Fixes
**API Key Secure Storage (High)**
- **Root Cause**: API keys (e.g. `ANTHROPIC_API_KEY`) were embedded as plaintext `export` lines inside world-readable (chmod 755) wrapper scripts at `/data/local/tmp/ancli/bin/` and `/data/adb/ksu/bin/`, exposing credentials to any process with filesystem read access.
- **Fix**: Introduced a dedicated `secrets/` directory (`chmod 700`) with per-tool secrets files (`chmod 600`). Wrapper scripts now `source` the secrets file at runtime instead of embedding keys inline. Existing users' keys are automatically migrated on the next `ancli repair` or `ancli config` run.

**Shell Injection Defense for env var Values (Medium)**
- **Root Cause**: User-supplied env var values (API keys) were interpolated into wrapper scripts with single-quote wrapping (`export KEY='value'`). A value containing a single quote would break the shell syntax and could be exploited for injection.
- **Fix**: Replaced manual quoting with Python's `shlex.quote()` for all env var values in both wrapper scripts and secrets files.

**SSL Verification Scoped to Per-Request Context (Medium-High)**
- **Root Cause**: `ssl._create_default_https_context = ssl._create_unverified_context` globally disabled certificate verification for all network calls in the process, removing all MITM protection.
- **Fix**: Removed the global monkey-patch. Registry fetches and installer downloads now create a local `ssl._create_unverified_context()` context passed only to their specific `urlopen()` calls, preserving security for all other SSL operations.

### Architecture Improvements
**Registry-Driven Installer Dispatch**
- Replaced the hardcoded `if app_id == "agy"` / `if app_id == "grok"` branches in `_install_proot()` with a generic `_install_pipe_script()` function driven by three new registry fields: `install_method`, `installer_url`, and `installer_script_env`. New apps requiring script-based installation no longer need Python code changes — only a registry entry update.

**`ancli list` Decoupled from Network**
- `ancli list` previously triggered a full registry fetch from GitHub on every invocation. It now reads only from local disk cache (`_load_local_registry_cache()`), making it instantaneous and offline-safe. Write operations (install/update/config/repair) still fetch the latest cloud registry.

### Other Fixes
- **Fcitx5 Conditional Install**: `customize.sh` now checks the Android system locale (`getprop persist.sys.locale`) and only installs Fcitx5 + Chinese addons (~50 MB) on CJK-locale devices (zh/ja/ko). Non-CJK devices skip these packages entirely.
- **Scoped proot Kill on Uninstall**: `uninstall.sh` now uses `pkill -f "proot.*ancli/rootfs"` instead of `killall proot`, avoiding accidental termination of unrelated proot sessions (e.g. Termux-proot).
- **Version Sync**: `ancli-core.py` `VERSION`, `customize.sh` banner, and `registry.json` version all unified to `v1.2.2`.
- **Secrets Cleanup on Uninstall**: `remove_wrapper()` now also deletes the associated `secrets/{executable}.env` file to prevent stale credential files on disk.

---

## AnCLI v1.2.1 — Physical Keyboard Input Method & Host Environment Sandboxing

### What's New
**Fcitx5 Input Method Support for Physical Keyboards**
- **Bootstrap Package Addition**: Added `fcitx5` and `fcitx5-chinese-addons` to the default bootstrapping package list in `customize.sh` during module installation.
- **Environment Integration**: Configured standard input method variables (`GTK_IM_MODULE=fcitx`, `QT_IM_MODULE=fcitx`, `XMODIFIERS=@im=fcitx`) inside `ancli_env.sh` to solve the issue where users using external physical keyboards (Bluetooth/USB) on Android tablets/phones cannot input Chinese or non-English characters directly in terminal-based TUI tools (like Aider, MiMo).

### Bug Fixes
**Host Environment Variable Isolation for Container Binaries**
- **Root Cause**: Containerized binaries (like Go-based `agy`) inherit Termux environment variables (like `PREFIX` and `TERMUX_VERSION`) from the host shell. Go's runtime or execution hooks detect these and incorrectly attempt to run host-side Termux paths (like `/data/data/com.termux/files/usr/bin/bash`), which do not exist inside the isolated container.
- **Fix**: Added explicit `unset` commands in `ancli_env.sh` to scrub all Termux-specific variables before entering the container, ensuring containerized binaries run in a clean, standard Linux environment and look up the container's own standard `/bin/bash` in `$PATH`.

---

## AnCLI v1.1.1 — Storage Bind Fix

### Bug Fixes
**MiMo/Aider `chdir` Failure on Internal Storage**
- **Root Cause**: When executing from `/storage/emulated/0/...` (which `/sdcard` symlinks to), `proot` failed to locate the directory inside the Ubuntu rootfs because only `/sdcard` was explicitly bound.
- **Fix**: The PRoot wrapper generator now explicitly binds `-b /storage` alongside `-b /sdcard`, ensuring all nested and symlinked storage paths resolve correctly within the guest container without triggering `proot warning: can't chdir`.

**Node.js / Bun TTY Input Hangs (Black Screen/Garbage Chars)**
- **Root Cause**: Node.js and Bun use modern `io_uring` polling by default, which PRoot's syscall interception mechanism on Android does not support properly. This caused interactive TUIs (like MiMo Code and Claude Code) to freeze their event loop and fail to read standard input, echoing raw ANSI codes like `^[[3~`.
- **Fix**: Injected `export UV_USE_IO_URING=0` and `export BUN_FEATURE_FLAG_IO_URING=0` into the execution wrappers, forcing standard `epoll` fallback and completely restoring flawless keyboard interactivity for Node-based tools.

**Core Engine Updates Failing to Propagate to Existing Apps**
- **Root Cause**: The `ancli repair` command previously skipped wrapper regeneration if a wrapper file already existed, preventing core engine bug fixes (like the storage and `io_uring` fixes above) from applying to already-installed applications.
- **Fix**: `ancli repair` now unconditionally regenerates all wrappers for installed apps, guaranteeing they always run on the latest AnCLI engine logic without requiring a reinstall.

---

## AnCLI v1.1.0 — Global Instant Access & UX Polish
### What's New
**True "No Reboot" Global Execution for KernelSU**
- **Root Cause**: KernelSU's SELinux policy enforces a strict prohibition on creating new files within `/data/adb/ksu/bin` after boot, which previously blocked `ancli install` from establishing instant-access wrappers.
- **Pre-seeded Placeholders**: The module installer (`customize.sh`) now pre-creates executable placeholders for all registry apps during the flash stage (when SELinux is permissive). These placeholders transparently route commands to the actual PRoot wrappers, achieving true instant global access without a reboot.
- **Boot-time Sync**: `service.sh` now automatically syncs and overwrites placeholders with real PRoot wrappers upon every boot, cementing long-term stability.
- **Clarified Logs**: Replaced the misleading "Requires Reboot" warning during installation with accurate status reporting.

**Revamped Uninstallation & App Management UX**
- **Expanded App Store Menu**: The main `ancli` TUI now features a dedicated, highly visible `[u] Uninstall an app` option to prevent feature-discovery issues.
- **Comprehensive Purge Options**: The uninstallation submenu now provides structured choices:
  1. Remove a specific app.
  2. Batch-remove all installed apps (while keeping the Ubuntu container safe).
  3. Safe-destruct guide: Provides explicit terminal commands (`rm -rf`) for users who wish to entirely obliterate the AnCLI framework from their storage.
- **Clarified Manager Descriptions**: Updated `module.prop` to explicitly reassure users that uninstalls triggered from the Magisk/KernelSU Manager are **Soft Uninstalls** (data and configs are safely preserved).

---

## AnCLI v1.0.2 — Auth Credential Fix & Code Hardening
### Bug Fixes

**agy Stops Working After Terminal Restart (Root Cause Fixed)**
- **Root Cause**: On first launch, `agy` writes OAuth tokens as root into `/root/.config/`, `/root/.gemini/`, etc. inside the PRoot container. When the terminal app is killed and restarted, subsequent runs execute as the `shell` user (UID 2000), which cannot read root-owned credential files, causing `agy` to behave as if never logged in.
- **Fix 1 — Wrapper on every launch**: The generated wrapper script now calls `chown -R 2000:2000 + chmod -R 755` on all credential dirs (`/root/.config`, `/root/.gemini`, `/root/.claude`, `/root/.local`) before exec-ing proot, so permissions are always correct regardless of who ran the previous session.
- **Fix 2 — service.sh on every boot**: `service.sh` now also resets those directory permissions on every boot, covering the case where the phone was rebooted between sessions.

**Duplicate `/sdcard` Bind Mount**
- When `$PWD` is inside `/sdcard`, the wrapper previously bound `/sdcard` twice (once explicitly and once via `-b "$PWD"`), causing undefined proot behavior. Now uses a shell `case` guard to skip the `$PWD` bind when it is already under `/sdcard`.

**Code Cleanup & Hardening**
- Removed duplicate comment block in `generate_proot_wrapper`.
- Removed redundant `GODEBUG=netdns=go` double-injection (was set via both `export` and env-var argument to proot).
- `save_installed`: switched `chown shell:shell` to numeric `chown 2000:2000` for reliability inside proot where the `shell` username may not exist in `/etc/passwd`.
- `repair_env`: added ROOTFS path sanity guard before any recursive `chmod`/`chown` to prevent accidental host filesystem modification if path is empty.
- `repair_env`: wrapper recreation check now mirrors `_write_wrapper_to_paths` logic (checks `os.path.isdir` on parent), preventing false-positive repairs for absent KSU/AP paths.
- `remove_wrapper`: now also removes the `ANCLI_DIR/bin/<executable>` copy.
- Wrappers now also written to `ANCLI_DIR/bin/` enabling direct `sh /data/local/tmp/ancli/bin/<cmd>` invocation that bypasses `noexec` without requiring module paths.

---

## AnCLI v1.0.1 — Data Preservation & Uninstall Protection

### What's New

**Container & Python Packages Preservation during Uninstall**
- **Dynamic TTY Detection**: Added automatic interactive terminal detection to `uninstall.sh`.
- **Non-Interactive Mode Protection (Magisk/KSU tap)**: When uninstalling the module from KernelSU/Magisk/APatch managers, the script now **safely preserves** the Ubuntu container, precompiled binaries, downloaded Python packages (like `gitpython`), and configurations (`installed.json`) by default.
- **Interactive Mode Flexibility**: When run manually in terminal, the uninstaller presents a menu option, giving the user a choice between a soft uninstall (keeping container data) or a full database & filesystem purge.
- **Forced Purge override**: Users can force a complete silent purge in manager uninstall by creating a file flag at `/data/local/tmp/ancli_force_purge`.

**Dynamic Host CWD Propagation**
- Expose current working directory ($PWD) directly to PRoot wrappers, solving startup hangs in Node.js-based AI agents like **mimo** when run from container's chroot system root directory.

---

## AnCLI v1.0.0 — Unified CLI Environment Manager (Initial Release)

### What's New

**Dual-Injection Systemless Architecture**
- Dynamic, reboot-persistent injection: Writes binary wrappers to both **Path A** (`/data/adb/modules/ancli/system/bin/` for post-reboot overlays) and **Path B** (`/data/adb/ksu/bin/` or `/data/adb/ap/bin/` for instant execution without rebooting).
- Implemented **File Ownership Override** logic: Automatically purges and overwrites conflicting wrapper paths and temporary locks (`installed.json.tmp`) to bypass Android's root ownership bugs during package upgrades.

**NPM-Free Standalone Binaries**
- Node.js-based terminal agents (Claude Code, OpenCode) and MiMo Code are fetched directly as precompiled native Linux-arm64 binaries.
- Completely bypasses standard `npm`/`nodejs` installations, eliminating Node.js multi-threading `libuv` / `ptrace` filesystem worker threads conflicts (`ENOENT` / `EACCES`) on Android 15.

**Container Virtualization Optimizations**
- **Eliminated Nested PRoot Conflict**: Replaced nested PRoot wrapper encapsulation inside the guest container with native subprocess execution.
- **Python Installer Bypass**: Provides a native Python urllib downloader pipeline to download installers directly, bypassing CMD/PowerShell ADB character escaping bugs (`|`, `--`).
- **HTTP Proxy Propagation**: Dynamically forwards host proxy variables (`http_proxy`/`https_proxy`) into the PRoot guest container for seamless packages downloads under local VPN or PC proxy environments.

**Security Hardening**
- Strictly enforces safety validation using an expanded `ALLOWED_CMD_PREFIXES` whitelist (`pip`, `npm`, `apt-get`, `apt`, `curl`, `rm`, `agy`, `bash`, `sh`).
- Restricts input character validation on dynamic environment variables to prevent shell command injection.
