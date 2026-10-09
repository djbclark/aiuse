"""Keychain helpers (issue #30). Every test stubs ``security``; none touches a real keychain."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aiuse.keychain import (
    DENIED,
    ERROR,
    LOCKED,
    MISSING,
    OK,
    PROMPT,
    UNAVAILABLE,
    KeychainResult,
    classify_exit,
    read_generic_password,
)
from aiuse.models import AccountUsage, BillingKind, utcnow


@pytest.fixture
def fake_security(tmp_path, monkeypatch):
    """Put a stub ``security`` first on PATH; behaviour comes from env vars.

    FAKE_SECURITY_RC: exit status. FAKE_SECURITY_OUT / _ERR: stdout / stderr.
    FAKE_SECURITY_SLEEP: seconds to sleep first (simulates a SecurityAgent prompt).
    Each call appends its argv to FAKE_SECURITY_LOG.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "security-calls.log"
    script = bindir / "security"
    script.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_SECURITY_LOG"\n'
        'if [ -n "$FAKE_SECURITY_SLEEP" ]; then sleep "$FAKE_SECURITY_SLEEP"; fi\n'
        'if [ -n "$FAKE_SECURITY_OUT" ]; then printf "%s\\n" "$FAKE_SECURITY_OUT"; fi\n'
        'if [ -n "$FAKE_SECURITY_ERR" ]; then printf "%s\\n" "$FAKE_SECURITY_ERR" >&2; fi\n'
        'exit "${FAKE_SECURITY_RC:-0}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_SECURITY_LOG", str(log))
    for name in ("FAKE_SECURITY_RC", "FAKE_SECURITY_OUT", "FAKE_SECURITY_ERR", "FAKE_SECURITY_SLEEP"):
        monkeypatch.delenv(name, raising=False)
    return log


# ── exit classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rc", "stderr", "expected"),
    [
        (0, "", OK),
        (44, "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.", MISSING),
        (152, "", LOCKED),
        (36, "User interaction is not allowed.", LOCKED),
        (1, "MacOS error: -60008", LOCKED),
        (128, "", DENIED),
        (51, "", DENIED),
        (1, "something else", ERROR),
    ],
)
def test_classify_exit(rc, stderr, expected):
    assert classify_exit(rc, stderr) == expected


def test_classify_timeout_is_prompt():
    assert classify_exit(None, timed_out=True) == PROMPT


def test_locked_message_is_actionable_and_never_invalid():
    msg = KeychainResult(status=LOCKED, returncode=152).message("Muse")
    assert "locked" in msg
    assert "152" in msg
    assert "retry after unlock" in msg
    assert "invalid" not in msg.lower()


def test_each_status_has_a_distinct_action():
    actions = {KeychainResult(status=s).action() for s in (MISSING, LOCKED, PROMPT, DENIED, UNAVAILABLE, ERROR)}
    assert len(actions) == 6


# ── read_generic_password against a stub binary ─────────────────────────────


def test_read_ok_returns_secret_but_result_holds_none(fake_security, monkeypatch):
    monkeypatch.setenv("FAKE_SECURITY_OUT", "s3cret")
    result, secret = read_generic_password("svc", "acct")
    assert result.status == OK
    assert secret == "s3cret"
    assert "s3cret" not in repr(result)
    assert fake_security.read_text().split() == ["find-generic-password", "-s", "svc", "-a", "acct", "-w"]


@pytest.mark.parametrize(("rc", "expected"), [(44, MISSING), (152, LOCKED), (36, LOCKED), (2, ERROR)])
def test_read_failure_is_classified(fake_security, monkeypatch, rc, expected):
    monkeypatch.setenv("FAKE_SECURITY_RC", str(rc))
    monkeypatch.setenv("FAKE_SECURITY_OUT", "should-not-be-returned")
    result, secret = read_generic_password("svc", "acct")
    assert result.status == expected
    assert result.returncode == rc
    assert secret is None


def test_read_timeout_is_prompt(fake_security, monkeypatch):
    monkeypatch.setenv("FAKE_SECURITY_SLEEP", "5")
    result, secret = read_generic_password("svc", "acct", timeout=0.3)
    assert result.status == PROMPT
    assert secret is None
    assert "timed out" in result.message("x")


def test_read_without_security_binary_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    result, secret = read_generic_password("svc", "acct")
    assert result.status == UNAVAILABLE
    assert secret is None


# ── Muse collector ───────────────────────────────────────────────────────────


@pytest.fixture
def muse_local(monkeypatch):
    """Reset the Muse plan cache. Tests then call :func:`_allow_local` in their body:
    pytest re-sets PYTEST_CURRENT_TEST at each phase, so a fixture cannot drop it."""
    from aiuse.collectors import muse as muse_mod

    monkeypatch.setattr(muse_mod, "_subs_cache", {"at": 0.0, "payload": None, "keychain": None})
    return muse_mod


