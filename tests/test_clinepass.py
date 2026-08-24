from aiuse.collectors.clinepass import _clinepass_window, collect_clinepass
from aiuse.models import BillingKind


def test_clinepass_window_types():
    assert _clinepass_window("five_hour") == ("ClinePass 5-hour", 300)
    assert _clinepass_window("weekly") == ("ClinePass weekly", 10080)
    assert _clinepass_window("monthly") == ("ClinePass monthly", 43200)


def test_collect_clinepass_parses_official_limits(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "limits": [
                        {"type": "five_hour", "percentUsed": 4, "resetsAt": "2026-08-19T22:17:09Z"},
                        {"type": "weekly", "percentUsed": 32, "resetsAt": "2026-08-23T22:36:15Z"},
                        {"type": "monthly", "percentUsed": 16, "resetsAt": "2026-09-15T22:36:15Z"},
                    ]
                },
            }

    monkeypatch.setattr("aiuse.collectors.clinepass.requests.get", lambda *_args, **_kwargs: FakeResponse())

    accounts = collect_clinepass(environ={"AIUSE_CLINE_API_KEY": "test-key"})
    assert len(accounts) == 1
    account = accounts[0]
    assert account.source == "clinepass"
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert [window.label for window in account.windows] == [
        "ClinePass 5-hour",
        "ClinePass weekly",
        "ClinePass monthly",
    ]
    assert [window.used_percent for window in account.windows] == [4.0, 32.0, 16.0]
    assert [window.window_minutes for window in account.windows] == [300, 10080, 43200]
    assert "test-key" not in str(account)


def test_missing_key_reports_an_error_account_not_an_empty_list(monkeypatch):
    """A provider we cannot check must not vanish from the snapshot.

    Returning [] made an unreachable ClinePass indistinguishable from an
    unconfigured one, so anything reading the snapshot saw "no data" where it
    should have seen "could not check" — which a quota or burn-rate alert
    reads as healthy.
    """
    from aiuse.collectors import clinepass

    monkeypatch.setattr(clinepass.shutil, "which", lambda _: None)
    accounts = clinepass.collect_clinepass(environ={})

    assert len(accounts) == 1
    assert accounts[0].provider == "clinepass"
    assert accounts[0].error
    assert "sudo-secretspec" in accounts[0].error
    assert not accounts[0].windows


def test_resolve_api_key_distinguishes_failure_causes(monkeypatch):
    """Each failure carries its own reason; they used to collapse into None."""
    from aiuse.collectors import clinepass

    monkeypatch.setattr(clinepass.shutil, "which", lambda _: None)
    key, err = clinepass._resolve_api_key({}, 45.0)
    assert key is None and "not on PATH" in err

    monkeypatch.setattr(clinepass.shutil, "which", lambda _: "/usr/bin/sudo-secretspec")

    def _timeout(*a, **k):
        raise clinepass.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(clinepass.subprocess, "run", _timeout)
    key, err = clinepass._resolve_api_key({}, 45.0)
    assert key is None and "timed out" in err

    class _Empty:
        returncode = 0
        stdout = "   "

    monkeypatch.setattr(clinepass.subprocess, "run", lambda *a, **k: _Empty())
    key, err = clinepass._resolve_api_key({}, 45.0)
    assert key is None and "empty" in err


def test_explicit_env_key_wins_and_reports_no_error():
    from aiuse.collectors import clinepass

    key, err = clinepass._resolve_api_key({"AIUSE_CLINE_API_KEY": "sk-x"}, 45.0)
    assert key == "sk-x" and err is None
