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
