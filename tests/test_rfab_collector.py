"""Hermetic unit tests for the Reality Fabricator (rfab.ai) token balance collector."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, unavailable_pools
from aiuse.chat_format import render_chat_report
from aiuse.collectors import runner
from aiuse.collectors.base import CollectorError
from aiuse.collectors.rfab import collect_rfab
from aiuse.config import (
    DEFAULT_CONFIG,
    KNOWN_COLLECTOR_KEYS,
    SINGLE_PROVIDER_COLLECTORS,
    timeout_for,
    validate_config,
)
from aiuse.models import (
    OPERATOR_APPROVAL_NOTICE,
    BillingKind,
    Snapshot,
    canonical_provider,
    utcnow,
)
from aiuse.report import _strip_ansi, render_clock_matrix

SECRET_KEY = "rfab_live_super_secret_test_key_999"
KEY_ENV = {"RFAB_API_KEY": SECRET_KEY}


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def _serve(monkeypatch, payload, status_code: int = 200):
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_get(url, timeout, headers):
        calls.append((url, dict(headers), timeout))
        return FakeResponse(payload, status_code)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


# --- Happy path & balance conversion ------------------------------------------


def test_happy_path_converts_tokens_to_usd_and_uses_x_api_key(monkeypatch):
    calls = _serve(
        monkeypatch,
        {
            "success": True,
            "tokenBalance": 633020,
            "totalTokensUsed": 17980,
            "totalInteractions": 5,
            "recentTransactions": [{"id": "tx1", "tokens": -100}],
        },
    )

    [account] = collect_rfab(timeout=12, environ=KEY_ENV)

    assert len(calls) == 1
    url, headers, timeout = calls[0]
    assert url == "https://api.rfab.ai/api/tokens/balance"
    assert timeout == 12
    assert headers["X-API-Key"] == SECRET_KEY
    assert "Authorization" not in headers
    assert all("Bearer" not in v for v in headers.values())

    assert (account.source, account.provider) == ("rfab", "rfab")
    assert account.billing_kind == BillingKind.PREPAID_BALANCE
    assert account.balance_usd == pytest.approx(12.6604)
    assert account.credits_remaining == 633020.0
    assert account.error is None
    assert account.requires_operator_approval is False

    assert len(account.windows) == 1
    window = account.windows[0]
    assert window.window_id == "rfab-token-balance"
    assert window.remaining_percent == 100.0
    assert window.used_percent == 0.0
    assert "$12.66" in (window.reset_description or "")
    assert "633,020 tokens" in (window.reset_description or "")
    assert any("633,020 tokens" in note and "$12.66" in note for note in account.notes)
    assert any("17,980 tokens used" in note and "5 interactions" in note for note in account.notes)
    assert SECRET_KEY not in str(account)
    assert SECRET_KEY not in str(account.to_dict())


def test_happy_path_appears_in_available_json_matrix_and_chat(monkeypatch):
    _serve(
        monkeypatch,
        {"success": True, "tokenBalance": 633020, "totalTokensUsed": 17980, "totalInteractions": 5},
    )
    [account] = collect_rfab(environ=KEY_ENV)
    now = utcnow()
    snap = Snapshot(collected_at=now, accounts=[account])
    snap_dict = snap.to_dict()
    enrich_snapshot(snap_dict, now=now)

    avail = available_pools(snap_dict)
    assert len(avail) == 1
    assert avail[0]["provider"] == "rfab"
    assert avail[0]["vendor"] == "rfab"
    assert avail[0]["pool_id"] == "rfab:default"
    assert avail[0]["usable_now"] is True
    assert avail[0]["balance_usd"] == pytest.approx(12.6604)
    assert "633,020 tokens" in avail[0]["windows"][0]["reset_description"]
    assert unavailable_pools(snap_dict, now=now) == []

    matrix = _strip_ansi(render_clock_matrix([], snapshot=snap, color=False, width=120))
    assert "rfab" in matrix
    assert "↓ $12.66 prepaid" in matrix

    chat = render_chat_report(snap, [])
    assert "**rfab**" in chat
    assert "↓ $12.66 prepaid" in chat


# --- Zero / negative balance --------------------------------------------------


@pytest.mark.parametrize("raw_balance", [0, -250])
def test_zero_or_negative_balance_reports_exhausted(monkeypatch, raw_balance):
    _serve(monkeypatch, {"success": True, "tokenBalance": raw_balance})

    [account] = collect_rfab(environ=KEY_ENV)
    assert account.balance_usd == 0.0
    assert account.credits_remaining == 0.0
    assert account.windows[0].remaining_percent == 0.0
    assert account.windows[0].used_percent == 100.0
    assert "0 tokens" in (account.windows[0].reset_description or "")
    assert any("exhausted" in note for note in account.notes)

    now = utcnow()
    snap = Snapshot(collected_at=now, accounts=[account])
    snap_dict = snap.to_dict()
    enrich_snapshot(snap_dict, now=now)

    assert available_pools(snap_dict) == []
    unavail = unavailable_pools(snap_dict, now=now)
    assert len(unavail) == 1
    assert unavail[0]["provider"] == "rfab"
    assert unavail[0]["reason"] == "exhausted"

    matrix = _strip_ansi(render_clock_matrix([], snapshot=snap, color=False, width=120))
    assert "empty" in matrix and "rfab" in matrix


# --- Auth, malformed payload, and network errors ------------------------------


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_returns_error_row_without_leaking_key(monkeypatch, status_code):
    _serve(monkeypatch, {"error": f"AUTH_REQUIRED {SECRET_KEY}"}, status_code=status_code)

    [account] = collect_rfab(environ=KEY_ENV)
    assert account.source == "rfab"
    assert account.provider == "rfab"
    assert account.billing_kind == BillingKind.PREPAID_BALANCE
    assert account.error is not None
    assert f"HTTP {status_code}" in account.error
    assert SECRET_KEY not in account.error
    assert SECRET_KEY not in str(account)


@pytest.mark.parametrize(
    ("payload", "status", "match"),
    [
        ({"success": False, "error": "bad"}, 200, "success=false"),
        ({"tokenBalance": 50000}, 200, "success=false"),
        ({"success": True}, 200, "missing numeric tokenBalance"),
        ({"success": True, "tokenBalance": True}, 200, "missing numeric tokenBalance"),
        ({"success": True, "tokenBalance": "633020"}, 200, "missing numeric tokenBalance"),
        ({"success": True, "tokenBalance": float("nan")}, 200, "missing numeric tokenBalance"),
        (["not", "a", "dict"], 200, "JSON object"),
        (ValueError("bad json"), 200, "invalid JSON"),
        ({}, 500, "HTTP 500"),
    ],
)
def test_malformed_or_failed_responses_raise_collector_error(monkeypatch, payload, status, match):
    _serve(monkeypatch, payload, status_code=status)

    with pytest.raises(CollectorError, match=match) as exc_info:
        collect_rfab(environ=KEY_ENV)
    assert SECRET_KEY not in str(exc_info.value)


@pytest.mark.parametrize(
    ("exc", "expected_name"),
    [
        (requests.Timeout(f"timed out with {SECRET_KEY}"), "Timeout"),
        (requests.ConnectionError(f"refused with {SECRET_KEY}"), "ConnectionError"),
    ],
)
def test_timeout_and_network_errors_raise_without_leaking_key(monkeypatch, exc, expected_name):
    def fake_get(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(CollectorError, match=expected_name) as exc_info:
        collect_rfab(environ=KEY_ENV)
    assert SECRET_KEY not in str(exc_info.value)


# --- Key resolution & clean skip when absent ----------------------------------


def test_missing_key_skips_cleanly_without_http(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no request without a key"))
    assert collect_rfab(environ={}) == []


def test_aiuse_rfab_api_key_overrides_rfab_api_key(monkeypatch):
    calls = _serve(monkeypatch, {"success": True, "tokenBalance": 50000})
    [account] = collect_rfab(
        environ={"AIUSE_RFAB_API_KEY": "rfab_override_key", "RFAB_API_KEY": SECRET_KEY},
    )
    assert calls[0][1]["X-API-Key"] == "rfab_override_key"
    assert account.balance_usd == pytest.approx(1.0)


def test_uses_sudo_secretspec_when_env_unset(monkeypatch):
    monkeypatch.delenv("AIUSE_RFAB_API_KEY", raising=False)
    monkeypatch.delenv("RFAB_API_KEY", raising=False)

    seen_cmds: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/sudo-secretspec" if name == "sudo-secretspec" else None

    def fake_run(cmd, **_kwargs):
        seen_cmds.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout=f"{SECRET_KEY}\n")

    calls = _serve(monkeypatch, {"success": True, "tokenBalance": 100000})
    monkeypatch.setattr("aiuse.collectors.rfab.shutil.which", fake_which)
    monkeypatch.setattr("aiuse.collectors.rfab.subprocess.run", fake_run)

    [account] = collect_rfab(timeout=15.0)

    assert seen_cmds == [
        ["/usr/local/bin/sudo-secretspec", "get", "RFAB_API_KEY", "--reason", "aiuse-rfab-balance"],
    ]
    assert calls[0][1]["X-API-Key"] == SECRET_KEY
    assert "Authorization" not in calls[0][1]
    assert account.balance_usd == pytest.approx(2.0)
    assert SECRET_KEY not in str(account)


def test_missing_secret_in_sudo_secretspec_and_no_secretspec_skips_cleanly(monkeypatch):
    monkeypatch.delenv("AIUSE_RFAB_API_KEY", raising=False)
    monkeypatch.delenv("RFAB_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no request without a key"))
    monkeypatch.setattr(
        "aiuse.collectors.rfab.shutil.which",
        lambda name: "/usr/local/bin/sudo-secretspec" if name == "sudo-secretspec" else None,
    )
    monkeypatch.setattr(
        "aiuse.collectors.rfab.subprocess.run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout=""),
    )

    assert collect_rfab() == []


# --- Config, runner registration, disabled_services, and operator gate --------


def _only_rfab_collectors(extra: dict | None = None) -> dict:
    cfg = {name: {"enabled": name == "rfab"} for name in KNOWN_COLLECTOR_KEYS}
    if extra:
        cfg["rfab"] = {"enabled": True, **extra}
    return cfg


def test_config_and_runner_registration_and_defaults():
    assert canonical_provider("rfab.ai") == "rfab"
    assert canonical_provider("reality-fabricator") == "rfab"
    assert SINGLE_PROVIDER_COLLECTORS["rfab"] == "rfab"
    assert "rfab" in KNOWN_COLLECTOR_KEYS
    assert timeout_for(DEFAULT_CONFIG, "rfab") == 15.0
    assert timeout_for({"timeouts": {"rfab": 9}}, "rfab") == 9.0

    issues = validate_config(
        {
            "timeouts": {"rfab": 15},
            "collectors": {
                "rfab": {"enabled": True, "operator_gated": True},
            },
            "operator_gated": {"rfab": "ask before spending"},
        }
    )
    assert issues == []

    bad = validate_config({"collectors": {"rfab": {"enabled": True, "operator_gated": "yes"}}})
    assert any("collectors.rfab.operator_gated must be a boolean" in item for item in bad)


def test_runner_respects_enabled_disabled_and_timeout(monkeypatch):
    seen_timeouts: list[float] = []

    def fake_collect_rfab(*, timeout: float = 15.0, requires_operator_approval: bool = False):
        seen_timeouts.append(timeout)
        return collect_rfab(
            timeout=timeout,
            environ=KEY_ENV,
            requires_operator_approval=requires_operator_approval,
        )

    _serve(monkeypatch, {"success": True, "tokenBalance": 633020})
    monkeypatch.setattr(runner, "collect_rfab", fake_collect_rfab)

    # Disabled via [collectors.rfab].enabled = false
    disabled_col = runner.run_collectors({"collectors": {name: {"enabled": False} for name in KNOWN_COLLECTOR_KEYS}})
    assert disabled_col.accounts == []
    assert seen_timeouts == []

    # Disabled via [disabled_services]
    disabled_svc = runner.run_collectors(
        {
            "collectors": _only_rfab_collectors(),
            "disabled_services": {"rfab": "paused"},
        }
    )
    assert disabled_svc.accounts == []
    assert seen_timeouts == []

    # Enabled with custom timeout
    snap = runner.run_collectors(
        {
            "timeouts": {"rfab": 7},
            "collectors": _only_rfab_collectors(),
        }
    )
    assert seen_timeouts == [7.0]
    assert len(snap.accounts) == 1
    assert snap.accounts[0].provider == "rfab"
    assert snap.accounts[0].balance_usd == pytest.approx(12.6604)


def test_runner_error_does_not_crash_run(monkeypatch):
    _serve(monkeypatch, {"success": False})
    monkeypatch.setattr(
        runner,
        "collect_rfab",
        lambda *, timeout=15.0, requires_operator_approval=False: collect_rfab(
            timeout=timeout,
            environ=KEY_ENV,
            requires_operator_approval=requires_operator_approval,
        ),
    )

    snap = runner.run_collectors({"collectors": _only_rfab_collectors()})
    assert snap.accounts == []
    assert any(err.startswith("rfab: ") and "success=false" in err for err in snap.collector_errors)


@pytest.mark.parametrize(
    "extra_config",
    [
        {"collectors": _only_rfab_collectors({"operator_gated": True})},
        {"collectors": _only_rfab_collectors({"requires_operator_approval": True})},
        {"collectors": _only_rfab_collectors(), "operator_gated": {"rfab": True}},
        {"collectors": _only_rfab_collectors(), "operator_gated": {"rfab": "operator approval required"}},
    ],
)
def test_operator_gate_config_compatibility(monkeypatch, extra_config):
    _serve(monkeypatch, {"success": True, "tokenBalance": 633020})
    monkeypatch.setattr(
        runner,
        "collect_rfab",
        lambda *, timeout=15.0, requires_operator_approval=False: collect_rfab(
            timeout=timeout,
            environ=KEY_ENV,
            requires_operator_approval=requires_operator_approval,
        ),
    )

    snap = runner.run_collectors(extra_config)
    assert len(snap.accounts) == 1
    account = snap.accounts[0]
    assert account.requires_operator_approval is True
    assert account.to_dict()["requires_operator_approval"] is True
    assert account.to_dict()["ai_notice"] == OPERATOR_APPROVAL_NOTICE

    snap_dict = snap.to_dict()
    enrich_snapshot(snap_dict, now=snap.collected_at)
    avail = available_pools(snap_dict)
    assert len(avail) == 1
    assert avail[0]["requires_operator_approval"] is True
    assert avail[0]["ai_notice"] == OPERATOR_APPROVAL_NOTICE

    matrix = _strip_ansi(render_clock_matrix([], snapshot=snap, color=False, width=140))
    assert "Do not use without asking operator" in matrix
