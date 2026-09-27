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
  "executable": "opencode",
  "version_cmd": "opencode --version",
  "latest": { "source": "github", "repo": "anomalyco/opencode" },
  "version": "1.18.32"
}
```

`version_cmd` is what makes update detection honest: after install/update (and on
every `ancli check`) the core runs it **inside the container** and stores the
version the tool reports. Storing the registry's declared `version` instead is a
trap — the record then always equals the registry, so no comparison can ever see a
newer upstream release (see CHANGELOG: "WebUI could not detect official versions").

### 3.1 Update detection pipeline

```
ancli check ─┬─ fetch_registry()            → refreshes /root/.ancli-registry.json
             ├─ _probe_installed_version()  → `version_cmd` per installed tool (container)
             ├─ _latest_version()           → official API per registry `latest` spec
             └─ _save_update_cache()        → /data/local/tmp/ancli/.update_cache.json
                                                {"ts": epoch, "latest": {app: {...}}}

ancli list --json / WebUI  ← reads local registry + update cache only (no network)
```

`latest.source` values supported by `_latest_version()`:

| source | fields | resolution |
| :--- | :--- | :--- |
| `github` | `repo` | `releases/latest` → `tag_name` |
| `pypi` | `package` | `info.version` |
| `npm` | `package` | version of the `latest` dist-tag |
| `json` | `url`, `path` | dotted path inside a JSON manifest (vendor release manifests) |
| `text` | `urls[]` | first version token of a channel pointer, tried in order |
| `static` | – | the registry's declared `version` (no public API exists) |
| `none` | – | detection unavailable by design; the UI says so instead of guessing |

Failures are per app and never fatal: the error is recorded in the cache and shown
in the WebUI, **the previously resolved version is kept**, and the auto-check window
drops to 15 minutes (`CHECK_TTL_FAILED`) so a transient outage is retried instead of
freezing the loss for 6 hours. Upstream lookups reuse the proxy exported by
`ancli_env.sh`, so they work behind the same Android system proxy as the installers.

Persistence is merge-based on purpose: a check can run for minutes while the WebUI
installs or updates another tool, so probed versions and the encoded-config migration
are merged field-by-field into a freshly re-read `installed.json`
(`_merge_installed_fields`) rather than written back from the snapshot taken at the
start of the run.

The WebUI triggers `ancli check` itself when `last_check` in the list payload is
older than `check_ttl` (6 h, or 15 min after failures), which also migrates legacy
install records that still hold the AnCLI framework version and decodes config values
that older WebUI builds stored percent-encoded.

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
