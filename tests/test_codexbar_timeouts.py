"""aiuse-e9d: a hanging CodexBar provider must not cost its timeout on every run."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from aiuse.collectors import codexbar
from aiuse.collectors.base import CollectorTimeout
from aiuse.collectors.runner import _codexbar_timeout_settings
from aiuse.collectors.throttle import TimeoutBackoff
from aiuse.config import validate_config
from aiuse.models import utcnow

OK_ROW = {
    "provider": "codex",
    "usage": {"primary": {"usedPercent": 10, "windowMinutes": 300, "resetsAt": "2099-01-01T00:00:00Z"}},
}


@pytest.fixture
def fake_codexbar(monkeypatch):
    """Discovery returns codex + alibabatokenplan; alibabatokenplan hangs."""
    calls: list[tuple[str, float]] = []
    hanging = {"alibabatokenplan"}

    def fake_run_json(argv, *, timeout=45.0, allow_empty=False):
        if "config" in argv:
            return [{"provider": "codex", "enabled": True}, {"provider": "alibabatokenplan", "enabled": True}]
        provider = argv[argv.index("--provider") + 1]
        calls.append((provider, timeout))
        if provider in hanging:
            raise CollectorTimeout(f"timed out after {timeout}s: codexbar usage --provider {provider}")
        return [dict(OK_ROW, provider=provider)]

    monkeypatch.setattr(codexbar, "which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr(codexbar, "run_json", fake_run_json)
    return calls, hanging


def _errors(accounts):
    return [a.error for a in accounts if a.provider == "codexbar-query-errors"]


def test_provider_timeouts_override_the_collector_timeout(fake_codexbar):
    calls, hanging = fake_codexbar
    hanging.clear()
    codexbar.collect_codexbar(timeout=45.0, provider_timeouts={"AlibabaTokenPlan": 8})
    assert dict(calls) == {"codex": 45.0, "alibabatokenplan": 8.0}


def test_hanging_provider_is_skipped_on_the_next_run(fake_codexbar):
    calls, _hanging = fake_codexbar
    first = codexbar.collect_codexbar(timeout=45.0, timeout_backoff=1800)
    assert "timed out after 45.0s" in _errors(first)[0]
    assert [p for p, _ in calls].count("alibabatokenplan") == 1

    second = codexbar.collect_codexbar(timeout=45.0, timeout_backoff=1800)
    # No second subprocess for the hung provider; the healthy one still runs.
    assert [p for p, _ in calls].count("alibabatokenplan") == 1
    assert [p for p, _ in calls].count("codex") == 2
    error = _errors(second)[0]
    assert error.startswith("alibabatokenplan: skipped: query timed out (45s) once")
    assert "next try in 30m" in error or "next try in 29m" in error
    assert {a.provider for a in second if not a.error} == {"codex"}


def test_backoff_disabled_queries_every_run(fake_codexbar):
    calls, _hanging = fake_codexbar
    codexbar.collect_codexbar(timeout=45.0, timeout_backoff=0)
    codexbar.collect_codexbar(timeout=45.0, timeout_backoff=0)
    assert [p for p, _ in calls].count("alibabatokenplan") == 2


def test_success_after_backoff_clears_the_record(fake_codexbar, tmp_path):
    _calls, hanging = fake_codexbar
    codexbar.collect_codexbar(timeout=45.0, timeout_backoff=1800)
    backoff = TimeoutBackoff("codexbar", base=1800)
    assert backoff.blocked("alibabatokenplan") is not None
    # Expire the window, let the provider answer, and the record is gone.
    state_path = backoff.directory / "codexbar-timeouts.json"
    state = json.loads(state_path.read_text())
    state["alibabatokenplan"]["timed_out_at"] = (utcnow() - timedelta(hours=1)).isoformat()
    state_path.write_text(json.dumps(state))
    hanging.clear()
    accounts = codexbar.collect_codexbar(timeout=45.0, timeout_backoff=1800)
    assert not _errors(accounts)
    assert "alibabatokenplan" not in json.loads(state_path.read_text())


def test_backoff_doubles_per_consecutive_timeout_and_caps(tmp_path):
    backoff = TimeoutBackoff("codexbar", base=100, cap=250, directory=tmp_path)
    assert backoff._window(1) == 100
    assert backoff._window(2) == 200
    assert backoff._window(3) == 250
    backoff.record_timeout("devin", 45)
    backoff.record_timeout("devin", 45)
    reason = backoff.blocked("devin")
    assert reason is not None and "2 times in a row" in reason


def test_non_timeout_errors_do_not_start_a_backoff(tmp_path):
    backoff = TimeoutBackoff("codexbar", base=100, directory=tmp_path)
    backoff.clear("alibaba")  # no state file yet: harmless
    assert backoff.blocked("alibaba") is None
    assert not (tmp_path / "codexbar-timeouts.json").exists()


def test_runner_settings_force_timeout_beats_provider_timeouts():
    cfg = {"collectors": {"codexbar": {"provider_timeouts": {"devin": 20, "bad": "x", "zero": 0}}}}
    timeouts, backoff = _codexbar_timeout_settings(cfg, cfg["collectors"]["codexbar"])
    assert timeouts == {"devin": 20.0}
    assert backoff == codexbar.DEFAULT_TIMEOUT_BACKOFF_SECONDS

    forced = dict(cfg, timeouts={"force": 5})
    timeouts, _ = _codexbar_timeout_settings(forced, cfg["collectors"]["codexbar"])
    assert timeouts == {}

    _, backoff = _codexbar_timeout_settings({}, {"timeout_backoff": 0})
    assert backoff == 0


def test_validate_config_checks_the_new_codexbar_keys():
    clean = validate_config({"collectors": {"codexbar": {"provider_timeouts": {"devin": 20}, "timeout_backoff": 0}}})
    assert not [i for i in clean if "codexbar" in i]
    bad = validate_config({"collectors": {"codexbar": {"provider_timeouts": {"devin": -1}, "timeout_backoff": "x"}}})
    assert any("provider_timeouts.devin" in i for i in bad)
    assert any("timeout_backoff" in i for i in bad)
