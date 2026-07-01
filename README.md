# AnCLI (Android CLI) 🚀

> Your reliable "Uncle" for effortlessly running any desktop-class CLI/TUI tool natively on Android.

[中文文档](README.zh.md) | [Technical Architecture](ARCHITECTURE.md)

AnCLI is a unified environment manager and plugin-based installer that brings full-blooded Linux command-line tools to Android root environments. Packaged as a standard **Magisk/KernelSU/APatch module**, it bootstraps a pristine Ubuntu Base Rootfs via `proot`, bypassing Android's Bionic C library limitations to let you run Node.js, Python, and Go-based CLI tools seamlessly.

## 🎯 Key Features

- **Native Module Integration (Magisk/KernelSU/APatch)**: Installs as a standard systemless module via your root Manager app. Auto-injects `ancli` into `/system/bin` on boot, plus instant access via KSU/AP dynamic bin paths — **no reboot required after app installation**.
- **OTA Auto-Update**: The module supports `updateJson`, so your root Manager automatically checks for new AnCLI versions and lets you upgrade with one tap.
- **Boot Self-Repair**: Built-in `service.sh` automatically fixes DNS config and file permissions on every boot — no manual maintenance needed.
- **Python-Powered App Store**: The core manager is built in pure Python (stdlib only), providing colorful interactive menus, robust plugin configuration parsing, and secure environment variable injection.
- **Cloud Plugin Registry**: AnCLI doesn't hardcode apps. It fetches its supported application registry from a GitHub-hosted JSON file with 3-retry fallback. Anyone can submit a PR to add a new CLI app!
- **Zero-Prefix Execution**: Installed a tool? Just type `aider` or `claude`. No `ancli run aider` required.
- **Security Hardened**: Command whitelist validation, shell operator blocking, path traversal protection, and `shlex.quote()` escaping for all injected environment variables.
- **The "Fat Base" Approach**: By leveraging the official `ubuntu-base` image and `apt-get`, all installed CLI apps share the same robust Glibc, Python, Node.js, and Git base.

## 📦 Supported Applications
*(Pulled dynamically from the Cloud Registry)*
- [x] **Aider** (AI pair programming in your terminal)
- [x] **Claude Code** (Anthropic's official terminal agent)
- [x] **Antigravity CLI (agy)** (Google's powerful terminal agent)
- [x] **OpenCode** (Open-source AI coding agent)
- [x] **MiMo Code** (Terminal agent tailored for Android)
- [ ] *Your plugin here!*

## 🚀 Installation

### Method A — Flash via Manager (Recommended)

1. Download `ancli-v1.1.0.zip` from [Releases](https://github.com/AHLLX/AnCLI-Android/releases)
2. Open your **Magisk/KernelSU/APatch Manager** app
3. Go to **Modules → Install from storage** → select the ZIP
4. Wait for bootstrap to complete (downloads PRoot + Ubuntu rootfs + APT dependencies)
5. Done! Type `ancli` in any root shell

### Method B — Quick Bootstrap (CLI)

```bash
# Executed as root (su) in Termux or any terminal emulator
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

This downloads the module ZIP and guides you through flashing it via your root manager.

## 💻 Usage

### Interactive Mode
```bash
ancli
```
Opens the App Store menu. Select a number to install, update, uninstall, or reconfigure any app.

### CLI Mode
```bash
ancli install aider          # Install an app
ancli uninstall claude-code  # Uninstall an app
ancli update aider           # Update an installed app
ancli config aider           # Reconfigure API keys / env vars
ancli list                   # List all installed apps
ancli --help                 # Show help
ancli --version              # Show version
```

### After Installing an App
```bash
# Just type the tool name directly — zero prefix!
aider
claude
opencode
```

## 🗑️ Uninstallation

Simply **remove the AnCLI module** from your Magisk/KernelSU/APatch Manager. The built-in `uninstall.sh` will automatically clean up all rootfs files, wrappers, and dynamic bin links.

## 📂 Directory Structure & File Locations

| Component | Physical Path | Description |
| :--- | :--- | :--- |
| **Ubuntu Rootfs** | `/data/local/tmp/ancli/rootfs/` | The core Proot container (full Ubuntu filesystem) |
| **AnCLI Core** | `/data/local/tmp/ancli/bin/ancli-core.py` | The Python package manager brain |
| **Installed Apps DB** | `/data/local/tmp/ancli/installed.json` | Tracks installed apps with metadata |
| **Systemless Module** | `/data/adb/modules/ancli/` | Standard module path (auto-mounts `system/bin/ancli`) |
| **KSU/AP Wrappers** | `/data/adb/ksu/bin/` or `/data/adb/ap/bin/` | Instant-access shortcuts (no reboot needed) |
| **NPM Packages** | `.../rootfs/usr/local/lib/node_modules/` | Node.js tools inside Proot |
| **Pip Packages** | `.../rootfs/usr/local/lib/python3.12/dist-packages/` | Python tools inside Proot |

## 🌐 Custom Mirror

For users outside China, override the default USTC mirror:

```bash
# Set before flashing, or export in your shell
export ANCLI_MIRROR="archive.ubuntu.com"
```

## 📚 Documentation
For a deep dive into the Proot environment, the dual-injection wrapper mechanism, the module lifecycle, and the Cloud Registry schema, see the [Architecture Document](ARCHITECTURE.md).
