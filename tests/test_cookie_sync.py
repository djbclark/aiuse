from __future__ import annotations

import base64
import json
import time
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

import pytest

from aiuse import cookie_sync as cs


def _jwt(claims: dict) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def _cookie(name: str, value: str, domain: str, expires: int | None = None) -> Cookie:
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        True,
        domain.startswith("."),
        "/",
        True,
        True,
        expires,
        expires is None,
        None,
        None,
        {},
        False,
    )


def test_cursor_cookie_wraps_user_id_and_token():
    token = _jwt({"sub": "auth0|user_01ABC", "exp": time.time() + 86400})
    header, exp = cs.cursor_cookie_from_token(token + "\n")
    assert header == f"WorkosCursorSessionToken=user_01ABC%3A%3A{token}"
    assert exp > time.time()


def test_cursor_cookie_rejects_expired_and_odd_ids():
    with pytest.raises(cs.CookieSyncError, match="expired"):
        cs.cursor_cookie_from_token(_jwt({"sub": "auth0|user_1", "exp": time.time() - 5}))
    with pytest.raises(cs.CookieSyncError, match="user id"):
        cs.cursor_cookie_from_token(_jwt({"sub": "auth0|bad/../id", "exp": time.time() + 999}))
    with pytest.raises(cs.CookieSyncError):
        cs.cursor_cookie_from_token("not-a-jwt")


def test_grok_header_needs_sso_and_scopes_to_grok_domain():
    jar = CookieJar()
    jar.set_cookie(_cookie("sso", "aaa", ".grok.com", 2000000000))
    jar.set_cookie(_cookie("sso-rw", "bbb", ".grok.com", 1900000000))
    jar.set_cookie(_cookie("other", "x", ".example.com"))
    header, exp = cs.grok_header_from_jar(jar)
    assert header == "sso=aaa; sso-rw=bbb"
    assert exp == 1900000000
    empty = CookieJar()
    empty.set_cookie(_cookie("_ga", "1", ".grok.com"))
    assert cs.grok_header_from_jar(empty) is None


def test_validate_payload_rejects_bad_input():
    good = {"schema": 1, "provider": "grok", "cookie_header": "sso=a; sso-rw=b"}
    assert cs.validate_payload(good) is good
    for bad in (
        {**good, "schema": 2},
        {**good, "provider": "claude"},
        {**good, "cookie_header": "sso=a\r\nX: y"},
        {**good, "cookie_header": ""},
        {**good, "provider": "cursor"},
        {**good, "cookie_header": "foo=bar"},
    ):
        with pytest.raises(cs.CookieSyncError):
            cs.validate_payload(bad)


def test_remote_command_is_quoted_and_provider_restricted():
    cmd = cs.remote_write_command("/var/lib/my drop", "cursor")
    assert "'/var/lib/my drop/cursor.json'" in cmd
    assert cmd.startswith("umask 007 && cat > ")
    with pytest.raises(cs.CookieSyncError):
        cs.remote_write_command("/x", "../etc")


def test_ingest_applies_via_stdin_shreds_and_records_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    drop = tmp_path / "drop"
    drop.mkdir()
    secret = "sso=SECRETVALUE; sso-rw=SECRETVALUE2"
    (drop / "grok.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "provider": "grok",
                "cookie_header": secret,
                "expires_at": "2027-01-01T00:00:00Z",
                "source": "chrome:Default",
            }
        )
    )
    (drop / "cursor.json").write_text("{not json")
    fake = tmp_path / "codexbar"
    out = tmp_path / "stdin.txt"
    fake.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/argv.txt\ncat > {out}\n')
    fake.chmod(0o755)

    results = cs.ingest(drop, codexbar=str(fake))

    assert results["grok"] == "applied"
    assert results["cursor"].startswith("failed")
    assert out.read_text() == secret
    assert "SECRETVALUE" not in (tmp_path / "argv.txt").read_text()
    assert not (drop / "grok.json").exists() and not (drop / "cursor.json").exists()
    status = json.loads(cs.status_path().read_text())
    assert status["grok"]["expires_at"] == "2027-01-01T00:00:00Z"
    assert "SECRETVALUE" not in cs.status_path().read_text()
    assert oct(cs.status_path().stat().st_mode & 0o777) == "0o600"


def test_ingest_missing_dir_is_noop(tmp_path: Path):
    assert cs.ingest(tmp_path / "nope") == {}


def test_push_dry_run_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(
        cs,
        "extract_cursor",
        lambda: {
            "provider": "cursor",
            "cookie_header": "WorkosCursorSessionToken=x",
            "expires_at": "2027-01-01T00:00:00Z",
            "source": "keychain",
        },
    )
    monkeypatch.setattr(
        cs, "extract_grok", lambda profile=None: (_ for _ in ()).throw(cs.CookieSyncError("no session"))
    )
    monkeypatch.setattr(cs, "push_payload", lambda *a, **k: pytest.fail("must not push"))
    rc = cs.run_sync_cookies_command(["push", "--to", "h", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "cursor: extracted" in captured.out and "grok: skipped - no session" in captured.err
    assert "WorkosCursorSessionToken" not in captured.out + captured.err
