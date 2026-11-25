"""Hermetic unit tests for the Reality Fabricator (rfab.ai) token balance collector."""

from __future__ import annotations

import json
import subprocess
import traceback
from types import SimpleNamespace

import pytest
import requests

from aiuse import cli
from aiuse.analysis import history
from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, unavailable_pools
from aiuse.chat_format import render_chat_report
from aiuse.collectors import runner
from aiuse.collectors.base import CollectorError
from aiuse.collectors.rfab import collect_rfab
from aiuse.config import (
    DEFAULT_CONFIG,
    KNOWN_COLLECTOR_KEYS,
    SINGLE_PROVIDER_COLLECTORS,
    collection_policy_fingerprint,
    snapshot_matches_policy,
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
from aiuse.usage_sources import usage_source_report

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
    calls: list[tuple[str, dict[str, str], float, bool | None]] = []

    def fake_get(url, *, timeout, headers, allow_redirects=None):
        calls.append((url, dict(headers), timeout, allow_redirects))
        return FakeResponse(payload, status_code)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


# --- Happy path, precision & redirect boundary --------------------------------


def test_happy_path_converts_tokens_to_usd_and_uses_x_api_key_without_redirects(monkeypatch):
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
    url, headers, timeout, allow_redirects = calls[0]
    assert url == "https://api.rfab.ai/api/tokens/balance"
    assert timeout == 12
    assert allow_redirects is False
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


@pytest.mark.parametrize(
    ("tokens", "expected_usd", "display_usd"),
    [
        (1, 0.00002, "$0.00"),
        (633021, 12.66042, "$12.66"),
    ],
)
def test_balance_usd_keeps_unrounded_precision_while_display_rounds(monkeypatch, tokens, expected_usd, display_usd):
    _serve(monkeypatch, {"success": True, "tokenBalance": tokens})

    [account] = collect_rfab(environ=KEY_ENV)
    assert account.balance_usd == pytest.approx(expected_usd)
    assert account.balance_usd is not None and account.balance_usd > 0.0
    assert account.windows[0].remaining_percent == 100.0
    assert display_usd in (account.windows[0].reset_description or "")

    now = utcnow()
    snap = Snapshot(collected_at=now, accounts=[account])
    snap_dict = snap.to_dict()
    enrich_snapshot(snap_dict, now=now)
    avail = available_pools(snap_dict)
    assert len(avail) == 1
    assert avail[0]["usable_now"] is True
    assert avail[0]["balance_usd"] == pytest.approx(expected_usd)


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_redirects_are_disabled_and_rejected_cleanly(monkeypatch, status_code):
    calls = _serve(monkeypatch, {"location": "https://evil.example/steal"}, status_code=status_code)

    with pytest.raises(CollectorError, match=f"unexpected redirect \\(HTTP {status_code}\\)") as exc_info:
        collect_rfab(environ=KEY_ENV)
    assert calls[0][3] is False
    assert SECRET_KEY not in str(exc_info.value)


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


# --- Auth, malformed payload, exception-chain hygiene & network errors --------


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
        ({"success": True, "tokenBalance": 10**400}, 200, "missing numeric tokenBalance"),
        (["not", "a", "dict"], 200, "JSON object"),
        (requests.exceptions.JSONDecodeError("Expecting value", "", 0), 200, "invalid JSON"),
        (ValueError("bad json"), 200, "invalid JSON"),
        ({}, 500, "HTTP 500"),
    ],
)
def test_malformed_or_failed_responses_raise_collector_error(monkeypatch, payload, status, match):
    _serve(monkeypatch, payload, status_code=status)

    with pytest.raises(CollectorError, match=match) as exc_info:
        collect_rfab(environ=KEY_ENV)
    assert exc_info.value.__cause__ is None
    assert SECRET_KEY not in str(exc_info.value)


