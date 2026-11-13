# AnCLI

AnCLI is a unified, systemless environment manager and plugin-based installer for rooted Android devices. It runs supported GNU/Linux command-line tools inside one Ubuntu glibc PRoot container while exposing controlled Android paths, proxy handling, browser hand-off, and root-manager wrappers.

The current runtime has one execution backend, `proot-ubuntu`. Android `/system` remains read-only. Tools are exposed through systemless wrappers in the module and KernelSU/APatch dynamic paths, while the guest-wide Agent runtime context lives at `/root/.agents/AGENTS.md`.

## Features

- **No host Node.js Required for Standalone Tools**: Claude Code, OpenCode, and MiMo Code are installed as standalone, precompiled Linux-arm64 binaries. DeepSeek Harness (`dsh`) is the explicit npm exception and bootstraps its own Node LTS inside the container.
- **Systemless Module Integration**: Installs as a standard Magisk/KernelSU/APatch module. The module overlay exposes wrappers through `/system/bin` on boot, while dynamic root paths receive instant-access wrappers without a reboot.
- **OTA Updates**: Integrates with the root manager's `updateJson` mechanism for automated updates.
- **Boot Service**: Automatically restores DNS configurations and file permissions on every boot.
- **Dynamic Configuration Injection**: Configure API keys and custom endpoints anytime via `ancli config <app_id>`. Credentials are stored in per-tool secrets files (mode 0600) and sourced by the wrapper at runtime — never embedded in world-readable wrapper scripts.
- **Bundled application catalog**: Applications and installation steps are defined by the registry shipped with the module.
- **Escaping & Proxy Passthrough**: Bypasses ADB character escaping bugs via Python urllib direct downloads, forwards the Android system proxy to tools that need it, and keeps DeepSeek Harness (`dsh`) in direct-network mode so stale proxy state does not break its connection.
- **PRoot Syscall Stabilization**: Automatically mitigates Android kernel `io_uring` and `epoll` translation bugs, ensuring modern Node.js and Bun interactive TUIs (like MiMo and Claude Code) can process raw keyboard input flawlessly without event loop blocking.
- **Physical Keyboard Input Method Support**: Pre-installs `fcitx5` and `fcitx5-chinese-addons` during the container bootstrap phase, and automatically exports standard input variables (`GTK_IM_MODULE=fcitx`, `QT_IM_MODULE=fcitx`, `XMODIFIERS=@im=fcitx`) to solve the issue where users using external physical keyboards (Bluetooth/USB) on Android tablets/phones cannot input Chinese or non-English characters directly in terminal-based TUI tools (like Aider, MiMo).
- **Cross-Sandbox Browser Redirect (OAuth login)**: Solves the headless virtualization restriction where terminal agents (like `grok login`, `agy auth login`) cannot open a browser from the guest. The container queues the URL and the host wrapper launches Android's browser, with the URL echoed for manual fallback.
- **Multi-language Support (zh/en)**: The CLI and WebUI share a persistent language setting.
- **Visual WebUI Settings**: The Settings dialog edits language, theme, default working directory, proxy mode, and manual proxy URL, and shows the active backend, Android data channel, effective directory, and proxy mode.
- **Short CLI Output**: Interactive labels and status lines stay compact; command details remain available through `ancli --help`, JSON output, and logs.
- **Security Hardened**: Command whitelist validation, shell operator blocking, input sanitization, and path traversal guards.
- **Shared Agent Skills**: one global skills directory (`~/.agents/skills` inside the container) is symlinked into every agent CLI's own skills path, so a `SKILL.md` written once is picked up by Claude Code, MiMo, Antigravity, Grok, OpenCode and DeepSeek Harness. Run `ancli skills` (or the WebUI's 修复环境) to create the layout.
- **Reliable dsh WebUI launch**: the AnCLI WebUI waits for dsh's token URL before opening the browser and reuses an already listening dsh service instead of starting a second copy on port 3080.
- **Android robot and CLI branding**: the module icon and WebUI header use the bundled black terminal/Android robot mark.
- **Terminal lifecycle binding**: Agent wrappers stop their PRoot child when the terminal parent disappears, while ordinary backgrounding keeps the Agent running. A full reset stops known Agent and PRoot processes before clearing their files.
- **Persistent WebUI task logs**: installs, updates, checks, repairs, and uninstalls run in separate log tabs. The tabs and card logs are restored after reopening the WebUI, and completed task logs are retained up to 10 MB total without a time-based expiry. Settings → Log history lists, opens, and clears completed task logs.
- **Factory reset**: Settings → Factory reset removes installed tools, credentials, global configuration, Agent memory, shared skills, task logs, and generated app files. It keeps the Ubuntu rootfs and module payload, then regenerates the module's runtime context. The reset requires typing `RESET` and does not migrate pre-reset logs. An optional reinstall checkbox repeats the pre-reset app list in the clean PRoot without restoring secrets, proxy settings, or app configuration.

