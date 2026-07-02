# AnCLI Compatibility Dossier (v1.2.2)

This document records the architectural boundaries, compatibility tests, and validation results of **AnCLI** on Android 15.

---

## 1. Core Technical Limitations on Android 15

### 1.1 Why NPM / Node.js fails inside PRoot

Node.js uses **libuv** for asynchronous filesystem operations, which runs a pool of worker threads. On Android 15 kernels, `ptrace` path interception (used by PRoot) does not correctly trace filesystem calls originating from new, non-main worker threads.

As a result, a call like `mkdir('/root')` issued from a worker thread resolves to `/root` on the **host** filesystem instead of the PRoot guest filesystem, triggering `ENOENT` (no such file or directory) or `EACCES` (permission denied).

### 1.2 Why Termux Host execution fails

Attempting to run Node.js tools natively in Termux by invoking Termux-side binaries (like `/data/data/com.termux/files/usr/bin/node`) from the root shell wrapper fails due to **SELinux policies**.

Android enforces strict domain boundaries. Programs labeled with root context cannot execute binaries labeled with Termux's app domain (`app_data_file`) with the execution path crossing app boundary lines, throwing permission denials even when the runner is root.

---

## 2. The Solution: Precompiled Standalone Binaries

Instead of installing packages from `npm` registries inside the container, AnCLI downloads **precompiled Linux-arm64 native binaries** directly from GitHub Releases.

| Platform / Tool | Source | Output | Type |
| :--- | :--- | :--- | :--- |
| **Claude Code** | `anthropics/claude-code` | `claude-linux-arm64.tar.gz` | ELF binary |
| **OpenCode** | `anomalyco/opencode` | `opencode-linux-arm64.tar.gz` | ELF binary |
| **MiMo Code** | `XiaomiMiMo/MiMo-Code` | `mimocode-linux-arm64.tar.gz` | ELF binary |

These binaries are statically compiled or bundled with their own minimal Node.js/JS runtimes. Since they run inside the PRoot glibc sandbox and perform standard synchronous ELF execution rather than asynchronous npm building scripts, they run natively on Android 15.

---

## 3. Compatibility Matrix

| Application | Stack | Status | Execution Backend | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Aider** | Python | ✅ **Supported** | PRoot / Ubuntu Base | Runs via `pip` |
| **MiMo Code** | Node.js | ✅ **Supported** | PRoot / Ubuntu Base | Native Release binary |
| **Antigravity CLI (agy)** | Go | ✅ **Supported** | PRoot / Ubuntu Base | Native Release binary |
| **Claude Code** | Node.js | ✅ **Supported** | PRoot / Ubuntu Base | Native Release binary |
| **OpenCode** | Node.js | ✅ **Supported** | PRoot / Ubuntu Base | Native Release binary |
