## AnCLI v1.1.0 — Module-Based Architecture

### 🚀 What's New

**Architecture Overhaul**
- Packaged as a standard **Magisk/KernelSU/APatch systemless module**
- Install via Manager app (Modules → Install from storage)
- **OTA auto-update** via `updateJson` — Manager notifies you of new versions
- **Boot self-repair** (`service.sh`) — auto-fixes DNS and permissions every boot
- **One-click uninstall** — remove module in Manager, cleanup is automatic

**Security Hardening**
- Command whitelist validation (blocks untrusted registry commands)
- Shell operator blocking (`|`, `&&`, `;`, `` ` ``, `$(`)
- `shlex.quote()` escaping for all injected environment variables
- Path traversal protection for executable names
- Atomic writes for `installed.json` with corruption recovery

**CLI Expansion**
- New commands: `update`, `config`, `list`, `--help`, `--version`
- Interactive menu now supports `[3] Reconfigure env vars`
- Registry fetch with 3-retry mechanism (15s timeout, 2s backoff)
- Richer install metadata (timestamps, env key tracking)

**Infrastructure**
- PRoot upgraded to **v5.4.0** (better Android 15+ compatibility)
- Configurable mirror via `ANCLI_MIRROR` env var
- Idempotent bootstrap (skips APT if dependencies already installed)
- `.gitattributes` enforces LF line endings for all shell/Python scripts

### 📦 Installation

Download `ancli-v1.1.0.zip` below and flash it via your root Manager app.

Or use the CLI bootstrap:
```bash
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```
