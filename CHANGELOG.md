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

## AnCLI v1.0.0 — Unified CLI Environment Manager for Android (Initial Release)

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
