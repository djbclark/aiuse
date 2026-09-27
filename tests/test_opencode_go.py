from types import SimpleNamespace

from aiuse.collectors.base import CollectorError
from aiuse.collectors.opencode_go import (
    _EXPIRED_DESCRIPTION,
    _account_from_go_status,
    collect_opencode_go,
)
from aiuse.models import BillingKind

# Shape of a live GET /console/api/go/status (micro-cents; 1e8 == $1).
_ACTIVE_STATUS = {
    "product": "go",
    "access": {
        "startsAt": "2026-09-19T17:20:12.000Z",
        "endsAt": "2026-10-19T17:20:12.000Z",
        "meters": {
            "fiveHour": {
                "startsAt": "2026-09-27T01:00:00.000Z",
                "resetsAt": "2026-09-27T06:00:00.000Z",
                "limitMicroCents": "1200000000",
                "usedMicroCents": "150000000",
            },
            "week": {
                "startsAt": "2026-09-21T00:00:00.000Z",
                "resetsAt": "2026-09-28T00:00:00.000Z",
                "limitMicroCents": "3000000000",
                "usedMicroCents": "120000000",
            },
            "month": {"limitMicroCents": "6000000000", "usedMicroCents": "114000000"},
        },
    },
}

# A lapsed plan: the route answers, but there is no access period.
_INACTIVE_STATUS = {"product": None, "access": None}


def test_collect_opencode_go_is_quiet_until_cookie_is_supplied():
    assert collect_opencode_go(environ={}) == []


def test_absent_access_is_expired_empty_not_zero_used():
    account = _account_from_go_status(_INACTIVE_STATUS)
    assert account is not None
    assert account.provider == "opencode-go"
    assert account.source == "opencode_go"
    assert account.plan == "expired"
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(account.windows) == 1
    window = account.windows[0]
    assert window.used_percent is None
    assert window.remaining_percent == 0.0
    assert window.reset_description == _EXPIRED_DESCRIPTION
    assert "expired" in " ".join(account.notes).casefold()


def test_a_missing_go_route_is_expired_not_a_failure():
    """``/go/status`` 404s for a workspace that never subscribed."""
    account = _account_from_go_status(None)
    assert account is not None
    assert account.plan == "expired"


def test_active_subscription_parses_shared_windows():
    account = _account_from_go_status(_ACTIVE_STATUS)
    assert account is not None
    assert account.plan == "go"
    labels = [window.label for window in account.windows]
    assert labels == ["OpenCode Go 5-hour", "OpenCode Go weekly", "OpenCode Go monthly"]
    assert [round(window.used_percent or 0.0, 2) for window in account.windows] == [12.5, 4.0, 1.9]
    assert all(window.resets_at is not None for window in account.windows)
    assert all((window.remaining() or 0) > 0 for window in account.windows)
    assert account.windows[0].raw["limit_usd"] == 12.0


def test_monthly_window_falls_back_to_the_billing_period_end():
    account = _account_from_go_status(_ACTIVE_STATUS)
    assert account is not None
    monthly = account.windows[-1]
    assert monthly.resets_at is not None
    assert monthly.resets_at.isoformat().startswith("2026-10-19T17:20:12")


def test_meters_without_a_limit_are_not_quota_windows():
    account = _account_from_go_status(
        {
            "product": "go",
            "access": {
                "endsAt": "2026-10-19T17:20:12.000Z",
                "meters": {
                    "fiveHour": {"limitMicroCents": "1200000000", "usedMicroCents": "0"},
                    "week": {"limitMicroCents": None, "usedMicroCents": "0"},
                },
            },
        }
    )
    assert account is not None
    assert [window.label for window in account.windows] == ["OpenCode Go 5-hour"]


def test_collect_prefers_active_workspace_over_expired_sibling(monkeypatch):
    statuses = {"work_inactive": _INACTIVE_STATUS, "work_active": _ACTIVE_STATUS}

    monkeypatch.setattr(
        "aiuse.collectors.opencode_go.list_workspaces",
        lambda cookie, _timeout, label="": ["work_inactive", "work_active"],
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_status",
        lambda workspace, _cookie, _timeout: statuses[workspace],
    )

    accounts = collect_opencode_go(timeout=12, environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
    assert len(accounts) == 1
    assert accounts[0].plan == "go"
    assert round(accounts[0].windows[0].used_percent or 0.0, 2) == 12.5
    assert "example" not in str(accounts[0])


def test_collect_all_inactive_workspaces_is_expired(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go.list_workspaces",
        lambda *_args, **_kwargs: ["work_only"],
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_status",
        lambda *_args, **_kwargs: _INACTIVE_STATUS,
    )

    accounts = collect_opencode_go(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
    assert accounts[0].plan == "expired"
    assert accounts[0].windows[0].remaining_percent == 0.0


def test_collect_raises_when_the_status_route_fails(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go.list_workspaces",
        lambda *_args, **_kwargs: ["work_only"],
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CollectorError("OpenCode Go status returned HTTP 500")),
    )
    try:
        collect_opencode_go(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
    except CollectorError as exc:
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("status failure should surface")


def test_collect_uses_explicit_workspace_override(monkeypatch):
    seen: list[str] = []

    monkeypatch.setattr(
        "aiuse.collectors.opencode_go.list_workspaces",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not list workspaces")),
    )

    def fake_status(workspace, _cookie, _timeout):
        seen.append(workspace)
        return _INACTIVE_STATUS

    monkeypatch.setattr("aiuse.collectors.opencode_go._fetch_go_status", fake_status)

    accounts = collect_opencode_go(
        environ={
            "AIUSE_OPENCODE_ZEN_COOKIE": "session=example",
            "AIUSE_OPENCODE_ZEN_WORKSPACE_ID": "work_selected",
        }
    )
    assert seen == ["work_selected"]
    assert accounts[0].plan == "expired"


def test_secretspec_cookie_is_used_when_no_override(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout="session=from-secretspec\n")

    monkeypatch.setattr("aiuse.collectors.opencode_zen.shutil.which", lambda _name: "/usr/bin/secretspec")
    monkeypatch.setattr("aiuse.collectors.opencode_zen.subprocess.run", fake_run)
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go.list_workspaces",
        lambda *_args, **_kwargs: ["work_secret"],
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_status",
        lambda *_args, **_kwargs: _INACTIVE_STATUS,
    )

    accounts = collect_opencode_go()
    assert accounts[0].plan == "expired"
    assert "from-secretspec" not in str(accounts[0])
    assert seen
    assert "OPENCODE_ZEN_COOKIE" in seen[0]