def _allow_local(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def _proc(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["security"], rc, stdout=out, stderr=err)


def test_muse_token_reader_classifies_locked():
    from aiuse.collectors import muse as muse_mod

    result, token = muse_mod._read_muse_keychain_access_token(run_fn=lambda *_a, **_k: _proc(152))
    assert result.status == LOCKED
    assert token is None


def test_muse_token_reader_ok_and_bad_payload():
    from aiuse.collectors import muse as muse_mod

    good = json.dumps({"access_token": " tok "})
    result, token = muse_mod._read_muse_keychain_access_token(run_fn=lambda *_a, **_k: _proc(0, good + "\n"))
    assert result.ok
    assert token == "tok"
    result, token = muse_mod._read_muse_keychain_access_token(run_fn=lambda *_a, **_k: _proc(0, "not json"))
    assert result.status == MISSING
    assert token is None


def test_muse_locked_keychain_marks_rows_and_skips_meta(muse_local, monkeypatch):
    _allow_local(monkeypatch)
    calls = {"read": 0, "fetch": 0}

    def read(**_k):
        calls["read"] += 1
        return KeychainResult(status=LOCKED, returncode=152), None

    def fetch(*_a, **_k):
        calls["fetch"] += 1
        return {}

    monkeypatch.setattr(muse_local, "_read_muse_keychain_access_token", read)
    monkeypatch.setattr(muse_local, "_fetch_muse_code_key", fetch)
    row = AccountUsage(source="muse", provider="muse", billing_kind=BillingKind.PAYG_API)
    merged = muse_local._merge_subscription_windows([row], 5.0, allow_local=True)
    assert calls == {"read": 1, "fetch": 0}
    status = merged[0].credential_status
    assert status is not None
    assert status["status"] == LOCKED
    assert status["exit_code"] == 152
    assert status["item"] == "ai.meta.dev.credentials"
    note = merged[0].notes[-1]
    assert "locked" in note and "invalid" not in note.lower()
    assert merged[0].to_dict()["credential_status"]["status"] == LOCKED
    # A second collect inside the cache window reuses the classified miss.
    again = AccountUsage(source="muse", provider="muse", billing_kind=BillingKind.PAYG_API)
    muse_local._merge_subscription_windows([again], 5.0, allow_local=True)
    assert calls["read"] == 1
    assert again.credential_status is not None and again.credential_status["status"] == LOCKED


def test_muse_prompt_is_backed_off_for_an_hour(muse_local, monkeypatch):
    _allow_local(monkeypatch)
    reads = []
    monkeypatch.setattr(
        muse_local,
        "_read_muse_keychain_access_token",
        lambda **_k: reads.append(1) or (KeychainResult(status=PROMPT), None),
    )
    clock = {"now": 1000.0}
    monkeypatch.setattr(muse_local.time, "monotonic", lambda: clock["now"])
    muse_local._local_login_plan(5.0)
    clock["now"] += muse_local._SUBS_CACHE_TTL_S + 1  # past the normal TTL
    _w, _n, issue = muse_local._local_login_plan(5.0)
    assert len(reads) == 1
    assert issue is not None and issue.status == PROMPT
    clock["now"] += muse_local._PROMPT_BACKOFF_S
    muse_local._local_login_plan(5.0)
    assert len(reads) == 2


def test_muse_ok_read_sets_no_credential_status(muse_local, monkeypatch):
    _allow_local(monkeypatch)
    monkeypatch.setattr(
        muse_local, "_read_muse_keychain_access_token", lambda **_k: (KeychainResult(status=OK, returncode=0), "tok")
    )
    monkeypatch.setattr(
        muse_local,
        "_fetch_muse_code_key",
        lambda *_a, **_k: {"subs_usage": {"weekly": {"used_percent": 10}}},
    )
    row = AccountUsage(source="muse", provider="muse", billing_kind=BillingKind.PAYG_API)
    merged = muse_local._merge_subscription_windows([row], 5.0, allow_local=True)
    assert merged[0].credential_status is None
    assert "credential_status" not in merged[0].to_dict()
    assert [w.label for w in merged[0].windows] == ["Muse weekly"]


# ── --available ──────────────────────────────────────────────────────────────


def _snapshot_with_locked_muse() -> dict:
    now = utcnow()
    return {
        "collected_at": now.isoformat(),
        "accounts": [
            {
                "provider": "muse",
                "account": None,
                "source": "muse",
                "billing_kind": "payg_api",
                "windows": [],
                "notes": [],
                "credential_status": KeychainResult(status=LOCKED, returncode=152).to_dict(
                    item="ai.meta.dev.credentials"
                ),
            },
            {
                "provider": "codex",
                "account": "a@example.com",
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "windows": [{"label": "Codex weekly", "used_percent": 20.0, "remaining_percent": 80.0}],
            },
        ],
    }


def test_credential_issues_lists_rows_without_pools():
    from aiuse.analysis.selfdescribe import credential_issue_line, credential_issues

    issues = credential_issues(_snapshot_with_locked_muse())
    assert [(i["provider"], i["status"]) for i in issues] == [("muse", LOCKED)]
    line = credential_issue_line(issues[0])
    assert line.startswith("credential: muse ai.meta.dev.credentials keychain LOCKED (security exit 152)")
    assert "invalid" not in line.lower()


def test_cli_available_reports_credential_issues(tmp_path: Path, monkeypatch, capsys):
    from aiuse.analysis import history
    from aiuse.cli import main

    cache = tmp_path / "snapshots"
    cache.mkdir()
    (cache / "latest.json").write_text(json.dumps(_snapshot_with_locked_muse()))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)

    assert main(["--available", "--json", "-q"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [p["provider"] for p in payload["available"]] == ["codex"]
    assert payload["credential_issues"][0]["status"] == LOCKED
    assert "credential_issues" in payload["semantics"]

    assert main(["--available", "-q"]) == 0
    err = capsys.readouterr().err
    assert "credential: muse ai.meta.dev.credentials keychain LOCKED" in err
