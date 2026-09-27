## Unreleased — app install hardening + shared agent skills

### Install / update hardening
- **Failed installs now fail loudly.** After the installer command succeeds, AnCLI checks that the
  tool is really there (its `version_cmd` answers *or* the binary exists in the container); if
  neither holds it reports failure with a non-zero exit instead of "successfully installed". This is
  exactly the check that would have caught the first `dsh` attempt, where the `||` fallback
  installed the CLI under Node 18 and every later run crashed.
- **GitHub-release downloads use `curl -fL --retry 3 --retry-delay 2`** (mimo, claude-code,
  opencode): a 404/HTML error page or a flaky link now aborts the install instead of being unpacked
  as if it were a tarball, and transient failures are retried.
- **claude-code verifies its published checksum**: the installer downloads the asset under its real
  name, fetches `SHASUMS256.txt` from the same release and runs
  `sha256sum -c --ignore-missing` before extracting (verified on device: intact → rc 0, tampered →
  rc 1). mimo/opencode publish no checksum file, so they get the fail-fast/retry treatment only.
- Temporary archives are removed on every path, and the `curl` auto-install fallback now carries the
  permissive apt flags the container actually needs (minimal keyring + half-configured systemd).

### Shared agent skills
- Every agent CLI reads skills from its own directory, but five of six also read `~/.agents/skills`.
  New `ancli skills` (also run by `ancli repair` and the WebUI's 修复环境 button) creates
  `/root/.agents/skills` and symlinks each tool's own skills path to it — `~/.claude/skills`,
  `~/.grok/skills`, `~/.mimocode/skills`, `~/.config/opencode/skills`, `~/.dsh/skills`. A tool
  directory that already holds your own skills is left untouched and reported as `kept`; an empty
  placeholder is replaced. Verified on device (all five links resolve to the shared root).
- The config-ownership repair (core `_fix_config_permissions` + `service.sh`) now covers `.agents`,
  `.dsh`, `.grok` and `.mimocode` too, so skills and config stay readable/writable for both root and
  the shell user across upgrades.
- README documents the per-tool skill matrix (global and project-scoped paths, formats, and the
  fact that Aider has no skill system and uses `CONVENTIONS.md` / `--read` instead).

## Unreleased — DeepSeek Harness app + WebUI official-version detection

### New app
- **DeepSeek Harness (`dsh`)** joins the registry: installed from the official npm package
  `@deepseek-ai/dsh` after bootstrapping a modern Node (the container's apt Node is 18, `dsh`
  needs `^22.19 || >=24`). Registry entry exposes `DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL` plus the
  proxy trio, probes `dsh --version`, and resolves its official latest version from the npm
  registry. `dsh web` serves the Web UI on `127.0.0.1:3080` and reaches the phone's browser through
  AnCLI's existing `xdg-open` bridge; `dsh headless "task"` runs one-shot tasks. DSH reads exactly
  the proxy variables the AnCLI wrapper already exports, and its `$DSH_HOME` (`/root/.dsh`) survives
  module upgrades.
- Version detection learned two things the new app needed: **scoped npm names** are queried as one
  path segment (`@deepseek-ai%2Fdsh`) and **prereleases are compared as prereleases** —
  `0.1.7-rc.2 < 0.1.7-rc.3 < 0.1.7` — instead of being flattened to `0.1.7`, which would have
  frozen the update badge for a project that only ships `-rc.N`.
- **Device-verified**: `dsh --version` → `0.1.7-rc.2` (Node v24.21.0 bootstrapped into `/usr/local`),
  `dsh web --no-open` listens on `127.0.0.1:3080` and answers from the phone shell too (HTTP 401
  without the token URL it prints), wrappers land in `/data/local/tmp/ancli/bin` and
  `/data/adb/ksu/bin`, and `ancli check` reports it as up to date from the npm registry.
- **Why not `apt`**: the container cannot verify archive signatures (minimal keyring) and its
  `systemd` package has been half-configured since bootstrap, so an `apt-get … && npm …` chain dies
  at the first step and falls through to the `||` alternative — which is how the first attempt
  installed `dsh` under Node 18 and crashed on start. The entry now unpacks the official Node
  tarball (`.tar.gz`, because `xz` is absent) straight into `/usr/local`. Downloads try
  npmmirror first: nodejs.org measured ~17 KB/s from the device, the mirror finished in seconds.

### Device-verified deploy path
- Hot-patching a running module works **from inside the container**, not from a `su` one-liner: the
  KernelSU mount namespace used by `su -c` denies writes to `/data/local/tmp/ancli/*` and
  `/data/adb/modules/ancli/*` even with SELinux permissive, while the proot child (same kernel
  objects) writes them fine. Verified by replacing `bin/ancli-core.py`, both registry copies,
  `bin/ancli_env.sh` and `webroot/index.html`, and by deleting the stale `.webui_last.log` residue —
  so the WebUI page *can* be updated without a reflash as long as the write goes through the
  container.

## Unreleased — WebUI could not detect official versions

### Bug Fixes
**The WebUI never knew a tool's real version (root cause)**
- **Root Cause**: `installed_version` was written as the *registry's* declared version (or, in
  older records, the AnCLI framework version itself — the device showed `mimo v1.2.2` while
  `mimo --version` printed `0.1.10`). Two consequences: the recorded value always equalled the
  comparison target, so no update ever appeared, and records like `1.2.2` compared "newer" than
  a real tool version (`grok 1.0.0` vs `1.1.7`), silently suppressing genuine updates.
