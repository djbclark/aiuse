from types import SimpleNamespace

from aiuse.collectors.base import CollectorError
from aiuse.collectors.opencode_zen import collect_opencode_zen
from aiuse.models import BillingKind


def test_collect_opencode_zen_is_quiet_until_cookie_is_explicitly_supplied():
    assert collect_opencode_zen(environ={}) == []


def test_collect_opencode_zen_returns_separate_prepaid_account(monkeypatch):
    calls: list[tuple[str, list[str] | None]] = []

    def fake_fetch(server_id, args, cookie, timeout):
        calls.append((server_id, args))
        assert cookie == "session=example"
        assert timeout == 12
        if args is None:
            return '{"data":[{"id":"work_example"}]}'
        return '{"customerID":"cus_example","balance":-3795383}'

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_server", fake_fetch)

    accounts = collect_opencode_zen(timeout=12, environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})

    assert calls[0][1] is None
    assert calls[1][1] == ["work_example"]
    assert len(accounts) == 1
    assert accounts[0].provider == "opencode-zen"
    assert accounts[0].source == "opencode_zen"
    assert accounts[0].billing_kind == BillingKind.PREPAID_BALANCE
    assert accounts[0].balance_usd == -0.03795383
    assert "example" not in str(accounts[0])


def test_collect_opencode_zen_uses_explicit_workspace_and_requires_balance(monkeypatch):
    def fake_fetch(_server_id, args, _cookie, _timeout):
        assert args == ["work_selected"]
        return '{"customerID":"cus_example"}'

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_server", fake_fetch)

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


def test_collect_opencode_zen_uses_secretspec_cookie_when_no_override(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout="session=from-secretspec\n")

    def fake_fetch(_server_id, args, cookie, _timeout):
        assert cookie == "session=from-secretspec"
        if args is None:
            return '{"id":"work_secret"}'
        return '{"customerID":"cus_example","balance":123456789}'

    monkeypatch.setattr("aiuse.collectors.opencode_zen.shutil.which", lambda _name: "/usr/bin/secretspec")
    monkeypatch.setattr("aiuse.collectors.opencode_zen.subprocess.run", fake_run)
    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_server", fake_fetch)

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


def test_collect_opencode_zen_explicit_cookie_overrides_secretspec(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.opencode_zen.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not call SecretSpec")),
    )

    def fake_fetch(_server_id, args, cookie, _timeout):
        assert cookie == "session=explicit"
        if args is None:
            return '{"id":"work_explicit"}'
        return '{"customerID":"cus_example","balance":1}'

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_server", fake_fetch)

    assert collect_opencode_zen(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=explicit"})


def test_collect_opencode_zen_accepts_current_opencode_workspace_prefix(monkeypatch):
    def fake_fetch(_server_id, args, _cookie, _timeout):
        if args is None:
            return '{"id":"w' + 'rk_current"}'
        assert args == ["w" + "rk_current"]
        return '{"customerID":"cus_example","balance":1}'

    monkeypatch.setattr("aiuse.collectors.opencode_zen._fetch_server", fake_fetch)

    assert collect_opencode_zen(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