def test_oversized_optional_counters_do_not_crash_parsing(monkeypatch):
    _serve(
        monkeypatch,
        {
            "success": True,
            "tokenBalance": 50000,
            "totalTokensUsed": 10**400,
            "totalInteractions": 10**400,
        },
    )
    [account] = collect_rfab(environ=KEY_ENV)
    assert account.balance_usd == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("exc", "expected_name"),
    [
        (requests.Timeout(f"timed out with {SECRET_KEY}"), "Timeout"),
        (requests.ConnectionError(f"refused with {SECRET_KEY}"), "ConnectionError"),
        (requests.exceptions.InvalidHeader(f"Invalid header value {SECRET_KEY}"), "InvalidHeader"),
    ],
)
def test_timeout_and_network_errors_raise_without_leaking_key_or_traceback_cause(monkeypatch, exc, expected_name):
    def fake_get(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(requests, "get", fake_get)

    formatted_tb = ""
    with pytest.raises(CollectorError, match=expected_name) as exc_info:
        try:
            collect_rfab(environ=KEY_ENV)
        except CollectorError:
            formatted_tb = traceback.format_exc()
            raise
    assert exc_info.value.__cause__ is None
    assert SECRET_KEY not in str(exc_info.value)
    assert SECRET_KEY not in formatted_tb


@pytest.mark.parametrize("bad_key", [f"{SECRET_KEY}\nX-Evil: 1", f"{SECRET_KEY}\r\nX-Evil: 1", f"{SECRET_KEY}\x00tail"])
def test_multiline_or_control_char_key_rejected_before_http_without_traceback_leak(monkeypatch, bad_key):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("invalid key must not reach requests.get"))

    formatted_tb = ""
    with pytest.raises(CollectorError, match="invalid header characters") as exc_info:
        try:
            collect_rfab(environ={"RFAB_API_KEY": bad_key})
        except CollectorError:
            formatted_tb = traceback.format_exc()
            raise
    assert exc_info.value.__cause__ is None
    assert SECRET_KEY not in str(exc_info.value)
    assert SECRET_KEY not in formatted_tb


# --- Key resolution & managed sudo-secretspec only ----------------------------


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


def test_uses_sudo_secretspec_with_safe_subprocess_kwargs(monkeypatch):
    monkeypatch.delenv("AIUSE_RFAB_API_KEY", raising=False)
    monkeypatch.delenv("RFAB_API_KEY", raising=False)

    seen_calls: list[tuple[list[str], dict]] = []

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/sudo-secretspec" if name == "sudo-secretspec" else None

    def fake_run(cmd, **kwargs):
        seen_calls.append((list(cmd), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout=f"{SECRET_KEY}\n")

    calls = _serve(monkeypatch, {"success": True, "tokenBalance": 100000})
    monkeypatch.setattr("aiuse.collectors.rfab.shutil.which", fake_which)
    monkeypatch.setattr("aiuse.collectors.rfab.subprocess.run", fake_run)

    [account] = collect_rfab(timeout=15.0)

    assert len(seen_calls) == 1
    cmd, kwargs = seen_calls[0]
    assert cmd == ["/usr/local/bin/sudo-secretspec", "get", "RFAB_API_KEY", "--reason", "aiuse-rfab-balance"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert 0 < kwargs["timeout"] <= 10.0

    assert calls[0][1]["X-API-Key"] == SECRET_KEY
    assert calls[0][3] is False
    assert "Authorization" not in calls[0][1]
    assert account.balance_usd == pytest.approx(2.0)
    assert SECRET_KEY not in str(account)


@pytest.mark.parametrize(
    ("sudo_path", "run_result"),
    [
        (None, None),
        ("/usr/local/bin/sudo-secretspec", SimpleNamespace(returncode=1, stdout="")),
        ("/usr/local/bin/sudo-secretspec", SimpleNamespace(returncode=0, stdout="   \n")),
    ],
)
def test_never_falls_back_to_plain_secretspec_when_managed_lookup_missing_or_empty(monkeypatch, sudo_path, run_result):
    monkeypatch.delenv("AIUSE_RFAB_API_KEY", raising=False)
    monkeypatch.delenv("RFAB_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no request without a key"))

    def fake_which(name: str) -> str | None:
        if name == "sudo-secretspec":
            return sudo_path
        if name == "secretspec":
            return "/usr/bin/secretspec"
        return None

    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        seen_cmds.append(list(cmd))
        assert "sudo-secretspec" in cmd[0], f"must not invoke unprivileged secretspec: {cmd}"
        return run_result

    monkeypatch.setattr("aiuse.collectors.rfab.shutil.which", fake_which)
    monkeypatch.setattr("aiuse.collectors.rfab.subprocess.run", fake_run)

    assert collect_rfab() == []
    if sudo_path is None:
        assert seen_cmds == []
    else:
        assert len(seen_cmds) == 1


# --- Config, validation, usage-sources, fingerprint & cached --available ------


def _only_rfab_collectors(extra: dict | None = None) -> dict:
    cfg = {name: {"enabled": name == "rfab"} for name in KNOWN_COLLECTOR_KEYS}
    if extra:
        cfg["rfab"] = {"enabled": True, **extra}
    return cfg


def test_config_validation_and_defaults():
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
            "plans": {"rfab": {"operator_gated": True, "requires_operator_approval": False}},
        }
    )
    assert issues == []

    bad_collector = validate_config({"collectors": {"rfab": {"enabled": True, "operator_gated": "yes"}}})
    assert any("collectors.rfab.operator_gated must be a boolean" in item for item in bad_collector)

    bad_plan = validate_config({"plans": {"rfab": {"operator_gated": "true", "requires_operator_approval": "yes"}}})
    assert any("plans.rfab.operator_gated must be a boolean" in item for item in bad_plan)
    assert any("plans.rfab.requires_operator_approval must be a boolean" in item for item in bad_plan)


