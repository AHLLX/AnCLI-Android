# AnCLI (Android CLI)

AnCLI is a unified environment manager and installer designed to run standard Linux command-line (CLI) tools on rooted Android devices. By bootstrapping a minimal Ubuntu Base rootfs via PRoot, it bypasses Android's Bionic C library limitations, allowing you to run Node.js, Python, and Go-based CLI utilities natively and systemlessly.

## Features

- **Systemless Module Integration**: Installs as a standard Magisk/KernelSU/APatch module. Commands are automatically mounted to `/system/bin` on boot, with instant-access symlinks injected into dynamic root paths (reboot-free after app installation).
- **OTA Updates**: Integrates with the root manager's `updateJson` mechanism for automated updates.
- **Boot Service**: Built-in service automatically restores DNS configurations and file permissions on every boot.
- **Dynamic Configuration Injection**: Prompts for required environment variables (e.g., API keys, custom endpoints) during installation and bakes them securely into execution wrappers.
- **Cloud Registry**: Applications and installation steps are resolved dynamically from a GitHub-hosted registry.
- **Security Hardened**: Implements command whitelist validation, shell operator blocking, input sanitization, and path traversal guards.

## Supported Applications
*(Fetched dynamically from the cloud registry)*
- **Aider** (AI pair programming terminal agent)
- **Claude Code** (Anthropic's official terminal agent)
- **OpenCode** (Open-source terminal-based coding agent)
- **MiMo Code** (Terminal agent tailored for Android/Proot)
- **Antigravity CLI (agy)** (Google's terminal agent)

## Installation

### Method A: Flashing via Root Manager (Recommended)

1. Download `ancli-module.zip` from the [Releases](https://github.com/AHLLX/AnCLI-Android/releases) page.
2. Open your **Magisk/KernelSU/APatch Manager** app.
3. Navigate to **Modules** -> **Install from storage** and select the ZIP file.
4. After the bootstrap installation finishes, open any root terminal and run `ancli`.

### Method B: CLI Bootstrap

Run the following command as root in Termux or any terminal emulator:

```bash
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

This script detects your active root manager, downloads the module ZIP, and guides you through the installation.

## Usage

### Interactive Menu
Run the package manager interface:
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
Once a tool is installed, run it directly from any shell without prefixing `ancli`:
```bash
aider
claude
opencode
```

## Directory Structure

| Component | Path | Description |
| :--- | :--- | :--- |
| **Ubuntu Rootfs** | `/data/local/tmp/ancli/rootfs/` | Guest container directory tree |
| **AnCLI Core** | `/data/local/tmp/ancli/bin/ancli-core.py` | Python manager executable |
| **State Database** | `/data/local/tmp/ancli/installed.json` | Installed application metadata |
| **Module Directory** | `/data/adb/modules/ancli/` | Magisk/KSU systemless module files |
| **Dynamic Bin Paths** | `/data/adb/ksu/bin/` or `/data/adb/ap/bin/` | Wrappers for reboot-free access |

## Custom Mirror

To use a specific Ubuntu archive mirror during rootfs bootstrap, export the `ANCLI_MIRROR` variable before installation:

```bash
export ANCLI_MIRROR="archive.ubuntu.com"
```

## Uninstallation

Remove the AnCLI module from your Magisk/KernelSU/APatch Manager app. The internal uninstaller will automatically clean up all associated binaries, rootfs directories, and environment wrappers.

## Technical Details

For information on the dual-injection wrapper mechanism, PRoot configuration, and registry schema, see the [Architecture Document](ARCHITECTURE.md).
