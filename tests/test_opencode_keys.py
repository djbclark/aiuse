import subprocess
from types import SimpleNamespace

import pytest

from aiuse.collectors.base import CollectorError
from aiuse.collectors.opencode_go import collect_opencode_go
from aiuse.collectors.opencode_keys import (
    active_label_from_list_output,
    display_label,
    resolve_opencode_api_keys,
)
from aiuse.collectors.opencode_zen import collect_opencode_zen

_KEYS = {
    "OPENCODE_GO_API_KEY_DJBCLARK": "oc_test_djbclark",
    "OPENCODE_GO_API_KEY_DCLARKUS": "oc_test_dclark",
    "OPENCODE_GO_API_KEY_TECHNOEUPHORA": "oc_test_te",
}

_LIST_OUTPUT = """\
service pid 1  http://127.0.0.1:1
  opencode     'djbclark'   someone@example.com         cred_a
  opencode     't-e'        other@example.com           cred_b
* opencode-go  'dclark'     key                         cred_c
* opencode     'dclark'     key                         cred_d
"""


def test_active_label_is_the_console_credential_not_the_go_row():
    assert active_label_from_list_output(_LIST_OUTPUT) == "dclark"
    assert display_label("OPENCODE_GO_API_KEY_DCLARKUS", "dclark") == "*dclark"
    assert display_label("OPENCODE_GO_API_KEY_TECHNOEUPHORA", "dclark") == "t-e"


def test_vault_reads_the_three_go_keys_without_keeping_them_in_the_label(monkeypatch):
    def fake_run(command, **_kwargs):
        assert command[0] == "/usr/local/bin/sudo-secretspec"
        assert command[1:3] == ["get", command[2]]
        assert "--reason" in command
        value = _KEYS.get(command[2], "")
        return SimpleNamespace(returncode=0 if value else 1, stdout=f"{value}\n")

    monkeypatch.setattr(
        "aiuse.collectors.opencode_keys.shutil.which",
        lambda name: "/usr/local/bin/sudo-secretspec" if name == "sudo-secretspec" else None,
    )
    monkeypatch.setattr("aiuse.collectors.opencode_keys.subprocess.run", fake_run)

    specs = resolve_opencode_api_keys({}, 5, allow_vault=True)

    assert specs is not None
    assert [label for label, _key in specs] == ["djbclark", "dclark", "t-e"]
    assert [key for _label, key in specs] == list(_KEYS.values())


def test_go_api_keys_skip_the_console_cookie(monkeypatch):
    def fake_get(url, timeout, headers, allow_redirects):
        assert allow_redirects is False
        assert url == "https://opencode.ai/zen/go/v1/usage"
        token = headers["Authorization"].removeprefix("Bearer ")
        monthly = {"oc_test_djbclark": 99, "oc_test_dclark": 1, "oc_test_te": 50}[token]
        weekly_status = "rate-limited" if token == "oc_test_te" else "ok"
        weekly_percent = 100 if token == "oc_test_te" else 0
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "usage": {
                    "rolling": {"status": "ok", "percent": 0, "resetsAt": "2026-10-29T18:14:44.000Z"},
                    "weekly": {
                        "status": weekly_status,
                        "percent": weekly_percent,
                        "resetsAt": "2026-11-02T00:00:00.000Z",
                    },
                    "monthly": {"status": "ok", "percent": monthly, "resetsAt": "2026-11-10T17:56:18.000Z"},
                }
            },
        )

    monkeypatch.setattr("aiuse.collectors.opencode_go.requests.get", fake_get)
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go.list_workspaces",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cookie path")),
    )

    accounts = collect_opencode_go(environ=dict(_KEYS))

    assert [account.account for account in accounts] == ["djbclark", "dclark", "t-e"]
    assert [round(account.windows[-1].used_percent or 0) for account in accounts] == [99, 1, 50]
    assert accounts[2].windows[1].used_percent == 100.0
    assert any("rate-limited" in note for note in accounts[2].notes)
    assert "oc_test" not in repr(accounts)
    assert accounts[0].windows[0].resets_at is not None


def test_rejected_go_key_does_not_drop_the_other_accounts(monkeypatch):
    def fake_get(_url, timeout, headers, allow_redirects):
        token = headers["Authorization"].removeprefix("Bearer ")
        if token == "oc_test_dclark":
            return SimpleNamespace(status_code=403, json=lambda: {})
        if token == "oc_test_te":
            return SimpleNamespace(status_code=401, json=lambda: {})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "usage": {
                    "rolling": {"status": "ok", "percent": 4, "resetsAt": "2026-10-29T18:14:44.000Z"},
                    "weekly": {"status": "ok", "percent": 3, "resetsAt": "2026-11-02T00:00:00.000Z"},
                    "monthly": {"status": "ok", "percent": 2, "resetsAt": "2026-11-10T17:56:18.000Z"},
                }
            },
        )

    monkeypatch.setattr("aiuse.collectors.opencode_go.requests.get", fake_get)

    accounts = collect_opencode_go(environ=dict(_KEYS))

    assert accounts[0].plan == "go"
    assert accounts[1].plan == "expired"
    assert accounts[1].error is None
    assert accounts[2].error == "OpenCode Go API key was rejected"
    assert "oc_test" not in repr(accounts)


def test_zen_api_keys_read_each_balance(monkeypatch):
    balances = {"oc_test_djbclark": "-3800000", "oc_test_dclark": "0", "oc_test_te": "500000000"}

    def fake_get(url, timeout, headers, allow_redirects):
        assert allow_redirects is False
        assert url.endswith("/console/api/billing/status")
        token = headers["Authorization"].removeprefix("Bearer ")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "billingMode": "prepaid",
                "mode": "pay-as-you-go",
                "balanceMicroCents": balances[token],
                "availableMicroCents": "0",
            },
        )

    monkeypatch.setattr("aiuse.collectors.opencode_zen.requests.get", fake_get)
    monkeypatch.setattr(
        "aiuse.collectors.opencode_zen._fetch_console",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cookie path")),
    )

    accounts = collect_opencode_zen(environ=dict(_KEYS))

    assert [account.account for account in accounts] == ["djbclark", "dclark", "t-e"]
    assert [account.balance_usd for account in accounts] == pytest.approx([-0.038, 0.0, 5.0])
    assert "oc_test" not in repr(accounts)


def test_zen_reports_a_vault_timeout_and_does_not_use_the_cookie(monkeypatch):
    """A vault read that exceeds 5s is the collector error, not a signed-out cookie."""

    def fake_run(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 5)

    monkeypatch.setattr(
        "aiuse.collectors.opencode_keys.shutil.which",
        lambda name: "/usr/local/bin/sudo-secretspec" if name == "sudo-secretspec" else None,
    )
    monkeypatch.setattr("aiuse.collectors.opencode_keys.subprocess.run", fake_run)
    monkeypatch.setattr(
        "aiuse.collectors.opencode_zen._fetch_console",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cookie path")),
    )

    with pytest.raises(CollectorError, match="sudo-secretspec timed out reading OPENCODE_GO_API_KEY_DJBCLARK"):
        collect_opencode_zen()