- **Fix**: install/update/`check` now probe the tool itself (`version_cmd` in the registry) inside
  the container and store that, with a `version_verified` flag. Legacy records are treated as
  untrusted: a differing official version counts as an update until the first `check` rewrites them.

**"Official latest version" was a hand-maintained number**
- **Root Cause**: `cloud_version` came from a static `version` field in `registry.json`, and
  `ancli list --json` read only the local cache (no network). On the real device every installed
  tool looked up to date while anthropics/claude-code had already moved 2.1.226 → 2.1.283.
- **Fix**: new `latest` registry spec resolved at check time from the vendor's own endpoint —
  GitHub Releases, PyPI, npm, a JSON release manifest (`agy`) or a text channel pointer (`grok`,
  GCS mirror fallback). Results are cached 6 h in `/data/local/tmp/ancli/.update_cache.json`;
  `ancli list --json` merges the cache (offline-safe) and the WebUI shows the source of every
  version. Registry versions refreshed: aider 0.86.2, mimo 0.1.14, agy 1.2.12, claude-code
  2.1.283, opencode 1.18.32, grok 1.0.41.

**Failed updates were reported as success**
- **Root Cause**: `update_app()` returned nothing and `ancli update` always exited 0, so a failed
  download ("curl: Couldn't connect to server") still ended as WebUI "✓ 完成" — the stale version
  and no-op update looked fine.
- **Fix**: install/update/uninstall propagate their result and exit non-zero on failure with an
  explicit message; the WebUI logs a red `退出码: N（命令失败，未生效）`.

### Features
- `ancli check [--json]`: refreshes the cloud registry, the installed versions and the official
  latest versions in one pass.
- The **更新 button always pulls the vendor's latest channel** (that is what `update_cmd` is:
  GitHub `releases/latest/download/...`, the vendor installer or `pip --upgrade`) and re-resolves
  the official version right after a successful update, so the status is honest without waiting for
  the next full check. Its tooltip shows the exact upstream command; clicking it when the badge says
  "up to date" is a supported force re-pull.
- Version probes do not trust the exit code: `grok --version` prints the version but exits 126 on
  device, and some tools print it on stderr — a version found in the output wins, with a
  stderr-merged retry.
