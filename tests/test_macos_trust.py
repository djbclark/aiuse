"""Tests for macOS codesign / trust helpers (no real codesign --sign in CI)."""

from __future__ import annotations

import json
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
    stdin: list[str] = []

    def run(argv, **k):
        calls.append(list(argv))
        stdin.append(k.get("input") or "")
        if argv[:2] == ["security", "find-generic-password"] and "-w" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="sekrit-cookie\n", stderr="")
        if argv[:3] == ["security", "dump-keychain", "-a"]:
            return subprocess.CompletedProcess(argv, 0, stdout=_acl_dump(app), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok, msg = fix_codexbar_cache_account(
        "cookie.codex",
        dry_run=False,
        keychain_password="pw-kc",
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
    # Neither the secret nor the keychain password is ever on argv (ps-visible).
    for argv in calls:
        assert not any("sekrit" in a or "pw-kc" in a for a in argv), argv
    add = next(line for line in stdin if line.startswith('"add-generic-password"'))
    assert calls[stdin.index(add)] == ["security", "-i"]
    assert f'"-T" "{app}"' in add
    assert f'"-T" "{cli}"' in add
    assert '"-T" "/usr/bin/security"' in add
    assert '"-w" "sekrit-cookie"' in add
    part = next(line for line in stdin if line.startswith('"set-generic-password-partition-list"'))
    assert '"-k" "pw-kc"' in part
    assert '"apple-tool:,apple:,teamid:Y5PE65HELJ"' in part


def _acl_dump(app, *, any_app: bool = False) -> str:
    """``dump-keychain -a`` text for one CodexBar Cache item (format from macOS 27.0.1)."""
    apps = (
        "        applications: <null>\n"
        if any_app
        else (
            "        applications (3):\n"
            f"            0: {app} (OK)\n"
            '                requirement: identifier "com.steipete.codexbar" and anchor apple generic and '
            'certificate leaf[subject.OU] = "Y5PE65HELJ"\n'
            "            1: /Applications/Gone.app (status -67068)\n"
            '                requirement: cdhash H"8e5d00"\n'
            "            2: /usr/bin/security (OK)\n"
            '                requirement: identifier "com.apple.security" and anchor apple\n'
        )
    )
    return (
        'keychain: "/Users/me/Library/Keychains/login.keychain-db"\n'
        "version: 512\n"
        'class: "genp"\n'
        "attributes:\n"
        '    0x00000007 <blob>="CodexBar Cache"\n'
        '    "acct"<blob>="cookie.codex"\n'
        '    "svce"<blob>="com.steipete.codexbar.cache"\n'
        "access: 3 entries\n"
        "    entry 0:\n"
        "        authorizations (1): encrypt\n"
        "        don't-require-password\n"
        "        description: CodexBar Cache\n"
        "        applications: <null>\n"
        "    entry 1:\n"
        "        authorizations (6): decrypt derive export_clear export_wrapped mac sign\n"
        "        don't-require-password\n"
        "        description: CodexBar Cache\n"
        f"{apps}"
        "    entry 2:\n"
        "        authorizations (1): partition_id\n"
        "        don't-require-password\n"
        "        description: apple-tool:, apple:, teamid:Y5PE65HELJ\n"
        "        applications: <null>\n"
    )


def _codexbar_paths(tmp_path):
    app = tmp_path / "CodexBar.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    cli = tmp_path / "CodexBarCLI"
    cli.write_text("x", encoding="utf-8")
    cli.chmod(0o755)
    kc = tmp_path / "login.keychain-db"
    kc.write_text("fake", encoding="utf-8")
    return app, cli, kc


def test_fix_codexbar_cache_refuses_unsendable_secret_before_delete(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    for secret in ("line1\nline2", "x" * 5000):
        calls: list[list[str]] = []

        def run(argv, _secret=secret, _calls=calls, **_k):
            _calls.append(list(argv))
            if "find-generic-password" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=_secret + "\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        ok, msg = fix_codexbar_cache_account(
            "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
        )
        assert not ok
        assert "not rewritten" in msg
        assert not any("delete-generic-password" in c for c in calls)
        assert secret not in msg


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


def _failing_add_runner(app, *, add_rcs, any_app=False, dump_rc=0):
    """Stub ``security``: read ok, delete ok, ``security -i`` adds fail per ``add_rcs`` in order."""
    calls: list[tuple[list[str], str]] = []
    rcs = list(add_rcs)

    def run(argv, **k):
        line = k.get("input") or ""
        calls.append((list(argv), line))
        if "find-generic-password" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="sekrit-cookie\n", stderr="")
        if argv[:3] == ["security", "dump-keychain", "-a"]:
            return subprocess.CompletedProcess(argv, dump_rc, stdout=_acl_dump(app, any_app=any_app), stderr="")
        if argv == ["security", "-i"] and line.startswith('"add-generic-password"'):
            rc = rcs.pop(0) if rcs else 0
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr="" if rc == 0 else f"returned {rc}")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return run, calls