def test_usage_sources_recognizes_both_rfab_env_vars():
    for env_key in ("AIUSE_RFAB_API_KEY", "RFAB_API_KEY"):
        report = usage_source_report({}, environ={env_key: SECRET_KEY})
        rfab_row = next(p for p in report["providers"] if p["provider"] == "rfab")
        rfab_src = next(s for s in rfab_row["sources"] if s["id"] == "rfab")
        assert rfab_src["status"] == "active"
        assert env_key in rfab_src["detail"]

    unset = usage_source_report({}, environ={})
    rfab_row = next(p for p in unset["providers"] if p["provider"] == "rfab")
    rfab_src = next(s for s in rfab_row["sources"] if s["id"] == "rfab")
    assert rfab_src["status"] == "enabled"
    assert "AIUSE_RFAB_API_KEY" in rfab_src["detail"]
    assert "RFAB_API_KEY" in rfab_src["detail"]


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
    "gated_config",
    [
        {"collectors": _only_rfab_collectors({"operator_gated": True})},
        {"collectors": _only_rfab_collectors({"requires_operator_approval": True})},
        {"collectors": _only_rfab_collectors(), "operator_gated": {"rfab": True}},
        {"collectors": _only_rfab_collectors(), "operator_gated": {"rfab": "operator approval required"}},
        {"collectors": _only_rfab_collectors(), "plans": {"rfab": {"operator_gated": True}}},
        {"collectors": _only_rfab_collectors(), "plans": {"rfab": {"requires_operator_approval": True}}},
    ],
)
def test_operator_gate_config_compatibility_and_fingerprint(monkeypatch, gated_config):
    baseline_cfg = {"collectors": _only_rfab_collectors()}
    base_fp = collection_policy_fingerprint(baseline_cfg)
    gated_fp = collection_policy_fingerprint(gated_config)
    assert gated_fp != base_fp
    assert snapshot_matches_policy({"config_fingerprint": base_fp}, gated_config) is False
    assert snapshot_matches_policy({"config_fingerprint": gated_fp}, gated_config) is True

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

    snap = runner.run_collectors(gated_config)
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


@pytest.mark.parametrize(
    "config_toml",
    [
        "[collectors.rfab]\nenabled = true\noperator_gated = true\n",
        "[operator_gated]\nrfab = true\n",
        "[plans.rfab]\nrequires_operator_approval = true\n",
    ],
)
def test_cached_available_json_applies_newly_configured_gate_without_clearing_existing(
    monkeypatch, tmp_path, capsys, config_toml
):
    _serve(monkeypatch, {"success": True, "tokenBalance": 633020})
    [rfab_account] = collect_rfab(environ=KEY_ENV)
    [pre_gated_account] = collect_rfab(environ=KEY_ENV, requires_operator_approval=True)
    pre_gated_account.provider = "deepseek"
    pre_gated_account.source = "deepseek"
    pre_gated_account.windows[0].label = "deepseek balance"
    pre_gated_account.windows[0].window_id = "deepseek-balance"

    now = utcnow()
    snap = Snapshot(collected_at=now, accounts=[rfab_account, pre_gated_account])
    history.save_snapshot(snap, [])

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(config_toml, encoding="utf-8")

    # Never invoke live collectors during cached --available
    monkeypatch.setattr(runner, "run_collectors", lambda *_a, **_k: pytest.fail("should read cached latest.json"))

    rc = cli.main(["--config", str(cfg_path), "--available", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "cache"

    by_provider = {entry["provider"]: entry for entry in payload["available"]}
    assert by_provider["rfab"]["requires_operator_approval"] is True
    assert by_provider["rfab"]["ai_notice"] == OPERATOR_APPROVAL_NOTICE
    # Independently recorded gate on cached account is preserved even when config does not gate deepseek.
    assert by_provider["deepseek"]["requires_operator_approval"] is True
    assert by_provider["deepseek"]["ai_notice"] == OPERATOR_APPROVAL_NOTICE
