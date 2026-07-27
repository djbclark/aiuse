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
    format_codesign_summary,
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
