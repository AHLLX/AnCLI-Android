# AnCLI

AnCLI is a unified, systemless environment manager and plugin-based installer for rooted Android devices. It enables seamless execution of standard GNU/Linux glibc command-line tools (such as Python AI pair programmers, Go binaries, and Node.js terminal agents) directly inside a lightweight containerized Ubuntu sandbox.

## Features

- **No Node.js or NPM Required**: Node.js-based tools (like Claude Code, OpenCode, and MiMo Code) are installed as standalone, precompiled Linux-arm64 binaries. No npm or complex JS compilation required.
- **Systemless Module Integration**: Installs as a standard Magisk/KernelSU/APatch module. Wrappers are mounted to `/system/bin` on boot, with instant-access wrappers injected into dynamic root paths (reboot-free).
- **OTA Updates**: Integrates with the root manager's `updateJson` mechanism for automated updates.
- **Boot Service**: Automatically restores DNS configurations and file permissions on every boot.
- **Dynamic Configuration Injection**: Configure API keys and custom endpoints anytime via `ancli config <app_id>`. Credentials are stored in per-tool secrets files (mode 0600) and sourced by the wrapper at runtime — never embedded in world-readable wrapper scripts.
- **Cloud Registry**: Applications and installation steps are resolved dynamically from a GitHub-hosted registry.
- **Escaping & Proxy Passthrough**: Bypasses ADB character escaping bugs via Python urllib direct downloads, and dynamically forwards host proxy settings into the guest container.
- **PRoot Syscall Stabilization**: Automatically mitigates Android kernel `io_uring` and `epoll` translation bugs, ensuring modern Node.js and Bun interactive TUIs (like MiMo and Claude Code) can process raw keyboard input flawlessly without event loop blocking.
- **Physical Keyboard Input Method Support**: Pre-installs `fcitx5` and `fcitx5-chinese-addons` during the container bootstrap phase, and automatically exports standard input variables (`GTK_IM_MODULE=fcitx`, `QT_IM_MODULE=fcitx`, `XMODIFIERS=@im=fcitx`) to solve the issue where users using external physical keyboards (Bluetooth/USB) on Android tablets/phones cannot input Chinese or non-English characters directly in terminal-based TUI tools (like Aider, MiMo).
- **Cross-Sandbox Browser Redirect (OAuth login)**: Solves the headless virtualization restriction where terminal agents (like `grok login`, `agy auth login`) fail to open a browser for device flow authentication. We map host-side wrappers and translate Golang's `statx` pathing system calls, automatically launching your host Android's default web browser when tools try to open URLs inside the guest container.
- **Multi-language Support (zh/en)**: Features an interactive language hot-toggle directly in the `ancli` menu, saving your locale preferences persistently.
- **Security Hardened**: Command whitelist validation, shell operator blocking, input sanitization, and path traversal guards.

## Supported Applications
*(Fetched dynamically from the cloud registry)*

| App | Runtime | Installation Method |
| :--- | :--- | :--- |
| **Aider** | Python | pip package |
| **MiMo Code** | Node.js/JS | Precompiled Release binary |
| **Antigravity CLI (agy)** | Go | Standalone release binary (proot mode — current release is dynamically linked) |
| **Claude Code** | Node.js/JS | Precompiled Release binary (NPM-free) |
| **OpenCode** | Node.js/JS | Precompiled Release binary (NPM-free) |
| **Grok** | Rust | Standalone release binary (proot mode) |

## Installation

### Method A: Flashing via Root Manager (Recommended)

