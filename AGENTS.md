# AI Agent Guidelines (AGENTS.md)

Welcome, fellow AI Assistant. If you are reading this, you have been tasked with modifying or maintaining the **AnCLI** project. This document contains critical context, constraints, and architectural details to help you immediately understand the codebase and avoid common Android-specific pitfalls.

## 1. Project Context & Purpose
AnCLI is a unified, systemless environment manager and plugin-based installer designed to bring full Linux command-line tools (like Node.js, Go, or Python-based AI agents) to rooted Android devices.
Because Android uses the Bionic C library, native Linux binaries often crash. AnCLI solves this by using `proot` to run a pure Ubuntu `glibc` base system transparently on Android.

## Current handoff (2026-11-13, v1.2.602)

- Keep the single `proot-ubuntu` backend. The old native execution branch and host-mode shims are removed.
- Keep terminal menu labels short. Put command details in `ancli --help`, structured output, and logs.
- The WebUI settings dialog is the visual editor for `lang`, `default_cwd`, `proxy_mode`, and `proxy_url`. It also shows the active backend, Android data channel, effective directory, and proxy mode.
- WebUI task logs are stored as `.webui_*.log` plus metadata under `/data/local/tmp/ancli`. Keep the newest completed task logs up to 10 MB total, do not expire them by age, and expose the history in Settings so a reopened page can select and read old output. Running task logs are never removed by retention.
- Settings also provides a destructive factory reset. It requires `RESET`, deletes installed app payloads, secrets, global config, Agent memory, shared skills, generated root-home files, and all WebUI logs, then regenerates the module template context. Preserve the Ubuntu rootfs and immutable module payload; do not add compatibility loading for pre-reset legacy logs. The optional reinstall mode records app IDs before clearing state and reinstalls them afterward without restoring secrets or proxy settings.
- The WebUI theme is stored locally in the page; the language is persisted through AnCLI global config. Do not add a second settings store.
- `ancli reset --yes` is the offline factory-reset path used by the WebUI. It keeps the rootfs and module payload, clears app state and user data inside the container, deletes all WebUI logs, and regenerates the bundled runtime context. Keep the explicit flag. The optional `--reinstall` mode intentionally reads the bundled or available registry after cleanup so the pre-reset app list can be installed again; it never restores old secrets, proxy settings, or app config.
- `dsh` keeps direct-network mode in the registry. Other tools use the wrapper's system or manually configured proxy. Download actions show the selected proxy path before starting.
- Release `v1.2.602` uses `versionCode=14`; keep the tag, `update.json`, module asset, and changelog aligned.

## 2. Core Architecture
You must understand these three pillars before writing any code:

1. **`src/module/customize.sh` (The Magisk Module Installer)**: 
   - This is the module flash hook executed by Magisk/KernelSU/APatch when the ZIP is installed. `src/install.sh` remains a separate optional bootstrap that downloads the ZIP; it is not called by the manager.
   - Downloads PRoot and a minimal `ubuntu-base` tarball, extracting it to `/data/local/tmp/ancli/rootfs`.
   - Bootstraps APT dependencies (`python3`, `nodejs`, `npm`) during the flash process.
2. **`src/ancli-core.py` (The Package Manager Brain)**: 
   - Runs *inside* the Proot Ubuntu container.
   - Fetches `registry.json` from the cloud.
   - Generates Bash wrappers and injects them into the Android host.
   - `ancli check` resolves each installed tool's *real* version (`version_cmd`) and the
     vendor's *official* latest version (`latest` in the registry), caching both in
     `/data/local/tmp/ancli/.update_cache.json` for the WebUI.
   - **CONSTRAINT**: Must rely strictly on the Python Standard Library (e.g., use `urllib.request`, NOT `requests`).
3. **`src/module/ancli_env.sh` (The Environment Bridger)**:
   - Sourced dynamically by the wrapper script before PRoot execution.
   - Detects Android system proxies (via `dumpsys`) and exports them.
   - Fixes permission locks and mounts `TMPDIR=/tmp`.
4. **`src/registry.json` (The Plugin Schema)**:
   - Contains the installation logic (`pip`, `npm`, `apt`) for third-party CLIs.