def _adds(calls):
    return [line for argv, line in calls if line.startswith('"add-generic-password"')]


def test_fix_codexbar_cache_add_failure_rolls_back_from_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    run, calls = _failing_add_runner(app, add_rcs=[1, 0])
    snapdir = tmp_path / "snaps"
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex",
        app_path=app,
        cli_path=cli,
        team_id="Y5PE65HELJ",
        keychain=kc,
        keychain_password="pw-kc",
        run_fn=run,
        snapshot_dir=snapdir,
    )
    assert not ok
    assert "add failed" in msg and "rolled back" in msg and "partition list restored" in msg
    assert "sekrit" not in msg and "pw-kc" not in msg
    # Order: snapshot (dump) before delete, then the failed add, then the rollback add.
    kinds = [argv[1] if argv[1] != "-i" else line.split()[0].strip('"') for argv, line in calls]
    assert kinds.index("dump-keychain") < kinds.index("delete-generic-password")
    rollback = _adds(calls)[1]
    assert f'"-T" "{app}"' in rollback
    assert '"-T" "/usr/bin/security"' in rollback
    assert "Gone.app" not in rollback  # trusted path that no longer exists is dropped
    assert str(cli) not in rollback  # the snapshot never trusted the CLI
    assert '"-l" "CodexBar Cache"' in rollback
    part = next(line for _a, line in calls if line.startswith('"set-generic-password-partition-list"'))
    assert '"apple-tool:,apple:,teamid:Y5PE65HELJ"' in part
    # Snapshot file: 0600, metadata only, with the event log.
    (snap,) = list(snapdir.iterdir())
    assert snap.stat().st_mode & 0o777 == 0o600
    text = snap.read_text()
    assert "sekrit" not in text and "pw-kc" not in text
    data = json.loads(text)
    assert data["acl"]["partitions"] == ["apple-tool:", "apple:", "teamid:Y5PE65HELJ"]
    events = [e["event"] for e in data["events"]]
    assert events[0] == "deleted"
    assert events[1].startswith("add failed")
    assert events[2].startswith("rolled back")


def test_fix_codexbar_cache_rollback_restores_any_app_exactly(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    run, calls = _failing_add_runner(app, add_rcs=[1, 0], any_app=True)
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
    )
    assert not ok and "rolled back" in msg
    assert '"-A"' in _adds(calls)[1]
    assert "partition list not restored (no keychain password)" in msg


def test_fix_codexbar_cache_rollback_falls_back_to_plain_add(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    run, calls = _failing_add_runner(app, add_rcs=[1, 1, 0])
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
    )
    assert not ok
    assert "re-added with default ACL" in msg
    plain = _adds(calls)[2]
    assert '"-T"' not in plain and '"-A"' not in plain


def test_fix_codexbar_cache_reports_lost_item_when_everything_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    run, _calls = _failing_add_runner(app, add_rcs=[1, 1, 1])
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
    )
    assert not ok
    assert "ROLLBACK FAILED" in msg and "sign in to CodexBar again" in msg


def test_fix_codexbar_cache_duplicate_on_rollback_is_not_reported_as_lost(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    run, calls = _failing_add_runner(app, add_rcs=[1, 45])
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
    )
    assert not ok
    assert "item exists again" in msg
    assert len(_adds(calls)) == 2


def test_fix_codexbar_cache_no_snapshot_no_delete(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)
    run, calls = _failing_add_runner(app, add_rcs=[], dump_rc=152)
    ok, msg = fix_codexbar_cache_account(
        "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
    )
    assert not ok
    assert "ACL snapshot failed" in msg and "locked" in msg
    assert not any("delete-generic-password" in argv for argv, _line in calls)


def test_fix_codexbar_cache_read_failure_is_classified(monkeypatch, tmp_path):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    app, cli, kc = _codexbar_paths(tmp_path)

    def run(argv, **_k):
        return subprocess.CompletedProcess(argv, 152, stdout="", stderr="")

    ok, msg = fix_codexbar_cache_account(
        "cookie.codex", app_path=app, cli_path=cli, team_id="T", keychain=kc, run_fn=run
    )
    assert not ok
    assert "keychain locked" in msg and "not a credential problem" in msg


# ── aiuse trust audit (read-only) ───────────────────────────────────────────