## Project guidance

- [AGENTS.md](AGENTS.md) is the development and maintenance handoff for this repository. Read it before changing module scripts, registry entries, wrapper generation, release assets, or device-deployment code.
- `src/runtime-context/AGENTS.md` is the template for Agents running inside the phone container. It is rendered to `/root/.agents/AGENTS.md`; it is separate from the repository's development guidance.
- Antigravity CLI also discovers the same generated context through `/root/.gemini/AGENTS.md` and `/root/.gemini/GEMINI.md`; AnCLI links both names to the canonical file.

## Supported Applications
*(Read from the registry bundled with the module)*

| App | Runtime | Installation Method |
| :--- | :--- | :--- |
| **Aider** | Python | pip package |
| **MiMo Code** | Node.js/JS | Precompiled Release binary |
| **Antigravity CLI (agy)** | Go | Standalone release binary (proot mode — current release is dynamically linked) |
| **Claude Code** | Node.js/JS | Precompiled Release binary (NPM-free) |
| **OpenCode** | Node.js/JS | Precompiled Release binary (NPM-free) |
| **Grok** | Rust | Standalone release binary (proot mode) |
| **DeepSeek Harness (dsh)** | Node.js/JS | npm package `@deepseek-ai/dsh` + Node LTS bootstrap (needs Node ≥ 22.19) |

### DeepSeek Harness on Android

