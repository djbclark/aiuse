from __future__ import annotations

from http.cookiejar import Cookie, CookieJar
from pathlib import Path

from aiuse import cli
from aiuse.credentials import CredentialError, _cookie_header_for_opencode


def _cookie(name: str, value: str, domain: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def test_cookie_header_is_scoped_to_opencode():
    jar = CookieJar()
    jar.set_cookie(_cookie("session", "good", ".opencode.ai"))
    jar.set_cookie(_cookie("unrelated", "nope", ".example.com"))

    assert _cookie_header_for_opencode(jar) == "session=good"


def test_cookie_header_rejects_missing_opencode_cookie():
    jar = CookieJar()
    jar.set_cookie(_cookie("unrelated", "nope", ".example.com"))

    try:
        _cookie_header_for_opencode(jar)
    except CredentialError as exc:
        assert "no OpenCode cookies" in str(exc)
    else:
        raise AssertionError("unrelated browser cookies must never be accepted")


def test_credential_refresh_dry_run_validates_without_writing(monkeypatch, capsys):
    validated: list[str] = []
    monkeypatch.setattr("aiuse.credentials._chrome_cookie_header", lambda _profile: "session=secret")
    monkeypatch.setattr(
        "aiuse.credentials._validate_opencode_zen_cookie",
        lambda cookie, *, timeout: validated.append(f"{cookie}:{timeout}"),
    )
    monkeypatch.setattr(
        "aiuse.credentials._save_with_secretspec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not save in dry run")),
    )

    code = cli.main(["credential", "refresh", "opencode-zen", "--dry-run", "--timeout", "7"])

    assert code == 0
    assert validated == ["session=secret:7.0"]
    output = capsys.readouterr().out
    assert "Validated" in output
    assert "secret" not in output


def test_credential_refresh_confirms_before_replacing(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.credentials._chrome_cookie_header", lambda _profile: "session=secret")
    monkeypatch.setattr("aiuse.credentials._validate_opencode_zen_cookie", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    monkeypatch.setattr(
        "aiuse.credentials._save_with_secretspec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not save without confirmation")),
    )

    assert cli.main(["credential", "refresh", "opencode-zen"]) == 0
    assert "not changed" in capsys.readouterr().out


def test_credential_refresh_saves_only_after_validation(monkeypatch, capsys, tmp_path):
    events: list[object] = []
    manifest = tmp_path / "secretspec.toml"
    manifest.write_text("[project]\nname = 'test'\n")
    monkeypatch.setattr("aiuse.credentials._chrome_cookie_header", lambda _profile: "session=secret")
    monkeypatch.setattr(
        "aiuse.credentials._validate_opencode_zen_cookie",
        lambda cookie, *, timeout: events.append(("validate", cookie, timeout)),
    )
    monkeypatch.setattr(
        "aiuse.credentials._save_with_secretspec",
        lambda secret, *, manifest, timeout: events.append(("save", secret, manifest, timeout)),
    )

    code = cli.main(
        [
            "credential",
            "refresh",
            "opencode-zen",
            "--yes",
            "--secretspec-file",
            str(manifest),
        ]
    )

    assert code == 0
    assert events == [
        ("validate", "session=secret", 10.0),
        ("save", "session=secret", Path(manifest), 10.0),
    ]
    output = capsys.readouterr().out
    assert "saved it to SecretSpec" in output
    assert "secret" not in output


def test_credential_refresh_creates_default_manifest_after_validation(monkeypatch, tmp_path):
    manifest = tmp_path / "aiuse" / "secretspec.toml"
    seen: list[Path] = []
    monkeypatch.setattr("aiuse.credentials.default_manifest_path", lambda: manifest)
    monkeypatch.setattr("aiuse.credentials._chrome_cookie_header", lambda _profile: "session=secret")
    monkeypatch.setattr("aiuse.credentials._validate_opencode_zen_cookie", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aiuse.credentials._save_with_secretspec",
        lambda _secret, *, manifest, timeout: seen.append(manifest),
    )

    assert cli.main(["credential", "refresh", "opencode-zen", "--yes"]) == 0
    assert seen == [manifest]
    text = manifest.read_text()
    assert "OPENCODE_ZEN_COOKIE" in text
    assert "session=secret" not in text


def test_credential_refresh_does_not_save_after_validation_failure(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.credentials._chrome_cookie_header", lambda _profile: "session=secret")
    monkeypatch.setattr(
        "aiuse.credentials._validate_opencode_zen_cookie",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CredentialError("not authenticated")),
    )
    monkeypatch.setattr(
        "aiuse.credentials._save_with_secretspec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not save invalid cookie")),
    )

    assert cli.main(["credential", "refresh", "opencode-zen", "--yes"]) == 1
    captured = capsys.readouterr()
    assert "not authenticated" in captured.err
    assert "secret" not in captured.err