_AUDIT_DUMP = (
    'keychain: "/Users/me/Library/Keychains/login.keychain-db"\n'
    'class: "genp"\n'
    "attributes:\n"
    '    "acct"<blob>="meta"\n'
    '    "svce"<blob>="ai.meta.dev.credentials"\n'
    "access: 2 entries\n"
    "    entry 0:\n"
    "        authorizations (6): decrypt derive export_clear export_wrapped mac sign\n"
    "        don't-require-password\n"
    "        description: ai.meta.dev.credentials\n"
    "        applications (2):\n"
    "            0: /usr/bin/security (OK)\n"
    '                requirement: identifier "com.apple.security" and anchor apple\n'
    "            1: /Users/me/.local/bin/muse-bin-1.4.2 (status -67068)\n"
    '                requirement: identifier "muse-arm64" and anchor apple generic and '
    'certificate leaf[subject.OU] = "V9WTTPBFK9"\n'
    "    entry 1:\n"
    "        authorizations (1): partition_id\n"
    "        don't-require-password\n"
    "        description: teamid:V9WTTPBFK9, apple-tool:\n"
    "        applications: <null>\n"
    'class: "genp"\n'
    "attributes:\n"
    '    "acct"<blob>="cookie.codex"\n'
    '    "svce"<blob>="com.steipete.codexbar.cache"\n'
    "access: 2 entries\n"
    "    entry 0:\n"
    "        authorizations (6): decrypt derive export_clear export_wrapped mac sign\n"
    "        don't-require-password\n"
    "        description: CodexBar Cache\n"
    "        applications (1):\n"
    "            0: /Users/me/src/CodexBar/CodexBar.app (OK)\n"
    '                requirement: cdhash H"8e5d00aa"\n'
    "    entry 1:\n"
    "        authorizations (1): partition_id\n"
    "        don't-require-password\n"
    "        description: apple-tool:, apple:, teamid:Y5PE65HELJ, cdhash:8e5d00aa\n"
    "        applications: <null>\n"
    'class: "genp"\n'
    "attributes:\n"
    '    "acct"<blob>="Cursor Key"\n'
    '    "svce"<blob>="Cursor Safe Storage"\n'
    "access: 1 entries\n"
    "    entry 0:\n"
    "        authorizations (6): decrypt derive export_clear export_wrapped mac sign\n"
    "        don't-require-password\n"
    "        description: Cursor Safe Storage\n"
    "        applications: <null>\n"
)


def _audit_runner(calls, *, rc=0, stdout=_AUDIT_DUMP):
    def run(argv, **k):
        calls.append((list(argv), k.get("input")))
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")

    return run


def test_trust_audit_flags_cdhash_security_stale_and_any_app(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    calls: list = []
    assert run_trust_command(["audit"], config={}, run_fn=_audit_runner(calls)) == 0
    out = capsys.readouterr().out
    # Read-only: exactly one metadata dump, never -d/-w, nothing on stdin, no writes.
    assert calls == [(["security", "dump-keychain", "-a", calls[0][0][3]], None)]
    assert "nothing changed" in out
    assert "ai.meta.dev.credentials acct='meta'" in out
    assert "partition list: teamid:V9WTTPBFK9, apple-tool:" in out
    assert "SECURITY-TOOL: trusts /usr/bin/security" in out
    assert "STALE: /Users/me/.local/bin/muse-bin-1.4.2 (status -67068)" in out
    assert "CDHASH: /Users/me/src/CodexBar/CodexBar.app is pinned" in out
    assert "CDHASH-PARTITION: partition list holds cdhash:8e5d00aa" in out
    assert "ANY-APP" in out
    assert "gh:github.com: not in this keychain" in out
    assert "3 item(s) flagged" in out


def test_trust_audit_json_rows(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    calls: list = []
    assert run_trust_command(["audit", "--json"], config={}, run_fn=_audit_runner(calls)) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {r["service"]: r for r in payload["items"]}
    muse = rows["ai.meta.dev.credentials"]
    assert muse["found"] is True
    assert [a["requirement_kind"] for a in muse["decrypt_apps"]] == ["apple", "team"]
    assert muse["partitions"] == ["teamid:V9WTTPBFK9", "apple-tool:"]
    assert rows["Cursor Safe Storage"]["allows_any_app"] is True
    assert rows["gh:github.com"] == {"service": "gh:github.com", "purpose": "GitHub CLI token", "found": False}


def test_trust_audit_extra_service_and_locked_keychain(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    calls: list = []
    assert run_trust_command(["audit", "--service", "other.svc"], config={}, run_fn=_audit_runner(calls)) == 0
    assert "other.svc: not in this keychain (requested with --service)" in capsys.readouterr().out
    assert run_trust_command(["audit"], config={}, run_fn=_audit_runner(calls, rc=152, stdout="")) == 1
    out = capsys.readouterr().out
    assert "keychain locked" in out and "not a credential problem" in out


def test_trust_audit_rejects_unknown_argument(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.macos_trust.is_darwin", lambda: True)
    assert run_trust_command(["audit", "--fix"], config={}) == 2
    assert "unknown argument" in capsys.readouterr().out