## 3. The "Dual-Injection" Systemless Hack (CRITICAL)
Android's `/system/bin` is strictly Read-Only. To expose a newly installed tool (like `aider`) globally to the user's `$PATH`, you **CANNOT** write to `/system/bin`.
Instead, `ancli-core.py` uses a Dual-Injection method:
- **Path A**: `/data/adb/modules/ancli/system/bin/<tool>` (Standard Systemless Module path, takes effect after the phone reboots).
- **Path B**: `/data/adb/ksu/bin/<tool>` or `/data/adb/ap/bin/<tool>` (Dynamic KernelSU/Apatch paths, takes effect instantly without reboot).

*Rule: If you are generating a new wrapper or shortcut, it MUST be written to both Path A and Path B.*

## 4. Technical Constraints & Red Lines (CRITICAL)
When modifying the script execution logic or registry installer commands, you MUST strictly adhere to the following rules:

1. **NO Nested PRoot execution**:
   - `ancli-core.py` runs *inside* the PRoot container. Therefore, do NOT wrap any commands executed by `run_cmd` inside another `proot` invocation block. Doing so causes nested virtualization ptrace lockups, leading to crashes like `/system/bin/sh: bash: inaccessible or not found`.
2. **Bypass ADB Shell Escaping using Python**:
   - Complex installation scripts (e.g. `curl | bash -s -- --dir`) fail when run through adb due to shell escaping bugs where characters like `|` or `--` get corrupted. If a tool installation script contains complex pipe execution, override it to download the script file natively via Python's `urllib.request` inside `/tmp` and then execute it.
3. **Always Force-Delete files before writing wrappers**:
   - Android's root and shell users create files with conflicting ownerships. When generating wrapper scripts, you MUST check if the file already exists and explicitly delete it using `os.remove` before recreating, otherwise the operation will fail with `[Errno 13] Permission denied`.
4. **Command Whitelist Validation**:
   - Any execution inside the container must be explicitly whitelisted in `ALLOWED_CMD_PREFIXES` in `ancli-core.py`. Add `bash ` and `sh ` if running local shell script installers.
   - Registry `version_cmd` probes (`claude --version`) go through `_capture_cmd(..., _probe_prefixes(...))`: the executable names come from the registry and extend the whitelist for that one call only (`validate_cmd` default stays strict). Because a probe runs *automatically* (WebUI page load, `ancli check`), it is stricter than `install_cmd`: `_probe_cmd_allowed()` requires the first token to equal the app's own `executable` and passes `allow_operators=False`, so no `&&`/`||`/`|` chain can be smuggled into a registry entry.
5. **Do NOT force delete container data during Manager uninstall (CRITICAL)**:
   - Magisk/KSU Manager uninstallations trigger `uninstall.sh` in a non-interactive background shell (there is **no TTY detection** in that script — do not claim there is). It defaults to KEEP: only `$ANCLI_DIR/bin` is wiped, while `/data/local/tmp/ancli/rootfs`, `installed.json`, `secrets/`, `registry.json` and `.update_cache.json` survive. A full purge only happens when `/data/local/tmp/ancli_force_purge` exists. This ensures Python dependencies (like `gitpython`) and API keys are not lost during module upgrades.
6. **`--json` stdout must stay parseable**: `_stdout_to_stderr()` redirects both `sys.stdout` and file descriptor 1 while data is gathered, so diagnostics from `os.system`/subprocess helpers land on stderr. Keep new diagnostics on stderr and never print to stdout from a helper used by `list --json` / `check --json`.

## 5. Physical Layout Mapping
When debugging paths or writing cleanup logic, refer to this exact physical mapping (Host Android perspective):

