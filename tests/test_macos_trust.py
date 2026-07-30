"""Tests for macOS codesign / trust helpers (no real codesign --sign in CI)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from aiuse import cli
from aiuse.config import validate_config
from aiuse.macos_trust import (
    DEFAULT_CODESIGN_IDENTITY,
    CodesignInfo,
    collect_status,
    configured_identity,
    doctor_caut_codesign_lines,
    fix_codexbar_cache_account,
    fix_codexbar_cache_all,
    format_codesign_summary,
    list_codexbar_cache_accounts,
    parse_codesign_output,
    resolve_caut_binary,
    run_trust_command,
    sign_caut,
)

ADHOC_DUMP = """\
Executable=/Users/me/.cargo/bin/caut
Identifier=caut-719d6558e209b6e9
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=39352 flags=0x20002(adhoc,linker-signed) hashes=1226+0 location=embedded
Signature=adhoc
TeamIdentifier=not set
"""

STABLE_DUMP = """\
Executable=/Users/me/.cargo/bin/caut
Identifier=caut
Authority=aiuse-local-codesign
TeamIdentifier=not set
Signature=local
"""


def test_parse_codesign_adhoc():
    fields = parse_codesign_output(ADHOC_DUMP)
    assert fields["adhoc"] is True
    assert fields["signed"] is True
    assert fields["authority"] is None
    assert "adhoc" in (fields["flags"] or "")


def test_parse_codesign_stable():
    fields = parse_codesign_output(STABLE_DUMP)
    assert fields["adhoc"] is False
    assert fields["signed"] is True
    assert fields["authority"] == "aiuse-local-codesign"


def test_configured_identity_precedence():
    assert configured_identity({}, env={}) == DEFAULT_CODESIGN_IDENTITY
    assert configured_identity({"macos": {"codesign_identity": "from-toml"}}, env={}) == "from-toml"
    assert (
        configured_identity(
            {"macos": {"codesign_identity": "from-toml"}},
            env={"AIUSE_CODESIGN_IDENTITY": "from-env"},
        )
        == "from-env"
    )


def test_validate_config_accepts_macos_codesign_identity():
    assert validate_config({"macos": {"codesign_identity": "aiuse-local-codesign"}}) == []
    issues = validate_config({"macos": {"nope": 1, "codesign_identity": "  "}})
    text = "\n".join(issues)
    assert "unknown macos key" in text
    assert "codesign_identity must be a non-empty" in text


def test_resolve_caut_binary_follows_symlink(tmp_path):
    real = tmp_path / "real-caut"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    real.chmod(0o755)
    link = tmp_path / "caut"
    link.symlink_to(real)

    def which(cmd: str) -> str | None:
        return str(link) if cmd == "caut" else None

    resolved = resolve_caut_binary(which_fn=which)
    assert resolved == real.resolve()


def test_format_codesign_summary_adhoc():
    info = CodesignInfo(
        path=Path("/tmp/caut"),
        exists=True,
        adhoc=True,
        signed=True,
        flags="adhoc,linker-signed",
    )
    lines = format_codesign_summary(info, label="caut")
    assert any("adhoc" in line for line in lines)


def test_doctor_lines_warn_when_adhoc(monkeypatch, tmp_path):
    binary = tmp_path / "caut"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)

    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    monkeypatch.setattr(
        "aiuse.macos_trust.resolve_caut_binary",
        lambda **_k: binary,
    )

    def fake_codesign(path, **_k):
        return CodesignInfo(path=path, exists=True, adhoc=True, signed=True)

    monkeypatch.setattr("aiuse.macos_trust.codesign_display", fake_codesign)
    lines = doctor_caut_codesign_lines({"collectors": {"caut": {"enabled": True}}})
    text = "\n".join(lines)
    assert "WARN" in text
    assert "aiuse trust setup" in text


def test_doctor_lines_quiet_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    lines = doctor_caut_codesign_lines({}, collector_enabled=False)
    assert lines == []


def test_doctor_lines_ok_when_stable(monkeypatch, tmp_path):
    binary = tmp_path / "caut"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    monkeypatch.setattr("aiuse.macos_trust.resolve_caut_binary", lambda **_k: binary)
    monkeypatch.setattr(
        "aiuse.macos_trust.codesign_display",
        lambda path, **_k: CodesignInfo(
            path=path,
            exists=True,
            adhoc=False,
            signed=True,
            authority="aiuse-local-codesign",
        ),
    )
    lines = doctor_caut_codesign_lines({}, collector_enabled=True)
    assert any("ok" in line and "stable" in line for line in lines)


def test_collect_status_non_darwin(monkeypatch):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: False)
    st = collect_status({})
    assert any("not macOS" in line for line in st.lines)


def test_caut_next_steps_footer_adhoc():
    from aiuse.macos_trust import caut_next_steps_footer

    lines = caut_next_steps_footer(
        identity="aiuse-local-codesign",
        identity_present=True,
        caut_path=Path("/tmp/caut"),
        caut_adhoc=True,
    )
    text = "\n".join(lines)
    assert "sign-caut" in text
    assert "probe" in text


def test_ensure_identity_opens_keychain_when_missing(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    monkeypatch.setattr("aiuse.macos_trust.identity_available", lambda *_a, **_k: False)
    opened: list[str] = []

    def fake_open(**_k):
        opened.append("yes")
        return 'Opened "Keychain Access"'

    monkeypatch.setattr("aiuse.macos_trust.try_open_keychain_access", fake_open)
    assert run_trust_command(["ensure-identity"], config={}) == 0
    assert opened
    assert "Create a stable Code Signing" in capsys.readouterr().out


def test_run_trust_status_and_help(capsys, monkeypatch):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: False)
    assert run_trust_command(["status"], config={}) == 0
    assert run_trust_command(["help"], config={}) == 0
    out = capsys.readouterr().out
    assert "aiuse trust" in out or "Always Allow" in out or "status" in out


def test_run_trust_unknown_command(capsys, monkeypatch):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: False)
    assert run_trust_command(["nope"], config={}) == 2
    assert "unknown trust command" in capsys.readouterr().out


def test_cli_trust_subcommand(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: False)
    assert cli.main(["trust", "status"]) == 0
    out = capsys.readouterr().out
    assert "not macOS" in out or "aiuse trust" in out


def test_cli_trust_help(monkeypatch, capsys):
    assert cli.main(["trust", "--help"]) == 0
    out = capsys.readouterr().out
    assert "sign-caut" in out
    assert "Always Allow" in out or "codesign" in out


def test_sign_caut_non_darwin(monkeypatch):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: False)
    ok, msg = sign_caut("id")
    assert ok is False
    assert "macOS" in msg


def test_sign_caut_invokes_codesign(monkeypatch, tmp_path):
    binary = tmp_path / "caut"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)

    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok, msg = sign_caut("aiuse-local-codesign", binary, run_fn=run)
    assert ok is True
    assert "signed" in msg
    assert calls
    assert calls[0][0] == "codesign"
    assert "--force" in calls[0]
    assert str(binary.resolve()) in calls[0]


def test_list_codexbar_cache_accounts_parses_dump(monkeypatch):
    dump = """
