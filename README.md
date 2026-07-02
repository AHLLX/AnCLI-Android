# AnCLI (Android CLI)

AnCLI is a **dual-mode** environment manager for rooted Android devices. It manages CLI tools through two complementary execution backends:

- **PRoot/Ubuntu mode** — Runs Python and Go tools inside an isolated Ubuntu glibc container via PRoot, completely independent of any third-party app.
- **Termux Host mode** — Runs Node.js-based tools natively through the Termux runtime on the Android host, bypassing PRoot's `ptrace` thread-interception limitations that prevent `npm` from working inside a container on Android 15.

## Features

- **Dual-Mode Execution**: Automatically selects the correct backend per tool. Python/Go tools run inside the Ubuntu PRoot container; Node.js tools run via the Termux host runtime.
- **Systemless Module Integration**: Installs as a standard Magisk/KernelSU/APatch module. Wrappers are mounted to `/system/bin` on boot, with instant-access wrappers injected into dynamic root paths (reboot-free).
- **OTA Updates**: Integrates with the root manager's `updateJson` mechanism for automated updates.
- **Boot Service**: Automatically restores DNS configurations and file permissions on every boot.
- **Dynamic Configuration Injection**: Prompts for environment variables (e.g., API keys, custom endpoints) during installation and bakes them securely into execution wrappers.
- **Cloud Registry**: Applications and installation steps are resolved dynamically from a GitHub-hosted registry.
- **Security Hardened**: Command whitelist validation, shell operator blocking, input sanitization, and path traversal guards.

## Supported Applications
*(Fetched dynamically from the cloud registry)*

| App | Runtime | Backend |
| :--- | :--- | :--- |
| **Aider** | Python | PRoot/Ubuntu |
| **MiMo Code** | Python | PRoot/Ubuntu |
| **Antigravity CLI (agy)** | Go (static) | PRoot/Ubuntu |
| **Claude Code** | Node.js (Bun) | Termux Host |
| **OpenCode** | Node.js | Termux Host |

> **Note**: Node.js tools require [Termux](https://termux.dev) to be installed on your device. AnCLI will automatically detect Termux and guide setup if it is absent.

## Installation

### Method A: Flashing via Root Manager (Recommended)

1. Download `ancli-module.zip` from the [Releases](https://github.com/AHLLX/AnCLI-Android/releases) page.
2. Open your **Magisk/KernelSU/APatch Manager** app.
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
ancli --help                   # Show help message
ancli --version                # Show version info
```

### Running Installed Tools
Once a tool is installed, run it directly from any shell:
```bash
aider
claude
opencode
```

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

Remove the AnCLI module from your root manager app. The internal uninstaller automatically cleans up all binaries, rootfs directories, and environment wrappers.

## Technical Details

For the dual-mode execution architecture, dual-injection wrapper mechanism, PRoot configuration, and registry schema, see the [Architecture Document](ARCHITECTURE.md). For technical boundaries and compatibility analysis, see the [Compatibility Dossier](COMPATIBILITY.md).
