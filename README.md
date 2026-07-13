# AnCLI

AnCLI is a unified, systemless environment manager and plugin-based installer for rooted Android devices. It enables seamless execution of standard GNU/Linux glibc command-line tools (such as Python AI pair programmers, Go binaries, and Node.js terminal agents) directly inside a lightweight containerized Ubuntu sandbox.

## Features

- **No Node.js or NPM Required**: Node.js-based tools (like Claude Code, OpenCode, and MiMo Code) are installed as standalone, precompiled Linux-arm64 binaries. No npm or complex JS compilation required.
- **Systemless Module Integration**: Installs as a standard Magisk/KernelSU/APatch module. Wrappers are mounted to `/system/bin` on boot, with instant-access wrappers injected into dynamic root paths (reboot-free).
- **OTA Updates**: Integrates with the root manager's `updateJson` mechanism for automated updates.
- **Boot Service**: Automatically restores DNS configurations and file permissions on every boot.
- **Dynamic Configuration Injection**: Prompts for environment variables (e.g., API keys, custom endpoints) during installation and bakes them securely into execution wrappers.
- **Cloud Registry**: Applications and installation steps are resolved dynamically from a GitHub-hosted registry.
- **Escaping & Proxy 直通**: Bypasses ADB character escaping bugs via Python urllib direct downloads, and dynamically forwards host proxy settings into the guest container.
- **PRoot Syscall Stabilization**: Automatically mitigates Android kernel `io_uring` and `epoll` translation bugs, ensuring modern Node.js and Bun interactive TUIs (like MiMo and Claude Code) can process raw keyboard input flawlessly without event loop blocking.
- **Physical Keyboard Input Method Support**: Pre-installs `fcitx5` and `fcitx5-chinese-addons` during the container bootstrap phase, and automatically exports standard input variables (`GTK_IM_MODULE=fcitx`, `QT_IM_MODULE=fcitx`, `XMODIFIERS=@im=fcitx`) to solve the issue where users using external physical keyboards (Bluetooth/USB) on Android tablets/phones cannot input Chinese or non-English characters directly in terminal-based TUI tools (like Aider, MiMo).
- **Security Hardened**: Command whitelist validation, shell operator blocking, input sanitization, and path traversal guards.

## Supported Applications
*(Fetched dynamically from the cloud registry)*

| App | Runtime | Installation Method |
| :--- | :--- | :--- |
| **Aider** | Python | pip package |
| **MiMo Code** | Node.js/JS | Precompiled Release binary |
| **Antigravity CLI (agy)** | Go (static) | Standalone release binary |
| **Claude Code** | Node.js/JS | Precompiled Release binary (NPM-free) |
| **OpenCode** | Node.js/JS | Precompiled Release binary (NPM-free) |

## Installation

### Method A: Flashing via Root Manager (Recommended)

1. Download `ancli-module.zip` from the [Releases](https://github.com/AHLLX/AnCLI-Android/releases) page.
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

## Technical Details

For the execution architecture, dual-injection wrapper mechanism, PRoot configuration, and registry schema, see the [Architecture Document](ARCHITECTURE.md). For technical boundaries and compatibility analysis, see the [Compatibility Dossier](COMPATIBILITY.md).
