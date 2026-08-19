from types import SimpleNamespace

from aiuse.collectors.base import CollectorError
from aiuse.collectors.opencode_go import (
    _EXPIRED_DESCRIPTION,
    _account_from_go_page,
    collect_opencode_go,
)
from aiuse.models import BillingKind

_INACTIVE_PAGE = """
reloadError:null,timeReloadError:null,subscription:null,subscriptionID:null,
subscriptionPlan:null,timeSubscriptionBooked:null,timeSubscriptionSelected:null,
lite:null,liteSubscriptionID:null,monthlyLimit:80,monthlyUsage:0
"""

_ACTIVE_PAGE = """
subscription:{id:"sub_example"},subscriptionID:"sub_example",subscriptionPlan:"go",
rollingUsage:{usagePercent:12.5,resetInSec:1800},
weeklyUsage:{usagePercent:4,resetInSec:172800},
monthlyUsage:{usagePercent:1.9,resetInSec:1209600}
"""


def test_collect_opencode_go_is_quiet_until_cookie_is_supplied():
    assert collect_opencode_go(environ={}) == []


def test_null_subscription_is_expired_empty_not_zero_used():
    account = _account_from_go_page(_INACTIVE_PAGE)
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


def test_scalar_monthly_usage_is_not_a_quota_window():
    """The inactive page has monthlyUsage:0 — that is not 0% of a live allotment."""
    account = _account_from_go_page(_INACTIVE_PAGE)
    assert account is not None
    assert all("monthly" not in (window.label or "").casefold() for window in account.windows)


def test_active_subscription_parses_shared_windows():
    account = _account_from_go_page(_ACTIVE_PAGE)
    assert account is not None
    assert account.plan == "go"
    labels = [window.label for window in account.windows]
    assert labels == ["OpenCode Go 5-hour", "OpenCode Go weekly", "OpenCode Go monthly"]
    assert [window.used_percent for window in account.windows] == [12.5, 4.0, 1.9]
    assert all(window.resets_at is not None for window in account.windows)
    assert all((window.remaining() or 0) > 0 for window in account.windows)


def test_fractional_usage_percent_is_scaled():
    page = """
    subscription:{id:"sub_example"},subscriptionID:"sub_example",
    rollingUsage:{usagePercent:0.25,resetInSec:600}
    """
    account = _account_from_go_page(page)
    assert account is not None
    assert account.windows[0].used_percent == 25.0


def test_collect_prefers_active_workspace_over_expired_sibling(monkeypatch):
    def fake_fetch(_server_id, _args, cookie, _timeout):
        assert cookie == "session=example"
        return '{"ids":["work_inactive","work_active"]}'

    pages = {
        "work_inactive": _INACTIVE_PAGE,
        "work_active": _ACTIVE_PAGE,
    }

    monkeypatch.setattr("aiuse.collectors.opencode_go._fetch_server", fake_fetch)
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_page",
        lambda workspace, _cookie, _timeout: pages[workspace],
    )

    accounts = collect_opencode_go(timeout=12, environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
    assert len(accounts) == 1
    assert accounts[0].plan == "go"
    assert accounts[0].windows[0].used_percent == 12.5
    assert "example" not in str(accounts[0])


def test_collect_all_inactive_workspaces_is_expired(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_server",
        lambda *_args, **_kwargs: '{"id":"work_only"}',
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_page",
        lambda *_args, **_kwargs: _INACTIVE_PAGE,
    )

    accounts = collect_opencode_go(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
    assert accounts[0].plan == "expired"
    assert accounts[0].windows[0].remaining_percent == 0.0


def test_collect_raises_when_workspace_page_fails(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_server",
        lambda *_args, **_kwargs: '{"id":"work_only"}',
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CollectorError("OpenCode Go usage page returned HTTP 500")),
    )
    try:
        collect_opencode_go(environ={"AIUSE_OPENCODE_ZEN_COOKIE": "session=example"})
    except CollectorError as exc:
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("page failure should surface")


def test_collect_uses_explicit_workspace_override(monkeypatch):
    seen: list[str] = []

    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not list workspaces")),
    )

    def fake_page(workspace, _cookie, _timeout):
        seen.append(workspace)
        return _INACTIVE_PAGE

    monkeypatch.setattr("aiuse.collectors.opencode_go._fetch_go_page", fake_page)

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
        "aiuse.collectors.opencode_go._fetch_server",
        lambda *_args, **_kwargs: '{"id":"work_secret"}',
    )
    monkeypatch.setattr(
        "aiuse.collectors.opencode_go._fetch_go_page",
        lambda *_args, **_kwargs: _INACTIVE_PAGE,
    )

    accounts = collect_opencode_go()
    assert accounts[0].plan == "expired"
    assert "from-secretspec" not in str(accounts[0])
    assert seen
    assert "OPENCODE_ZEN_COOKIE" in seen[0]