keychain: "/Users/me/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="cookie.codex"
    "svce"<blob>="com.steipete.codexbar.cache"
keychain: "/Users/me/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="oauth.claude"
    "svce"<blob>="com.steipete.codexbar.cache"
"""
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)

    def run(argv, **_k):
        return subprocess.CompletedProcess(argv, 0, stdout=dump, stderr="")

    accts = list_codexbar_cache_accounts(run_fn=run)
    assert accts == ["cookie.codex", "oauth.claude"]


def test_fix_codexbar_cache_account_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app = tmp_path / "CodexBar.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "CodexBar").write_text("x", encoding="utf-8")
    cli = tmp_path / "CodexBarCLI"
    cli.write_text("x", encoding="utf-8")
    cli.chmod(0o755)
    kc = tmp_path / "login.keychain-db"
    kc.write_text("fake", encoding="utf-8")
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex",
        dry_run=True,
        app_path=app,
        cli_path=cli,
        keychain=kc,
    )
    assert ok
    assert "dry-run" in msg
    assert "cookie.codex" in msg


def test_fix_codexbar_cache_account_rewrites(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app = tmp_path / "CodexBar.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    cli = tmp_path / "CodexBarCLI"
    cli.write_text("x", encoding="utf-8")
    cli.chmod(0o755)
    kc = tmp_path / "login.keychain-db"
    kc.write_text("fake", encoding="utf-8")
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(list(argv))
        if argv[:2] == ["security", "find-generic-password"] and "-w" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="sekrit-cookie\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok, msg = fix_codexbar_cache_account(
        "cookie.codex",
        dry_run=False,
        keychain_password="pw",
        app_path=app,
        cli_path=cli,
        team_id="Y5PE65HELJ",
        keychain=kc,
        run_fn=run,
    )
    assert ok
    assert "rewrote" in msg
    # Secret must not appear in user-facing message
    assert "sekrit" not in msg
    # security calls: find -w, delete, add, partition-list
    assert any("find-generic-password" in c for c in calls)
    assert any("delete-generic-password" in c for c in calls)
    assert any("add-generic-password" in c for c in calls)
    add = next(c for c in calls if "add-generic-password" in c)
    assert str(app) in add
    assert str(cli) in add
    assert "/usr/bin/security" in add
    assert any("set-generic-password-partition-list" in c for c in calls)


def test_fix_codexbar_cache_all_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    monkeypatch.setattr(
        "aiuse.macos_trust.resolve_codexbar_app",
        lambda: tmp_path / "CodexBar.app",
    )
    app = tmp_path / "CodexBar.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    cli = tmp_path / "cli"
    cli.write_text("x", encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setattr("aiuse.macos_trust.resolve_codexbar_cli", lambda **_k: cli)
    # Dry-run describes its prospective change without needing a host keychain.
    monkeypatch.setattr("aiuse.macos_trust.login_keychain_path", lambda: tmp_path / "missing.keychain-db")
    monkeypatch.setattr("aiuse.macos_trust.list_codexbar_cache_accounts", lambda **_k: ["cookie.codex"])
    monkeypatch.setattr("aiuse.macos_trust.codexbar_team_id", lambda **_k: "Y5PE65HELJ")
    fails, lines = fix_codexbar_cache_all(dry_run=True)
    assert fails == 0
    text = "\n".join(lines)
    assert "dry-run" in text
    assert "cookie.codex" in text


def test_cli_fix_codexbar_cache_dry(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    monkeypatch.setattr(
        "aiuse.macos_trust.fix_codexbar_cache_all",
        lambda **_k: (0, ["dry-run ok"]),
    )
    assert run_trust_command(["fix-codexbar-cache", "--dry-run"], config={}) == 0
    assert "dry-run ok" in capsys.readouterr().out


def test_diagnose_includes_trust_hint_on_darwin(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys, "platform", "darwin", raising=False)
    # Force platform check inside diagnose via sys.platform
    monkeypatch.setattr("sys.platform", "darwin")

    binary = tmp_path / "caut"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)

    monkeypatch.setattr(cli, "which", lambda cmd: str(binary) if cmd == "caut" else "/usr/bin/fake")
    monkeypatch.setattr(
        cli,
        "probe_tool_version",
        lambda cmd, _va, **_k: (True, f"{cmd}-probe"),
    )
    monkeypatch.setattr(cli, "_http_probe_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "_openusage_http_ok", lambda **_k: True)

    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    monkeypatch.setattr("aiuse.macos_trust.resolve_caut_binary", lambda **_k: binary)
    monkeypatch.setattr(
        "aiuse.macos_trust.codesign_display",
        lambda path, **_k: CodesignInfo(path=path, exists=True, adhoc=True, signed=True),
    )

    code, lines = cli.diagnose(
        {
            "collectors": {
                "cswap": {"enabled": True},
                "codexbar": {"enabled": True},
                "caut": {"enabled": True},
                "openusage": {"enabled": True},
                "tokscale": {"enabled": True},
            }
        },
        which_fn=lambda cmd: str(binary) if cmd == "caut" else "/usr/bin/fake",
        probe=False,
    )
    text = "\n".join(lines)
    assert code == 0  # soft warn, not hard failure
    assert "WARN" in text or "ad-hoc" in text or "adhoc" in text
    assert "aiuse trust setup" in text