- WebUI: **检查更新 (check)** button, "官方最新/参考版本 + 来源" line, "可更新" highlighting on the
  update button, "未核实" markers for legacy records, and an automatic check when the cache is
  older than 6 h. Old module builds (no `check_ttl` in the payload) are detected and told to update.

### Other Fixes
- **Bundled registry deployed to the path the core reads**: `customize.sh` wrote
  `$ANCLI_DIR/registry.json` while the core reads `$ANCLI_DIR/bin/registry.json`; both are now
  written and the newest-mtime copy wins, so a stale leftover can no longer shadow a fresh registry.
- **`--json` stdout stays parseable**: registry retries, corrupted-state warnings and blocked-command
  notices are redirected to stderr during `list --json` / `check --json`.
- **WebUI background log is per-run**: concurrent actions used to share `.webui_last.log` and
  interleave each other's output; the tail offset now advances by UTF-8 bytes, so logs are no
  longer duplicated/garbled.

### Verified on device (2026-09-27, KernelSU)
`ancli check` on a real device that had been stuck showing "up to date" reported, and
`installed.json` was migrated in place without touching API keys:

| Tool | Installed (probed) | Official latest | Source |
| :--- | :--- | :--- | :--- |
| MiMo Code | 0.1.10 (was recorded as 1.2.2) | 0.1.14 | `github:XiaomiMiMo/MiMo-Code` |
| Antigravity CLI | 1.1.27 (was 1.2.2) | 1.2.12 | vendor manifest |
| Claude Code | 2.1.226 | 2.1.283 | `github:anthropics/claude-code` |
| Grok CLI | 1.0.0 (was 1.2.2) | 1.0.41 | vendor channel |

### Independent review fixes (same release)
Two adversarial reviews (one on the new code, one on the untouched code) ran against
this diff; every finding below was reproduced, then fixed and covered by a test:

**Data-loss / correctness**
- **`ancli check` no longer overwrites the install database from a stale snapshot.**
  It ran for minutes while the WebUI could install/update in parallel; writing back the
  snapshot taken at the start rolled records back or deleted an app installed in the
  meantime. Probes and the env migration are now merged field-by-field into a fresh read.
- **`save_installed` uses `os.replace`**: the old remove-then-rename had a window where a
  crash lost the whole database (records *and* configured key names).