- **Ubuntu Rootfs**: `/data/local/tmp/ancli/rootfs/`
- **NPM Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/node_modules/`
- **Pip Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/python3.12/dist-packages/`
- **Core Script**: `/data/local/tmp/ancli/bin/ancli-core.py`
- **Update Cache**: `/data/local/tmp/ancli/.update_cache.json` (official latest versions from `ancli check`)
- **Shared Skills**: `/root/.agents/skills` inside the container (i.e. `$ROOTFS/root/.agents/skills`); `ancli skills` / `ancli repair` create it and symlink each tool's own path (`~/.claude/skills`, `~/.grok/skills`, `~/.mimocode/skills`, `~/.config/opencode/skills`, `~/.dsh/skills`) to it
- **Magisk Module**: `/data/adb/modules/ancli/`
- **App-data channel**: `/mnt/ancli-data` (host bind of the real `/data`, visible in the container as `/mnt/ancli-data/data/<pkg>`)
- **Global settings (2026-11-13)**: `/root/.ancli-config.json` accepts `lang`, `default_cwd`, `proxy_mode` (`system|manual|direct`) and `proxy_url`. `ancli config` with no app id prints/updates them; `ancli config --json` reports the supported keys. Wrappers read `default_cwd` and proxy settings at launch, so changing them needs no wrapper regeneration. The WebUI's Settings dialog is the visual editor for the same file and shows the backend, data channel, effective directory, and proxy mode.
- **Runtime context**: `ancli agent-init` renders the bundled `src/runtime-context/AGENTS.md` template into the guest-wide `/root/.agents/AGENTS.md`; `ancli repair`, `ancli skills`, and successful installs ensure it too. Existing project `AGENTS.md` files are kept. The generated global `AGENTS.md` contains environment paths and guidance, never secret values.

**Why `/data/data` can look empty.** An `su` started from an app (Termius, an SSH-server app) runs in
that app's mount namespace, where Android masks `/data/data` with a tmpfs (`mode 751`, only the app's
own dirs). This is **not** a proot limitation — the same shell from `adb shell` / a global `su` sees
all ~752 packages, and `test -x` works there too. Do not "fix" it by adding proot binds: the mask
lives in the parent namespace. Every wrapper instead does an idempotent,
non-recursive `mount --bind /data /mnt/ancli-data` before launching — a non-recursive bind does not
inherit submounts, so the mask is left behind, and `/mnt` is already bound into the container.
`ensure_android_data()` (called by `ancli repair`) re-checks it; `status --json` reports `android_data`.

## 6. Adding New Apps to Registry
To add support for a new CLI tool, do not modify `ancli-core.py`. Instead, add a new JSON object to `registry.json`.

**Required Schema:**
```json
"app_id": {
  "name": "Human Readable Name",
  "install_cmd": "npm install -g something",
  "update_cmd": "npm update -g something",
  "uninstall_cmd": "npm uninstall -g something",
  "env_vars": ["ANY_REQUIRED_API_KEY"], 
  "optional_env_vars": ["ANY_PROXY_BASE_URL"],
  "executable": "command_name_to_bind",
  "version_cmd": "something --version",
  "latest": { "source": "npm", "package": "something" },
  "version": "1.2.3"
}
```
*Note: If `env_vars` is provided, `ancli-core.py` will automatically prompt the user for them and inject them into the wrapper via `export KEY="VALUE"`.*

**Prefer tarball / npm / pip installs over `apt` inside the container.** The rootfs ships with a half-configured `systemd` (its postinst cannot run under proot: "Failed to resolve group 'systemd-journal'"), so `dpkg --configure -a` aborts with a proot assertion, and `apt-get` also fails archive signature checks unless every call carries `-o Acquire::AllowInsecureRepositories=true -o Acquire::AllowUnauthenticated=true --allow-unauthenticated` (see `customize.sh`). A chain that relies on `apt-get … && npm …` therefore dies at the first step and silently falls through to whatever `||` alternative follows. Download the vendor tarball (`.tar.gz`, not `.tar.xz` — `xz` is absent) or install from npm/pip instead.