1. Download the module ZIP (`ancli-v1.2.3.zip`) from the [Releases](https://github.com/AHLLX/AnCLI-Android/releases) page.
2. Open your Magisk, KernelSU, or APatch Manager app.
3. Navigate to **Modules** → **Install from storage** and select the ZIP file.
4. After bootstrap finishes, open any root terminal and run `ancli`.

### Method B: CLI Bootstrap

Run the following command as root in Termux or any terminal emulator:

```bash
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

This script detects your active root manager, downloads the module ZIP, and guides you through the installation.

## Usage

### Interactive Menu
```bash
ancli
```

### CLI Mode
```bash
ancli install <app_id>         # Install an application
ancli uninstall <app_id>       # Uninstall an application
ancli update <app_id>          # Update an installed application
ancli config <app_id>          # Reconfigure environment variables
ancli list                     # List installed applications
ancli repair                   # Detect and repair environment issues
ancli --help                   # Show help message
ancli --version                # Show version info
```

### Running Installed Tools
Once a tool is installed, run it directly from any shell:
```bash
aider
claude
opencode
mimo
agy
```

## Network Proxy & VPN Handling

On Android, VPN applications (like Clash, v2rayNG running in TUN mode) typically bypass Root (UID 0) traffic to prevent routing loops. As a result, network requests from tools like `agy` or `aider` running in a root shell will pierce through the VPN and connect directly, potentially being blocked by firewalls.

**AnCLI provides a seamless solution:**
1. **Auto Proxy Detection**: Every time you launch a shortcut (e.g., typing `agy`), the wrapper automatically polls Android's `dumpsys connectivity` state. If your VPN is configured with a global or system HTTP proxy, the wrapper will extract the local IP and port and inject it into the guest container, achieving **zero-config transparent proxying**.
2. **Hardcoded Proxy Override (Pure VPN Mode)**: If your VPN app does not support system proxy injection, simply run `ancli config <app_id>` (e.g., `ancli config agy`) in your terminal. You can directly input your local proxy address (like `http://127.0.0.1:7890`) into the `HTTP_PROXY` and `ALL_PROXY` fields. This will permanently bake the proxy route into that tool's execution flow.

## Directory Structure

| Component | Path | Description |
| :--- | :--- | :--- |
| **Ubuntu Rootfs** | `/data/local/tmp/ancli/rootfs/` | PRoot guest container |
| **AnCLI Core** | `/data/local/tmp/ancli/bin/ancli-core.py` | Python package manager |
| **State Database** | `/data/local/tmp/ancli/installed.json` | Installed app metadata |
| **Module Directory** | `/data/adb/modules/ancli/` | Systemless module files |
| **Dynamic Bin Paths** | `/data/adb/ksu/bin/` or `/data/adb/ap/bin/` | Reboot-free wrapper paths |

## Custom Mirror

To use a specific Ubuntu archive mirror during rootfs bootstrap:

```bash
export ANCLI_MIRROR="archive.ubuntu.com"
```

## Uninstallation

- **Soft Uninstall (Safe & Default)**: Removing the module from your KernelSU/Magisk/APatch manager only cleans up the module's hooks. It **safely preserves** your entire Ubuntu container, downloaded Python packages, and API configurations so you can upgrade easily.
- **Full Purge (Complete Removal)**: If you wish to permanently destroy all data, containers, and configurations, use the `ancli` menu and press `u`, then select `[3] Completely uninstall AnCLI`. Alternatively, run this in a root terminal:
  ```bash
  rm -rf /data/local/tmp/ancli
  ```
  After wiping the directory, uninstall the module from your manager.

## Native (No-PROOT) Mode

Native mode is registry-driven (`"native": true`) and still supported, but **no registry app currently uses it**: the current agy release (1.1.11) is **dynamically linked** (188MB, needs glibc) — direct exec on the Android host fails with `No such file or directory` (missing `/lib64/ld-linux-aarch64.so.1`). agy and grok therefore run in proot mode again; their wrappers execute inside the container where glibc exists.

- Only **statically-linked** binaries qualify for native mode (instant startup, no proot translation overhead); the mechanism is kept for future static tools.
- In proot mode everything else (config keys, proxy detection, browser redirect for OAuth, secrets) works unchanged.

## Troubleshooting

### TUI tools (mimo / claude / opencode / agy) don't start from `/`

All Node/Bun-based TUIs (mimo, opencode, claude) scan the current directory's file tree at startup. From `/` inside the container, that scan covers the entire rootfs plus the `/data` and `/sdcard` bindings — hundreds of thousands of files — which hangs or silently aborts the TUI. Python (aider) and Go/Rust tools are affected to a lesser degree.

The wrappers handle this automatically: launching from `/` (or any path not visible inside the container, e.g. `/cache`) now redirects the working directory to the container HOME (`/root`), so the TUI starts fast. You will see a hint like:

```
[AnCLI] Launched from /; using /root (container HOME) as working directory.
```

If you want to work on a project instead, `cd` into it first:

```bash
cd /sdcard/MyProject && mimo
```

### Tools take ~10s to start from `/sdcard`

`/sdcard` is Android's FUSE (emulated storage): every file operation costs 50–200 ms (proot translation + FUSE round trip). Startup scans that take 100 ms on a normal disk take ~10 s there. For fast startups, keep the project **inside the container rootfs** instead:

```bash
cd /root/projects && mimo   # instant startup, native ext4 speed
```

The wrapper also avoids repeating slow setup on every launch: the system proxy is detected via `dumpsys connectivity` and cached for 30 seconds (see `ancli_env.sh`), and Clash virtual-IP loopback binds are only re-applied when missing.

### `bash -c 'test -x <file>'` 在容器内总是失败（faccessat2）

已知问题：官方 proot（5.4.0）在 aarch64 上未映射 `faccessat2` syscall（glibc 2.39 的 bash `[ -x ]` 依赖它），导致 **bash 的权限检测误报 false**，而 `sh`（dash/toybox 走老 `access()`）正常。影响 jadx 等 Java 工具启动脚本里的 `JAVA_HOME`/`-x` 检测。

**绕法**（mimo 已验证）：
- 用 sh 写启动包装：`#!/bin/sh` + `exec java -cp /path/jadx-all.jar jadx.cli.JadxCLI "$@"`
- 或先 `chmod +x` 后用 `sh -c` 调用
- termux 的 proot 构建含修复但依赖 libtalloc/liblandroid-shmem（纯 Android 无），已放弃该路线

### No update notification in KernelSU/Magisk manager

The manager polls `update.json` (`updateJson` in `module.prop`). If `raw.githubusercontent.com` is unreachable on your network, no update will appear — use a proxy/VPN, or point `updateJson` at a jsDelivr mirror. Also verify the `zipUrl` asset name exactly matches what you uploaded to the GitHub release — a mismatch silently disables OTA.

### TLS & package-source trade-offs (known)

- Registry fetches and installer-script downloads verify TLS certificates first, and fall back to an **unverified** retry only when certificate validation fails (the proot container may ship an incomplete CA store). Downloads of third-party installer scripts then execute them with `bash` — review what you install.
- `customize.sh` installs APT packages with `--allow-unauthenticated` (the rootfs ships with a minimal keyring) and downloads `ubuntu-base` without a checksum. Both are pragmatic trade-offs for a bootstrap that must work offline-first; do not extend this pattern to user-supplied inputs.

## Technical Details

For the execution architecture, dual-injection wrapper mechanism, PRoot configuration, and registry schema, see the [Architecture Document](ARCHITECTURE.md). For technical boundaries and compatibility analysis, see the [Compatibility Dossier](COMPATIBILITY.md).