`dsh` is the one app that is installed from **npm** rather than a prebuilt binary, because its
launcher resolves 80+ plugin-bundle packages. The registry entry therefore bootstraps a modern
Node first (the container's apt Node is 18; `dsh` requires `^22.19 || >=24`) and then installs the
CLI from the official registry:

```bash
ancli install dsh        # download Node LTS → npm i -g @deepseek-ai/dsh
dsh --version            # runtime version (also what `ancli check` probes)
dsh web                  # Web UI on http://127.0.0.1:3080 — opens the phone's browser via AnCLI's xdg-open bridge
dsh web --no-open        # …or just print the URL
dsh headless "summarize this repo"   # one-shot task, prints the answer and exits
```

- **API key**: run `ancli config dsh` and set `DEEPSEEK_API_KEY` (optionally `DEEPSEEK_BASE_URL`),
  or enter it in the Web UI under Settings → Models. Either way it lands in `/root/.dsh`
  (`$DSH_HOME`), which module upgrades preserve.
- **Proxy**: DSH uses direct-network mode in the AnCLI registry. It does not inherit the cached
  Android system proxy. Other tools continue to use the wrapper's system-proxy detection.
- **Profiles**: `web`, `headless`, `sdk`, `sdk-minimal` and `acp` auto-initialize on first use.
  **There is no `tui` profile**: `dsh tui` aborts with *"profile \"tui\" does not exist; create it with
  `dsh plugin --profile tui add <package>`"*, the shipped templates in `packages/boot/app-boot`
  contain no terminal app bundle, and no TUI bundle is published on npm — the examples in the CLI
  `--help` are stale. The interactive interface is the Web UI; for scripted one-shots use
  `dsh headless "…"`. (`dsh plugin … add <package>` needs `pnpm`, which the container does not ship.)
- **The browser opens itself.** `dsh web` prints an authenticated URL and hands it to the phone's
  browser (AnCLI bridges it: the container cannot execute Android's `am`, so the wrapper opens it on
  the host and echoes the URL back if that fails). The URL carries a **per-run token** — opening the
  bare `127.0.0.1:3080` from a bookmark shows *"dsh web authentication required"*, so use the printed
  URL (it sets a signed cookie and then redirects to the token-less one). `--no-open` keeps it in the
  terminal; if a previous `dsh web` is still running, the port is taken and the new one exits —
  stop the old session first (`Ctrl+C`).
- **When the page says "authentication required"**: that text only means the request arrived without a
  valid cookie. (1) Make sure the server on that port is the instance you just started — a leftover
  `dsh web` rejects the new instance's token, so stop old sessions first; (2) open the printed URL in a
  *fresh* tab (a reload of the `/` address it redirected to, or a private window, will not have the
  cookie); (3) if the browser blocks cookies for the site, allow `127.0.0.1` or use another browser —
  the server side works, as `scripts/probe_dsh_auth.py` verifies against a live instance.

## Installation

### Method A: Flashing via Root Manager (Recommended)

1. Download the module ZIP (`ancli-v<version>.zip`, e.g. `ancli-v1.2.605.zip`) from the [Releases](https://github.com/AHLLX/AnCLI-Android/releases) page.
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
ancli check                    # Check official latest versions / available updates
ancli doctor --json            # Print read-only path, mount, and tool diagnostics
ancli agent-init               # Create or refresh the guest-global Agent context
ancli agent-init --refresh     # Refresh an AnCLI-generated context after installed tools change
ancli repair                   # Detect and repair environment issues
ancli reset --yes              # Delete app state, memory, credentials and logs; keep the module baseline
ancli reset --yes --reinstall  # Reset, then reinstall the apps recorded before reset without secrets
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

### Configuration

Global settings are stored in `/root/.ancli-config.json` inside the container.
The command accepts exactly these keys:

| Key | Values | Effect |
| :--- | :--- | :--- |
| `default_cwd` | absolute path or empty | Directory used when a tool is launched from `/`; empty restores `/root`. |
| `lang` | `zh`, `en` | Language used by the AnCLI interactive output and WebUI preference. |
| `proxy_mode` | `system`, `manual`, `direct` | Android system detection, configured proxy, or no proxy. |
| `proxy_url` | `http://host:port` or `https://host:port` | Manual proxy address; used when `proxy_mode=manual`. |

```bash
ancli config --json
ancli config --help
ancli config --set lang=zh
ancli config --set default_cwd=/sdcard/projects
ancli config --set proxy_mode=manual --set proxy_url=http://192.168.1.10:7890
ancli config --set proxy_mode=direct
```

Per-tool variables are declared by `src/registry.json` and can be listed with
`ancli list --json`. Set them without embedding secrets in wrappers:

```bash
ancli config claude-code --set ANTHROPIC_BASE_URL=https://example.invalid
ancli config dsh --set DEEPSEEK_BASE_URL=https://example.invalid
```

The current registry exposes provider variables for Claude Code (`ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`) and DeepSeek Harness
(`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`). All supported tools also expose the optional
`HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` variables; Aider, MiMo, OpenCode, Antigravity,
and Grok currently have no additional provider variables.

### Update Detection (official versions)

AnCLI does not guess versions. For every installed tool it asks **the tool itself**
(`version_cmd`, e.g. `claude --version`) for the installed version, and asks the
**vendor's own release endpoint** (`latest` in `registry.json`) for the official
latest one:

| Tool | Official source |
| :--- | :--- |
| Aider | PyPI (`aider-chat`) |
| MiMo Code | GitHub Releases (`XiaomiMiMo/MiMo-Code`) |
| Antigravity CLI | Vendor release manifest (`antigravity-cli-auto-updater…/manifests/linux_arm64.json`) |
| Claude Code | GitHub Releases (`anthropics/claude-code`) |
| OpenCode | GitHub Releases (`anomalyco/opencode`) |
| Grok CLI | Vendor channel pointer (`x.ai/cli/stable`, GCS mirror as fallback) |

Results are cached in `/data/local/tmp/ancli/.update_cache.json`, so the WebUI can
show the last known result while offline. On every WebUI login, AnCLI checks the
installed tools against their official release endpoints; the highlighted
**检查更新 (check)** button runs the same check on demand. A failed lookup keeps
the previous known version and records the error for that app.

```bash
ancli check            # refresh installed + official versions (network)
ancli check --json     # same, machine-readable (for scripts; the WebUI calls `check` + `list --json`)
ancli list             # show "可更新 → vX (source)" once a check has run
```

A failed `ancli update` (no network, proxy down, vendor 404) now exits non-zero,
so the WebUI reports the failure instead of claiming success, and the recorded
version stays untouched.

**Note**: the WebUI page (`webroot/index.html`) can only be replaced by flashing the
module — `ancli repair` cannot write it (`/data/adb/modules` is SELinux-protected). If
`check` reports `[Errno 13] Permission denied`, the current shell domain cannot write
`/data/local/tmp/ancli`; use the WebUI's 检查更新 button (manager exec channel) or
another root shell.

## Network Proxy & VPN Handling

On Android, VPN applications (like Clash, v2rayNG running in TUN mode) typically bypass Root (UID 0) traffic to prevent routing loops. As a result, network requests from tools like `agy` or `aider` running in a root shell will pierce through the VPN and connect directly, potentially being blocked by firewalls.

**AnCLI provides a seamless solution:**
1. **Auto Proxy Detection**: Every time you launch a shortcut (e.g., typing `agy`), the wrapper automatically polls Android's `dumpsys connectivity` state. If your VPN is configured with a global or system HTTP proxy, the wrapper will extract the local IP and port and inject it into the guest container, achieving **zero-config transparent proxying**.
2. **Hardcoded Proxy Override (Pure VPN Mode)**: If your VPN app does not support system proxy injection, simply run `ancli config <app_id>` (e.g., `ancli config agy`) in your terminal. You can directly input your local proxy address (like `http://127.0.0.1:7890`) into the `HTTP_PROXY` and `ALL_PROXY` fields. This will permanently bake the proxy route into that tool's execution flow.

## Shared Agent Skills

Every agent CLI in AnCLI reads skills from its own path, but they all agree on one
"agents" convention — so AnCLI points them at a single directory:

```bash
ancli skills          # create ~/.agents/skills and link each tool's skills dir to it
ancli skills --json   # same, machine-readable
ancli repair          # also runs the setup (the WebUI's 修复环境 button does too)
```

Put a skill at `/root/.agents/skills/<name>/SKILL.md` (frontmatter needs `name` and
`description`; names are lower-case kebab-case) and every tool below sees it. Existing
per-tool skill directories with your own content are never overwritten — the command
reports them as `kept` instead.

| Tool | Global (container `$HOME`) | Project-scoped | Notes |
| :--- | :--- | :--- | :--- |
| **Claude Code** | `~/.claude/skills/<name>/SKILL.md` | `<repo>/.claude/skills/` | linked to the shared root |
| **OpenCode** | `~/.config/opencode/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | `<repo>/.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | bundle folders only, no flat `.md` |
| **MiMo Code** | `~/.mimocode/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | same names in the repo | |
| **Antigravity CLI** | `~/.agents/skills/` (+ `~/.agents/skills.json`), `~/.gemini/config/skills/` | `<repo>/.agents/skills/` | reads the shared root natively |
| **Grok CLI** | `~/.grok/skills/`, `~/.claude/skills/`, `~/.agents/skills/`, bundled `~/.grok/bundled/skills/` | `<repo>/.grok/skills/`, `.claude/skills/`, `.agents/skills/` | |
| **DeepSeek Harness** | `~/.dsh/skills/`, `~/.agents/skills/` | `<repo>/.dsh/skills/`, `.agents/skills/` | also accepts a flat `<name>.md` |
| **Aider** | — (no skill system) | `CONVENTIONS.md` (or `aider --read FILE`) | use conventions files instead |

Skills live inside the container's `/root`, which module upgrades preserve. To keep them
with your projects, put them in the repo (`<repo>/.agents/skills/`) — that path works for
every tool except Aider.

## Workspaces and the phone's real files

The container is not a sandbox: host directories are **bind-mounted** into it, so a container path *is*
the phone path. Edits take effect immediately (any file manager or editor sees them) — nothing to sync.

| Container path | What it actually is |
| :--- | :--- |
| `/sdcard`, `/storage/emulated/0` | **The phone's internal storage** (what file managers show) |
| `/data` | The phone's `/data` (app-private dirs included, visible to root) |
| `/mnt`, `/storage` | The phone's matching mount points |
| `/root` | The container's private HOME: tool config, credentials, skills. **Not** in the phone's file manager |

In `dsh web`'s "choose workspace directory" dialog:

- It starts in the container HOME `/root`, which holds only dotfiles (`.claude`, `.dsh`, …) — that is
  why it looks empty until "show hidden files" is ticked, and those are **tool config**, not your files.
- To work on phone files, type `/sdcard` or a project path such as `/sdcard/projects/demo` in the path box.
- Easier: **start dsh from the directory you want** — `cd /sdcard/projects/demo && dsh web` — the
  workspace then defaults there and the agent's working directory is already your project.
- Note: Android 11+ restricts `/sdcard/Android/data` and similar; quote paths that contain spaces.

### Default working directory (launches from `/`)

A fresh Termius session, or `su` followed by `mimo`, leaves the cwd at `/`. A full-screen TUI walks
its cwd at startup, so `/` means scanning the whole container plus the `/data` and `/sdcard` bindings
(hundreds of thousands of files) — slow, and often a hang. AnCLI starts such launches in a small
directory instead: `/root` by default, or whatever you configure:

```bash
ancli config                                     # show the current setting
ancli config --set default_cwd=/sdcard/projects  # where launches from / land
ancli config --set default_cwd=                  # empty = back to /root
```

The WebUI's **Settings** button edits the same value (stored in the container at
`/root/.ancli-config.json`).

The same dialog also edits `lang`, `proxy_mode`, and `proxy_url`. The theme is a local WebUI
preference. Its status card reports the active Ubuntu/PRoot backend, `/mnt/ancli-data` readiness,
the effective launch directory, and the selected proxy mode.

Changing `default_cwd` no longer moves the runtime context. AnCLI keeps the generated global
context at `/root/.agents/AGENTS.md`; use `ancli agent-init` to regenerate it explicitly.

This only affects launches from `/`: if you `cd` into a project first, your cwd is used verbatim.
The directory must be an absolute path that exists inside the container — otherwise `ancli config`
rejects it with a hint.

### Default Agent memory and environment guide

AnCLI keeps a concise global runtime `AGENTS.md` at `/root/.agents/AGENTS.md`. It is created by
`ancli agent-init`, and is also ensured by `ancli repair`, `ancli skills`, and a successful tool
installation. With the built-in setting the working directory is `/root`; after
`ancli config --set default_cwd=/sdcard/projects`, only tool launches from `/` use that directory.
The runtime context remains global at `/root/.agents/AGENTS.md`.

The generated file records the current AnCLI version, container and Android data paths, proxy/DNS
and browser hand-off contract, installed tool names, configured environment-variable names (never
their values), the shared skills path, and safe operating guidance for agents. It is deliberately
not a secret store. An existing project `AGENTS.md` is kept. The source template is
`src/runtime-context/AGENTS.md`, and the generated device context can be refreshed with
`ancli agent-init --refresh`.

```bash
ancli agent-init
ancli agent-init --json
ancli agent-init --refresh
```

## Other apps' data (`/data/data` can look empty)

Android gives every app its own mount namespace and masks `/data/data` inside it with a tmpfs — only
that app's own directories remain (`mode 751`). A root shell started from Termius or an SSH-server
app inherits that shrunken view, which is why `/data/data` sometimes shows a handful of packages
instead of hundreds. This is not a proot limitation: the same shell started from `adb shell` (or a
system-level terminal) sees the full tree.

AnCLI works around it by **binding the real `/data` non-recursively onto `/mnt/ancli-data`** every
time a tool starts. A non-recursive bind does not inherit submounts, so the tmpfs mask is left behind;
`/mnt` is already bound into the container, so the path is directly visible:

```bash
ls /mnt/ancli-data/data                 # every app's private dir, the real view
ls /mnt/ancli-data/data/com.tencent.mm  # one app's data
```

- Requires root; when `/mnt` is not writable the step is skipped and the tool still starts.
- Idempotent: it checks `/proc/mounts` first and never stacks a second mount.
- `ancli repair` checks and mounts it too; `ancli status` / `status --json` expose `android_data`.
- The mount belongs to the current mount namespace: it goes away with the app, while one made from a
  global `su` lasts until the next reboot.

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
- **Full Purge (Complete Removal)**: If you wish to permanently destroy the container, installed tools, credentials, and configuration, use the `ancli` menu and press `u`, then select `[3] Completely uninstall AnCLI`. The menu prints the supported one-shot flow. From a root Android shell, create the purge marker and then uninstall the module from your Magisk, KernelSU, or APatch Manager:
  ```bash
  touch /data/local/tmp/ancli_force_purge
  ```
  The uninstall script consumes the marker, removes the AnCLI data directory, and cleans the dynamic wrappers. Do not reuse the marker for a later upgrade.

## Execution backend

All catalog tools run through the single Ubuntu PRoot backend. This keeps glibc, Python, Node/npm, proxy, DNS, browser hand-off, and app-data behavior consistent across tools.

## Troubleshooting

### TUI tools (mimo / claude / opencode / agy) don't start from `/`

All Node/Bun-based TUIs (mimo, opencode, claude) scan the current directory's file tree at startup. From `/` inside the container, that scan covers the entire rootfs plus the `/data` and `/sdcard` bindings — hundreds of thousands of files — which hangs or silently aborts the TUI. Python (aider) and Go/Rust tools are affected to a lesser degree.

The wrappers redirect launches from `/` or another path that is not visible inside the container to the container HOME (`/root`). This keeps TUI startup focused on a small working directory. The wrapper binds every common Android tree that exists in the current root namespace; if that namespace has no `/sdcard` alias, it skips that alias and continues with `/storage`, `/mnt`, `/data`, and the other available trees.

The dsh directory picker therefore starts at `/root` when launched from `/`. Dot directories such as `.dsh` and `.agents` are tool state; enable hidden files to see them. Set `default_cwd` to `/sdcard` or `/sdcard/code` when phone storage should be the starting location.

Agent commands started from Termius remain active while the Termius session stays alive in the background. If the terminal app is swiped away or force-stopped, the wrapper watchdog sees the original parent disappear and stops the PRoot child. A detached service is not created for interactive tools; use a dedicated service manager for a web-only process if it must survive the terminal app.

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

已知问题：官方 proot（5.4.0）在部分 aarch64 环境上未映射 `faccessat2` syscall（glibc 2.39 的 `bash` `[ -x ]` 依赖它），导致权限检测可能误报 `false`；部分 Android shell 的 `test -x` 也可能误报，因此不要把 `test -x` 当作可执行性证明。影响 jadx 等 Java 工具启动脚本里的 `JAVA_HOME`/`-x` 检测。

**绕法**（mimo 已验证）：
- 用 Python `os.access(path, os.X_OK)`、`command -v` 或直接调用程序判断可执行性。
- Java 工具用显式入口：`exec java -cp /path/jadx-all.jar jadx.cli.JadxCLI "$@"`；压缩包解出的启动文件先 `chmod +x`。
- Termux 的 proot 构建依赖 Android 环境缺少的 `libtalloc` 和 `liblandroid-shmem`，AnCLI 使用自带的 PRoot Ubuntu 后端。

### No update notification in KernelSU/Magisk manager

The manager polls `update.json` (`updateJson` in `module.prop`). If `raw.githubusercontent.com` is unreachable on your network, no update will appear — use a proxy/VPN, or point `updateJson` at a jsDelivr mirror. Also verify the `zipUrl` asset name exactly matches what you uploaded to the GitHub release — a mismatch silently disables OTA.

This is separate from **tool** updates inside the module: the module OTA upgrades AnCLI itself, while the WebUI's 检查更新 / `ancli check` reports newer versions of the AI CLIs you installed. Both need working network + proxy.

### TLS & package-source trade-offs (known)

- Official version checks and installer-script downloads verify TLS certificates first, and fall back to an **unverified** retry only when certificate validation fails (the proot container may ship an incomplete CA store). Downloads of third-party installer scripts then execute them with `bash` — review what you install.
- `customize.sh` installs APT packages with `--allow-unauthenticated` (the rootfs ships with a minimal keyring) and downloads `ubuntu-base` without a checksum. Both are pragmatic trade-offs for a bootstrap that must work offline-first; do not extend this pattern to user-supplied inputs.

## Technical Details

For the execution architecture, dual-injection wrapper mechanism, PRoot configuration, and registry schema, see the [Architecture Document](ARCHITECTURE.md). For technical boundaries and compatibility analysis, see the [Compatibility Dossier](COMPATIBILITY.md).