**Update detection fields (never omit them):**
- `version_cmd` — the **installed** version is read from the tool itself inside the container
  (ask the vendor's real binary; verified prefixes are auto-whitelisted via `_registry_exe_prefixes`).
  Do not gate the probe on the exit code: `grok --version` prints the version but exits 126 on
  device, and some tools print it on stderr — `_probe_installed_version` accepts any version found
  in the output (stdout first, then a stderr-merged retry).
- `latest` — where the **official latest** version comes from. Supported sources:
  `github` (`repo`), `pypi` (`package`), `npm` (`package`), `json` (`url` + `path`),
  `text` (`urls[]`, first version token wins), `static` (no public API — uses `version`),
  `none` (detection unavailable; the UI says so). `note` is free-text documentation for
  humans; no code reads it. An unknown `source` is reported as an error, never treated as
  `static`.
- `version` — declared fallback used when the network is down or the source is `static`.
- Rule: **never** treat a hand-written `version` as the official latest, and never write the
  registry/AnCLI version into `installed_version` — that silently disables update detection
  (see CHANGELOG: "WebUI could not detect official versions").

**Skills are a per-app property too.** Agent CLIs read `SKILL.md` bundles from their own directory
plus the shared `~/.agents/skills` (Claude Code excepted — `~/.claude/skills` is symlinked to the
shared root by `ancli skills`). When adding an app, survey its real paths instead of guessing:
`scripts/survey_skill_paths.py` (piped into the container) greps the binary's own string literals and
the bundled docs. Aider-style tools with no skill system use `CONVENTIONS.md` / `--read`.

## 7. Testing Constraints
- There is **no Android emulator** for this project — and an emulator could not verify this module anyway (it needs a rooted real device).
- Only use `adb` when a device is actually attached (`adb devices` lists one). Even then, stick to read-only inspection (`ls`, `cat`, `settings get`, `logcat`) unless the user explicitly asks you to flash/test.
- **NEVER** claim the module was flashed or verified when it was not, and never flash/uninstall on your own. Fall back to `sh -n` syntax checks, `python -m pytest tests/`, and static reasoning.
- Update detection has two runnable checks: `python -m pytest tests/` (offline unit suite) and `python scripts/smoke_update_check.py` (optional live-upstream check for registry latest sources). Device state comes from `ancli list --json` on the target phone.
- **Hot-patching a running module must go through the container.** A `su -c` one-liner cannot write `/data/local/tmp/ancli/*` or `/data/adb/modules/ancli/*` on this device (denied even with SELinux permissive — the KernelSU `su` mount namespace is the restriction), while the proot child writes the same files fine. Use `scripts/device_deploy_helper.py`, piped into the container with `... python3 -`, to replace `bin/ancli-core.py`, either registry copy, `bin/ancli_env.sh`, `module.prop`, `service.sh` or `webroot/index.html`, and to clean stale files. Restore SELinux (`setenforce 1`) if you temporarily relaxed it.
- **KernelSU's `su -c` resolves only the *first* command via `/data/adb/ksu/bin`.** `echo $PATH` shows no ksu entry, yet `su -c 'dsh --version'` works while `su -c 'dsh --version; claude --version'` fails with "inaccessible or not found" for the second command. Verify tools one per `su -c` invocation (or use absolute paths); do not conclude a wrapper is missing from a chained command. The manager's WebUI exec bridge is unaffected.
- **Never probe a TUI agent by hand.** `su -c 'mimo --version'` ignores the flag, opens the interactive UI and blocks forever (looks like a hung device; it cost a real debugging round). Use `ancli list --json` / `ancli check`, which probe inside the container with `PROBE_TIMEOUT` and never inherit the terminal.
- **After flashing the module, run `ancli repair`.** The flash replaces the module directory with the ZIP contents, so `system/bin/<tool>` loses every wrapper `ancli install` had added (the tools keep working through `/data/adb/ksu/bin`, but dual-injection is incomplete until repair regenerates them). The same repair re-applies the dsh cookie patch. Also expect a harmless `cp: can't create …/bin/proot: Text file busy` in the flash log when a proot process is alive: the running binary is the same version and the reboot settles it.
- **The WebUI can be rendered and reviewed without a device.** `.webui-preview/build.js` stubs `window.ksu` with `list --json` / `status --json` payloads captured from the device and writes `preview-*.html`; screenshot them with `chrome --headless --virtual-time-budget=4000 --window-size=412,900 --screenshot=… file:///…`. Without `--virtual-time-budget` the shot stops mid-load (skeleton cards); the harness disables CSS transitions because virtual time does not advance them (see `.webui-preview/README.md`). After any `webroot/index.html` change: render locally, then deploy through `scripts/device_deploy_helper.py` and open the page in the manager.
- **WebUI JSON calls must stay quiet and parseable.** `status --json` is dispatched *before* the registry fetch (a registry retry prints to stdout and breaks `JSON.parse` — observed on device). Internal payload calls go through `ancli(cmd, true)` so the output panel shows user actions rather than raw JSON.

## 8. Network Proxy & VPN Constraints (CRITICAL)
- **VPN Bypass**: Android VPNs in TUN mode (e.g. Clash, v2rayNG) bypass Root (UID 0) traffic by default. If a Python or curl script runs as root and the proxy is not explicitly set, it will ignore the VPN and fail to connect to domains like `github.com`.
- **Proxy Override**: Always parse the Android system proxy via `dumpsys connectivity`. Extract the `HttpProxy` IP and port. `ancli_env.sh` caches the result for 30 s, keeps the last successful value in `.proxy_cache_last` (used when the probe comes back empty, instead of silently going direct), and exports `no_proxy=localhost,127.0.0.1,::1` so local model servers/MCP endpoints are never tunnelled.
- **DNS inheritance (2026-11-13)**: the host-side `ancli_env.sh` remains the source of truth for proxy and DNS discovery. It probes Android resolvers (properties, then `dumpsys connectivity` → `DnsAddresses`) and caches them in `/data/local/tmp/ancli/.dns_cache` (refreshed hourly); `_get_android_dns()` reads that cache first. A container may run `/system/bin/getprop` or `/system/bin/dumpsys` when the wrapper exposes them and SELinux permits it, but the core must not depend on that optional path. `service.sh` performs the boot-time probe with absolute paths, writes safe fallbacks, and retries asynchronously for up to 5 minutes. Never lead `resolv.conf` with 8.8.8.8 — it is blocked or poisoned on many networks.
- **Browser hand-off (xdg-open)**: Android's own binaries cannot execute inside the glibc guest — `/system/bin/am` fails with `cmd: inaccessible or not found` — so a container-side `xdg-open` that calls `am` looks successful (exit 0) while opening nothing. The container shim (`$ROOTFS/usr/local/bin/xdg-open`, also at `/usr/bin` + `/bin`, plus `sensible-browser`) therefore only appends URLs to `/data/local/tmp/ancli/.open_url`; the generated wrapper runs a watcher that drains that queue with `am start` on the host and prints the URL back to stderr if that fails. **The wrapper must export `BROWSER=/usr/local/bin/xdg-open`**: the npm `open` package (used by `dsh web`) prefers its own vendored freedesktop `xdg-open` over PATH and otherwise ends in `xdg-open: no method available for opening 'http://…'` (rc 3, verified on device). Re-check with `scripts/probe_open_package.py` after a dsh upgrade. The drain also prefers a real browser over the chooser (`am start -p <pkg>`, Chrome first): with no default browser set, `am start` raises "Open with", and a WebView-based pick (Via and friends) does not carry the auth cookie.
- **`dsh web` on Android needs `SameSite=Lax`** (patched by `patch_dsh_web_cookie()`, re-applied by `ancli repair` and after installing/updating dsh). dsh authenticates by setting an HttpOnly `SameSite=Strict` cookie on the token URL and redirecting to `/`; Chrome treats a navigation started by an external app — exactly what an Android Intent is — as cross-site, drops the Strict cookie on that redirect, and the UI answers "dsh web authentication required". Typing the URL or launching from a desktop browser works, which is why upstream does not see it. The patch edits the one packaged file that carries the attribute (`…/@deepseek-ai/dsh-client-connection/lib/index.js`) and is idempotent. If a future dsh release renders differently, `scripts/probe_dsh_web_page.py` replays the token/cookie exchange against a live server.
- **DO NOT hardcode 127.0.0.1**: Android users often run proxy software on other devices (e.g. PC at `192.168.1.x`) and set the Android proxy to that IP. Always use the IP dynamically returned by `dumpsys`.
- **Go Socket Binds**: Go binaries (like `agy`) crash when binding to `198.18.0.x` virtual IPs created by Clash TUN. Ensure `ancli_env.sh` pre-binds these to the loopback interface (`ip addr add 198.18.0.x/32 dev lo`).

## 9. PRoot Path Resolution & TUI Crashes
- **The `/sdcard` Trap**: Android's `/sdcard` is a complex chain of symlinks pointing to `/mnt/user/0/...` or `/storage/emulated/0`; some adb-root namespaces omit the alias entirely.
- **Node.js/Bun TUI Panics**: If you run Node.js-based tools (like `mimo`) inside PRoot while inside `/sdcard`, the tool will attempt to resolve its absolute physical path. If PRoot is missing `-b /mnt` or `-b /data` binds, the physical path resolution will fail, causing the Event Loop or TUI library to silently crash (you won't even see an error, the TUI simply won't launch).
- **The Solution**: Do not choose binds from `$PWD`. Generate wrappers with the complete common-tree list and include each path when it exists in the current namespace: `/sdcard`, `/storage`, `/mnt`, `/data`, `/apex`, `/linkerconfig`, and `/system`. This keeps symlink targets available without emitting a fatal-looking warning for an absent `/sdcard` alias.

## 10. Version & Release Checklist (CRITICAL)
Releases are consumed by Magisk/KernelSU/APatch managers through `update.json`, so **every version number lives in three places and must move together**:

| Where | Field | Must match |
| :--- | :--- | :--- |
| `src/module/module.prop` | `version` / `versionCode` | source of truth; `versionCode` must always increase |
| `update.json` | `version` / `versionCode` / `zipUrl` / `changelog` | same version, and `zipUrl` must point at the **exact** asset name of the new Release |
| GitHub Release asset | `ancli-<version>.zip` | file name must equal the basename in `zipUrl` (case included) |

Use a three-part version with an unrestricted numeric patch segment. `v1.2.602` is the current small release after `v1.2.601`. Keep `versionCode` as a separate monotonically increasing integer for KernelSU and Magisk comparisons. `python scripts/prepare_release.py v1.2.602` selects the next code; pass an explicit second argument only when a different increasing code is required.

Release procedure (copy-paste, in order):

```bash
# 1) bump version: src/module/module.prop (version=vX.Y.Z, versionCode=N+1) and update.json (same + zipUrl/changelog)
# 2) update CHANGELOG.md (managers display it via update.json -> changelog URL)
# 3) build the flashable zip (build.sh syncs src/* into src/module/ancli/ first)
sh build.sh
unzip -l ancli-vX.Y.Z.zip | head          # sanity: module.prop must be at the ZIP ROOT, not nested
# 4) commit + tag + push
git add -A && git commit -m "release: vX.Y.Z (versionCode N)"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main && git push origin vX.Y.Z
# 5) publish the asset (name must equal update.json zipUrl basename)
gh release create vX.Y.Z ancli-vX.Y.Z.zip --title "vX.Y.Z" --notes-file CHANGELOG.md
# 6) confirm the manager-visible manifest is live and points to the new asset
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/update.json
```

Release rules:

- **Never reuse or decrease `versionCode`**: managers compare it to decide there is an update.
- **Never ship a zip whose root is not the module root** (no extra folder around `module.prop`), and keep the module directory name equal to `id` in `module.prop`.
- **Upgrade must not lose user data**: `uninstall.sh` preserves `/data/local/tmp/ancli/rootfs` and `installed.json` (see section 4.5). Re-check that rule before every release that touches install/uninstall logic.
- **Pre-release checks you can actually run here**: `sh -n` on every `src/module/*.sh`, `python -m pytest tests/`, and a manual read of the diff for `/system` writes (must not exist — see section 3 dual-injection).
- **On a real device only** (when `adb devices` shows one, and only with the user's go-ahead): flash the zip, then verify `su -c "ls -la /data/adb/modules/ancli"`, `su -c "ancli --version"` (or the tool's own `--version`), a reboot that keeps the command available, and that a manager uninstall leaves `/data/local/tmp/ancli/rootfs` intact.
