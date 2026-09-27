from types import SimpleNamespace

from aiuse.collectors.base import CollectorError
from aiuse.collectors.opencode_zen import collect_opencode_zen
from aiuse.models import BillingKind

_ORGS = [{"id": "work_example", "name": "Default"}]
_BILLING = {
    "billingMode": "prepaid",
    "mode": "pay-as-you-go",
    "balanceMicroCents": "-3795383",
    "availableMicroCents": "0",
}


def test_collect_opencode_zen_is_quiet_until_cookie_is_explicitly_supplied():
    assert collect_opencode_zen(environ={}) == []


def test_collect_opencode_zen_returns_separate_prepaid_account(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_fetch(path, cookie, timeout, *, org=None, label="", allow_missing=False):
        calls.append((path, org))
        assert cookie == "session=example"
        assert timeout == 12
        return _ORGS if path == "/orgs" else _BILLING

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_console", fake_fetch)

    accounts = collect_opencode_zen(timeout=12, environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})

    assert calls == [("/orgs", None), ("/billing/status", "work_example")]
    assert len(accounts) == 1
    assert accounts[0].provider == "opencode-zen"
    assert accounts[0].source == "opencode_zen"
    assert accounts[0].billing_kind == BillingKind.PREPAID_BALANCE
    assert accounts[0].balance_usd == -0.03795383
    assert accounts[0].raw["available_usd"] == 0.0
    assert "example" not in str(accounts[0])


def test_collect_opencode_zen_uses_explicit_workspace_and_requires_balance(monkeypatch):
    def fake_fetch(path, _cookie, _timeout, *, org=None, label="", allow_missing=False):
        assert path == "/billing/status"
        assert org == "work_selected"
        return {"billingMode": "prepaid"}

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_console", fake_fetch)

    try:
        collect_opencode_zen(
            environ={
                "AIUSE_OPENCODE_ZEN_COOKIE": "session=example",
                "AIUSE_OPENCODE_ZEN_WORKSPACE_ID": "work_selected",
            }
        )
    except CollectorError as exc:
        assert "did not include a balance" in str(exc)
    else:
        raise AssertionError("missing Zen balance should not be reported as live")


def test_collect_opencode_zen_reports_a_signed_out_session_with_a_hint(monkeypatch):
    class _Response:
        status_code = 401
        text = '{"_tag":"Unauthorized"}'

    monkeypatch.setattr(
        "aiuse.collectors.opencode_zen.requests.get",
        lambda *_args, **_kwargs: _Response(),
    )

    try:
        collect_opencode_zen(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=stale"})
    except CollectorError as exc:
        assert "credential refresh opencode-zen" in str(exc)
    else:
        raise AssertionError("an unauthenticated console session must be actionable")


def test_collect_opencode_zen_uses_secretspec_cookie_when_no_override(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout="session=from-secretspec\n")

    def fake_fetch(path, cookie, _timeout, *, org=None, label="", allow_missing=False):
        assert cookie == "session=from-secretspec"
        if path == "/orgs":
            return [{"id": "work_secret"}]
        return {"balanceMicroCents": 123456789}

    monkeypatch.setattr("aiuse.collectors.opencode_zen.shutil.which", lambda _name: "/usr/bin/secretspec")
    monkeypatch.setattr("aiuse.collectors.opencode_zen.subprocess.run", fake_run)
    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_console", fake_fetch)

    monkeypatch.setenv("SECRETSPEC_FILE", "/tmp/aiuse-secretspec.toml")
    accounts = collect_opencode_zen()

    assert seen == [
        [
            "/usr/bin/secretspec",
            "get",
            "--file",
            "/tmp/aiuse-secretspec.toml",
            "--reason",
            "aiuse OpenCode Zen balance collection",
            "OPENCODE_ZEN_COOKIE",
        ]
    ]
    assert accounts[0].balance_usd == 1.23456789
    assert "from-secretspec" not in str(accounts[0])


def test_collect_opencode_zen_prefers_user_manifest_when_not_overridden(monkeypatch, tmp_path):
    manifest = tmp_path / "secretspec.toml"
    manifest.write_text("[project]\nname = 'aiuse'\n")
    seen: list[list[str]] = []

    monkeypatch.setattr("aiuse.collectors.opencode_zen.shutil.which", lambda _name: "/usr/bin/secretspec")
    monkeypatch.setattr("aiuse.collectors.opencode_zen.resolve_manifest_path", lambda _env: manifest)
    monkeypatch.setattr(
        "aiuse.collectors.opencode_zen.subprocess.run",
        lambda command, **_kwargs: seen.append(command) or SimpleNamespace(returncode=1, stdout=""),
    )

    assert collect_opencode_zen() == []
    assert str(manifest) in seen[0]


def test_collect_opencode_zen_explicit_cookie_overrides_secretspec(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.opencode_zen.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not call SecretSpec")),
    )

    def fake_fetch(path, cookie, _timeout, *, org=None, label="", allow_missing=False):
        assert cookie == "session=explicit"
        if path == "/orgs":
            return [{"id": "work_explicit"}]
        return {"balanceMicroCents": 1}

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_console", fake_fetch)

    assert collect_opencode_zen(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=explicit"})


def test_collect_opencode_zen_accepts_the_legacy_workspace_prefix(monkeypatch):
    def fake_fetch(path, _cookie, _timeout, *, org=None, label="", allow_missing=False):
        if path == "/orgs":
            return [{"id": "work_legacy"}]
        assert org == "work_legacy"
        return {"balanceMicroCents": 1}

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_console", fake_fetch)

    assert collect_opencode_zen(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