- **Re-installing an app keeps its configuration** (`ancli install <id>` used to blank
  `env` and the wrapper's secrets line → "logged out" tools).
- **A failed upstream lookup keeps the last known-good version** (and the WebUI retries
  after 15 min instead of freezing the loss for 6 h).

**WebUI configuration was stored percent-encoded (pre-existing, device-confirmed)**
- `config` now `unquote`s values, and a migration decodes values already written to disk
  and rewrites the secrets files. Evidence: `secrets/claude.env` held
  `ANTHROPIC_BASE_URL=https%3A%2F%2Fapi.deepseek.com%2Fanthropic`, so Claude Code/MiMo
  received a literal, unusable URL. Runs automatically on `ancli check` / `ancli repair`.

**Security / blast radius**
- **Registry `version_cmd` runs automatically (WebUI load), so it is now strictly validated**:
  first token must be the app's own executable, and no shell operator is allowed — a
  tampered registry can no longer turn "open the WebUI" into an arbitrary command chain.
- **Version probes can no longer read a version out of an error banner** (the stderr pass is
  limited to "stdout empty and the tool succeeded" and parsed conservatively) — that would
  have stored e.g. `20.11.0` and hidden every real update.
- **Host shims (`git`/`bash`/`curl`) are no longer synced into `/data/adb/ksu/bin`** by
  `service.sh`, and a boot pass removes shims an older version copied there (they shadowed
  those commands for every root shell and other modules). Confirmed on device.
- **Credential directories are owner-only** (`chown 2000` + `u+rwX,go-rwx` instead of
  world-readable `755`).
- **State files are root-owned 0644**: `installed.json` and `.update_cache.json` no longer carry a
  world-write bit — AnCLI is a root tool (`su -c ancli …` or the manager's WebUI bridge), so the
  shell user only ever needs to read them. Secrets keep 0600 + shell-uid ownership because wrappers
  run as the shell user. Both writes also go through `os.replace` now, so a crash cannot leave the
  database or the cache missing.

**Boot / network**
- **`service.sh` no longer overwrites the container DNS with 8.8.8.8/1.1.1.1 on every boot**:
  it now prefers the real Android DNS servers (like `customize.sh` and `_write_resolv_conf`),
  falling back to public resolvers only if none exist. This is what made apt/pip/curl — and
  now `ancli check` — time out on networks where 8.8.8.8 is blocked.
- `ancli_env.sh` exports `no_proxy=localhost,127.0.0.1,::1` (local model/MCP endpoints are
  no longer pushed through a remote proxy) and falls back to the **last known-good proxy**
  when the system proxy is momentarily unadvertised instead of connecting directly.

**Honesty / diagnostics**
- `ancli check` exits non-zero when *no* upstream lookup succeeded (previously "✓ 完成" on a
  dead network); `ancli uninstall` returns the tool's own result; `--json` stays parseable
  even if a child process writes to fd 1 (`_stdout_to_stderr` now redirects the descriptor).
- `fetch_registry` fails fast and loudly on 4xx instead of retrying a broken URL and silently
  serving a stale cache; offline fallback logs say whether the *fetched cache* or the
  *bundled registry* was used.
- The registry reports unknown `latest.source` values instead of silently treating them as
  static, four-segment versions compare correctly, and hand-edited cache types can no longer
  crash the CLI.

**Other**
- Downloaded installer scripts are removed from the container after use; the soft-uninstall
  placeholder now prints "Run: ancli repair" instead of a raw 127; the interactive menu gained
  `[c] Check for updates`; the WebUI sweeps `.webui_*.log` files older than 2 h.
- Every app now exposes `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` as optional config, matching what
  the README already promised for TUN-only setups (previously no app exposed them).
- Tests: 115 cases, fully hermetic (a network guard fails any test that reaches out), including
  regression coverage for every finding above.

---

## AnCLI v1.2.3 — Hotfix Batch & Release (2026-08-08)

### Runtime Fixes
- **proot reverted to official static build**: the termux build (faccessat2 fix) dynamically links `libtalloc.so.2` + `libandroid-shmem.so`, which do not exist on a pure Android host — the module installer kept failing with `CANNOT LINK EXECUTABLE`. Official 5.4.0 static proot (402KB, zero external deps) restores clean installs.
- **agy / grok back to proot mode**: the current agy release (1.1.11) is dynamically linked (188MB); native direct-exec fails with `No such file or directory` (no `/lib64/ld-linux-aarch64.so.1` on Android). Removed `"native": true` from the registry — wrappers now run inside the container again. Verified on device: `agy --version` → 1.1.11.
- **Known limitation documented**: bash `[ -x ]` (faccessat2) still misreports on aarch64 proot; workaround (sh wrapper / `java -cp` direct call) recorded in README and device AGENTS.md.

### WebUI Config Presets
- Added verified Anthropic-compatible endpoints (from official docs) for Claude Code config: DeepSeek `https://api.deepseek.com/anthropic`, 智谱 GLM `https://open.bigmodel.cn/api/anthropic`, Kimi `https://api.moonshot.cn/anthropic`, OpenRouter `https://openrouter.ai/api`.
- Kimi/OpenRouter use `ANTHROPIC_AUTH_TOKEN` (UI warns to leave `ANTHROPIC_API_KEY` empty); registry `claude-code` gained the `ANTHROPIC_AUTH_TOKEN` optional field.
- Removed dead UI fields (OPENAI_*/GEMINI_/ANTIGRAVITY_/DEEPSEEK_/GROK_*/MIMO_ hints and EXTRA_KEYS) left over from deleted config features.

---

## AnCLI v1.2.2 — Security Hardening & Architecture Refactor

### Security Fixes
**API Key Secure Storage (High)**
- **Root Cause**: API keys (e.g. `ANTHROPIC_API_KEY`) were embedded as plaintext `export` lines inside world-readable (chmod 755) wrapper scripts at `/data/local/tmp/ancli/bin/` and `/data/adb/ksu/bin/`, exposing credentials to any process with filesystem read access.
- **Fix**: Introduced a dedicated `secrets/` directory (`chmod 700`) with per-tool secrets files (`chmod 600`). Wrapper scripts now `source` the secrets file at runtime instead of embedding keys inline. Existing users' keys are automatically migrated on the next `ancli repair` or `ancli config` run.

**Shell Injection Defense for env var Values (Medium)**
- **Root Cause**: User-supplied env var values (API keys) were interpolated into wrapper scripts with single-quote wrapping (`export KEY='value'`). A value containing a single quote would break the shell syntax and could be exploited for injection.
- **Fix**: Replaced manual quoting with Python's `shlex.quote()` for all env var values in both wrapper scripts and secrets files.

**SSL Verification Scoped to Per-Request Context (Medium-High)**
- **Root Cause**: `ssl._create_default_https_context = ssl._create_unverified_context` globally disabled certificate verification for all network calls in the process, removing all MITM protection.
- **Fix**: Removed the global monkey-patch. Registry fetches and installer downloads now create a local `ssl._create_unverified_context()` context passed only to their specific `urlopen()` calls, preserving security for all other SSL operations.

### Architecture Improvements
**Registry-Driven Installer Dispatch**
- Replaced the hardcoded `if app_id == "agy"` / `if app_id == "grok"` branches in `_install_proot()` with a generic `_install_pipe_script()` function driven by three new registry fields: `install_method`, `installer_url`, and `installer_script_env`. New apps requiring script-based installation no longer need Python code changes — only a registry entry update.

**`ancli list` Decoupled from Network**
- `ancli list` previously triggered a full registry fetch from GitHub on every invocation. It now reads only from local disk cache (`_load_local_registry_cache()`), making it instantaneous and offline-safe. Write operations (install/update/config/repair) still fetch the latest cloud registry.

### Other Fixes
- **Fcitx5 Conditional Install**: `customize.sh` now checks the Android system locale (`getprop persist.sys.locale`) and only installs Fcitx5 + Chinese addons (~50 MB) on CJK-locale devices (zh/ja/ko). Non-CJK devices skip these packages entirely.
- **Scoped proot Kill on Uninstall**: `uninstall.sh` now uses `pkill -f "proot.*ancli/rootfs"` instead of `killall proot`, avoiding accidental termination of unrelated proot sessions (e.g. Termux-proot).
- **Version Sync**: `ancli-core.py` `VERSION`, `customize.sh` banner, and `registry.json` version all unified to `v1.2.2`.
- **Secrets Cleanup on Uninstall**: `remove_wrapper()` now also deletes the associated `secrets/{executable}.env` file to prevent stale credential files on disk.

---

## AnCLI v1.2.1 — Physical Keyboard Input Method & Host Environment Sandboxing

### What's New
**Fcitx5 Input Method Support for Physical Keyboards**
- **Bootstrap Package Addition**: Added `fcitx5` and `fcitx5-chinese-addons` to the default bootstrapping package list in `customize.sh` during module installation.
- **Environment Integration**: Configured standard input method variables (`GTK_IM_MODULE=fcitx`, `QT_IM_MODULE=fcitx`, `XMODIFIERS=@im=fcitx`) inside `ancli_env.sh` to solve the issue where users using external physical keyboards (Bluetooth/USB) on Android tablets/phones cannot input Chinese or non-English characters directly in terminal-based TUI tools (like Aider, MiMo).

### Bug Fixes
**Host Environment Variable Isolation for Container Binaries**
- **Root Cause**: Containerized binaries (like Go-based `agy`) inherit Termux environment variables (like `PREFIX` and `TERMUX_VERSION`) from the host shell. Go's runtime or execution hooks detect these and incorrectly attempt to run host-side Termux paths (like `/data/data/com.termux/files/usr/bin/bash`), which do not exist inside the isolated container.
- **Fix**: Added explicit `unset` commands in `ancli_env.sh` to scrub all Termux-specific variables before entering the container, ensuring containerized binaries run in a clean, standard Linux environment and look up the container's own standard `/bin/bash` in `$PATH`.

---

## AnCLI v1.1.1 — Storage Bind Fix

### Bug Fixes
**MiMo/Aider `chdir` Failure on Internal Storage**
- **Root Cause**: When executing from `/storage/emulated/0/...` (which `/sdcard` symlinks to), `proot` failed to locate the directory inside the Ubuntu rootfs because only `/sdcard` was explicitly bound.
- **Fix**: The PRoot wrapper generator now explicitly binds `-b /storage` alongside `-b /sdcard`, ensuring all nested and symlinked storage paths resolve correctly within the guest container without triggering `proot warning: can't chdir`.

**Node.js / Bun TTY Input Hangs (Black Screen/Garbage Chars)**
- **Root Cause**: Node.js and Bun use modern `io_uring` polling by default, which PRoot's syscall interception mechanism on Android does not support properly. This caused interactive TUIs (like MiMo Code and Claude Code) to freeze their event loop and fail to read standard input, echoing raw ANSI codes like `^[[3~`.
- **Fix**: Injected `export UV_USE_IO_URING=0` and `export BUN_FEATURE_FLAG_IO_URING=0` into the execution wrappers, forcing standard `epoll` fallback and completely restoring flawless keyboard interactivity for Node-based tools.

**Core Engine Updates Failing to Propagate to Existing Apps**
- **Root Cause**: The `ancli repair` command previously skipped wrapper regeneration if a wrapper file already existed, preventing core engine bug fixes (like the storage and `io_uring` fixes above) from applying to already-installed applications.
- **Fix**: `ancli repair` now unconditionally regenerates all wrappers for installed apps, guaranteeing they always run on the latest AnCLI engine logic without requiring a reinstall.

---

## AnCLI v1.1.0 — Global Instant Access & UX Polish
### What's New
**True "No Reboot" Global Execution for KernelSU**
- **Root Cause**: KernelSU's SELinux policy enforces a strict prohibition on creating new files within `/data/adb/ksu/bin` after boot, which previously blocked `ancli install` from establishing instant-access wrappers.
- **Pre-seeded Placeholders**: The module installer (`customize.sh`) now pre-creates executable placeholders for all registry apps during the flash stage (when SELinux is permissive). These placeholders transparently route commands to the actual PRoot wrappers, achieving true instant global access without a reboot.
- **Boot-time Sync**: `service.sh` now automatically syncs and overwrites placeholders with real PRoot wrappers upon every boot, cementing long-term stability.
- **Clarified Logs**: Replaced the misleading "Requires Reboot" warning during installation with accurate status reporting.

**Revamped Uninstallation & App Management UX**
- **Expanded App Store Menu**: The main `ancli` TUI now features a dedicated, highly visible `[u] Uninstall an app` option to prevent feature-discovery issues.
- **Comprehensive Purge Options**: The uninstallation submenu now provides structured choices:
  1. Remove a specific app.
  2. Batch-remove all installed apps (while keeping the Ubuntu container safe).
  3. Safe-destruct guide: Provides explicit terminal commands (`rm -rf`) for users who wish to entirely obliterate the AnCLI framework from their storage.
- **Clarified Manager Descriptions**: Updated `module.prop` to explicitly reassure users that uninstalls triggered from the Magisk/KernelSU Manager are **Soft Uninstalls** (data and configs are safely preserved).

---

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
