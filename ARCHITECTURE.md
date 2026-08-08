# AnCLI Technical Architecture & Deep Dive

This document outlines the internal design, lifecycle, security model, and execution flows of **AnCLI v1.2.2**.

---

## 1. Unified Container Architecture

AnCLI uses a single, unified execution backend based on **PRoot** to run Linux command-line tools.

```
Host Shell
    → Wrapper (/data/adb/ksu/bin/<tool>)
        → proot v5.4.0 (official static build, user-space chroot)
            → Ubuntu 24.04 glibc rootfs (/data/local/tmp/ancli/rootfs/)
                → Standalone Python/Go/JS Native Binary
```

### 1.1 Why PRoot / Ubuntu Base?

Android's Linux kernel is paired with the Bionic C library instead of the standard GNU glibc. This creates compatibility issues for standard Linux tools built for glibc. 

- `customize.sh` downloads the official `ubuntu-base-arm64` tarball (~30 MB) and extracts it to `/data/local/tmp/ancli/rootfs/`.
- PRoot bind-mounts `/dev`, `/proc`, `/sys`, `/sdcard`, and `/data/local/tmp/ancli` so the container has access to host resources.
- Glibc binaries run inside this isolated sandbox natively.

### 1.2 Resolving Node.js Incompatibility via Standalone Binaries

In older configurations, running Node.js (`npm`) inside PRoot on Android 15 failed due to a `libuv` thread-interception ptrace bug (asynchronous `mkdir` returned `ENOENT` during npm initialization).

Instead of running npm or trying to bind to a Termux host Node.js runtime (which fails due to SELinux blocking `exec` calls across application domains), **AnCLI installs Node.js/JS-based tools directly as precompiled native Linux binaries**.

1. Tools like **Claude Code**, **OpenCode**, and **MiMo Code** publish native standalone executables to their GitHub Releases.
2. AnCLI downloads these standalone `.tar.gz` packages directly inside the container using `curl` or native Python bypass downloads and extracts the executable to `/usr/local/bin`.
3. The generated wrapper calls this native binary directly via PRoot, completely bypassing `npm` and Node.js compilation.

### 1.3 Eliminating Nested PRoot Conflicts

Older wrapper models executed sub-installers inside another nested `proot` context, which caused ptrace collisions on modern Linux kernels and led to shell execution freezes (e.g. `/system/bin/sh: bash: inaccessible or not found`).

AnCLI completely decouples inner script runners. All container commands are called directly inside the primary PRoot context using the guest container's `/bin/bash` with `set -o pipefail`.

---

## 2. Module Lifecycle

AnCLI is packaged as a standard Magisk/KernelSU/APatch module.

| Hook | When | Description |
| :--- | :--- | :--- |
| **`customize.sh`** | Flashed in manager | Bootstraps PRoot and Ubuntu rootfs, installs APT dependencies. |
| **`service.sh`** | Late start boot hook | Restores container DNS (`resolv.conf`) and permissions. |
| **`uninstall.sh`** | Module uninstalled | Cleans rootfs directories, kills PRoot processes, removes dynamic wrappers. |

---

## 3. Dynamic Registry System

The registry schema defines the metadata and installation scripts for supported tools.

```json
"opencode": {
  "name": "OpenCode",
  "description": "Open-source terminal-based AI coding agent",
  "install_mode": "proot",
  "install_cmd": "curl -L https://github.com/anomalyco/opencode/releases/latest/download/opencode-linux-arm64.tar.gz -o /tmp/opencode.tar.gz && tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin && rm /tmp/opencode.tar.gz",
  "update_cmd": "curl -L ...",
  "uninstall_cmd": "rm -f /usr/local/bin/opencode",
  "executable": "opencode"
}
```

---

## 4. The "Dual-Injection" Systemless Wrapper Trick

To expose a newly installed tool globally to the user's `$PATH` without modifying the read-only `/system` partition, AnCLI writes wrappers to two locations:

1. **`/data/adb/modules/ancli/system/bin/<tool>`** — Active after device reboot (Magisk/KSU module overlay).
2. **`/data/adb/ksu/bin/<tool>` (or `/data/adb/ap/bin/<tool>`)** — Active immediately without reboot.

### Wrapper Template

```sh
#!/system/bin/sh
# AnCLI wrapper for: <tool>

# 1. Load centralized proxy & environment variables
. /data/local/tmp/ancli/bin/ancli_env.sh 2>/dev/null || true

# 2. Load tool-specific secrets (mode 0600, not embedded in this script)
. /data/local/tmp/ancli/secrets/<tool>.env 2>/dev/null || true

# 3. Inject static runtime env vars (from registry)
# export SOME_VAR='value'

# 4. Launch PRoot with unified global binds
# All common Android root directories are bound unconditionally so that
# Node.js fs.realpath and other symlink-following logic never breaks.
exec /data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs \
    -b /dev -b /proc -b /sys -b /data/local/tmp/ancli \
    -b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system \
    -b /data/local/tmp/ancli/hosts:/etc/hosts -b /data/adb \
    -w "$PWD" /usr/bin/env <tool> "$@"
```

---

## 5. Security & Ownership Hardening

- **Command Whitelist**: Restricts execution inside the container to trusted prefixes (`pip`, `npm`, `apt-get`, `apt`, `curl`, `rm`, `agy`, `bash`, `sh`, `env`) and blocks shell operators (`;`, `>`, `<`, `&`, newline, `$()`, backticks).
- **File Overwrite Protection**: Inodes and database paths like `installed.json` are forcefully deleted before recreating, preventing local permission lock errors (`[Errno 13] Permission denied`) from conflicting host users.
