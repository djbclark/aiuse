"""Cross-process minimum interval between live quota queries (agy / antigravity)."""

from __future__ import annotations

import json
from datetime import timedelta

from aiuse.collectors import codexbar, openusage
from aiuse.collectors.base import CollectorError
from aiuse.collectors.throttle import QueryGate, min_intervals, throttle_dir
from aiuse.config import DEFAULT_CONFIG, validate_config
from aiuse.models import utcnow

_AGY_ROW = {
    "provider": "antigravity",
    "source": "local",
    "usage": {"primary": {"usedPercent": 10, "windowMinutes": 300}},
}


def _backdate(provider: str, seconds: float) -> None:
    path = throttle_dir() / f"{provider}.json"
    state = json.loads(path.read_text())
    state["queried_at"] = (utcnow() - timedelta(seconds=seconds)).isoformat()
    path.write_text(json.dumps(state))


def test_antigravity_defaults_to_fifteen_minutes():
    assert DEFAULT_CONFIG["query_min_interval"]["antigravity"] == 900
    assert min_intervals({}) == {"antigravity": 900.0}
    assert min_intervals(DEFAULT_CONFIG) == {"antigravity": 900.0}


def test_config_can_change_or_disable_a_limit():
    assert min_intervals({"query_min_interval": {"antigravity": 1800}}) == {"antigravity": 1800.0}
    assert min_intervals({"query_min_interval": {"antigravity": 0}}) == {}
    assert min_intervals({"query_min_interval": {"Grok": 60}}) == {"antigravity": 900.0, "grok": 60.0}


def test_validate_config_checks_query_min_interval():
    assert not [i for i in validate_config({"query_min_interval": {"antigravity": 900}}) if "query_min" in i]
    issues = validate_config({"query_min_interval": {"antigravity": "soon", "grok": -1}})
    assert any("query_min_interval.antigravity must be a number" in i for i in issues)
    assert any("query_min_interval.grok must not be negative" in i for i in issues)
    assert any("must be a provider -> seconds mapping" in i for i in validate_config({"query_min_interval": 5}))


def test_gate_allows_one_query_per_interval_and_reuses_the_payload():
    with QueryGate("antigravity", min_interval=900, wait=0) as gate:
        assert gate.allowed
        gate.record("codexbar", [_AGY_ROW])

    with QueryGate("antigravity", min_interval=900, wait=0) as gate:
        assert not gate.allowed
        assert gate.reuse("codexbar") == [_AGY_ROW]
        assert "at most every 15m" in gate.describe()
        assert "via codexbar" in gate.describe()

    _backdate("antigravity", 901)
    with QueryGate("antigravity", min_interval=900, wait=0) as gate:
        assert gate.allowed


def test_gate_held_by_another_process_counts_as_throttled():
    with QueryGate("antigravity", min_interval=900, wait=0) as first:
        assert first.allowed
        with QueryGate("antigravity", min_interval=900, wait=0) as second:
            assert not second.allowed
            assert "another aiuse process" in second.describe()
            assert isinstance(second.reuse("codexbar"), CollectorError)


def test_codexbar_queries_antigravity_once_per_interval(monkeypatch):
    calls: list[str | None] = []

    def fake_query(provider_arg, *, timeout=45.0):
        calls.append(provider_arg)
        return [dict(_AGY_ROW, provider=provider_arg)]

    monkeypatch.setattr(codexbar, "_query_provider", fake_query)
    limits = {"antigravity": 900.0}

    first_notes: dict[str, str] = {}
    first = codexbar._query_providers(["codex", "antigravity"], min_intervals=limits, reuse_notes=first_notes)
    second_notes: dict[str, str] = {}
    second = codexbar._query_providers(["codex", "antigravity"], min_intervals=limits, reuse_notes=second_notes)

    assert calls.count("antigravity") == 1
    assert calls.count("codex") == 2
    assert dict(second)["antigravity"] == dict(first)["antigravity"]
    assert first_notes == {}
    assert "reused CodexBar's result" in second_notes["antigravity"]


def test_codexbar_reused_rows_carry_the_throttle_note(monkeypatch):
    monkeypatch.setattr(codexbar, "which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr(codexbar, "_query_provider", lambda provider_arg, *, timeout=45.0: [_AGY_ROW])

    codexbar.collect_codexbar(providers="antigravity", min_intervals={"antigravity": 900.0})
    accounts = codexbar.collect_codexbar(providers="antigravity", min_intervals={"antigravity": 900.0})

    assert [a.provider for a in accounts] == ["antigravity"]
    assert any("queried at most every 15m" in note for note in accounts[0].notes)


def test_a_failed_antigravity_query_still_counts(monkeypatch):
    calls: list[str | None] = []

    def failing_query(provider_arg, *, timeout=45.0):
        calls.append(provider_arg)
        return CollectorError("HTTP 429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(codexbar, "_query_provider", failing_query)
    limits = {"antigravity": 900.0}
    codexbar._query_providers(["antigravity"], min_intervals=limits)
    [(_, outcome)] = codexbar._query_providers(["antigravity"], min_intervals=limits)

    assert calls == ["antigravity"]
    assert isinstance(outcome, CollectorError)
    assert "429" in str(outcome) and "next allowed in" in str(outcome)


def test_codexbar_without_limits_is_unchanged(monkeypatch):
    calls: list[str | None] = []
    monkeypatch.setattr(
        codexbar, "_query_provider", lambda provider_arg, *, timeout=45.0: calls.append(provider_arg) or [_AGY_ROW]
    )
    codexbar._query_providers(["antigravity"])
    codexbar._query_providers(["antigravity"])
    assert calls == ["antigravity", "antigravity"]
    assert not (throttle_dir() / "antigravity.json").exists()


def test_openusage_withholds_force_while_antigravity_is_throttled(monkeypatch):
    forced: list[bool] = []
    payload = {
        "schema": "openusage.limits.v1",
        "providers": {
            "antigravity": {
                "displayName": "Antigravity",
                "resources": {"geminiSession": {"kind": "consumption", "unit": "percent", "used": 10}},
            },
            "codex": {
                "displayName": "Codex",
                "resources": {"session": {"kind": "consumption", "unit": "percent", "used": 5}},
            },
        },
    }

    def fake_fetch(*, timeout, base_url, force_refresh, try_launch_app):
        forced.append(force_refresh)
        return payload, "cli"

    monkeypatch.setattr(openusage, "app_cli_path", lambda: "/usr/local/bin/openusage")
    monkeypatch.setattr(openusage, "_fetch_limits", fake_fetch)
    limits = {"antigravity": 900.0}

    openusage.collect_openusage_ai(min_intervals=limits)
    accounts = openusage.collect_openusage_ai(min_intervals=limits)

    assert forced == [True, False]
    by_provider = {a.provider: a for a in accounts}
    assert any("refresh not forced" in n for n in by_provider["antigravity"].notes)
    assert not any("refresh not forced" in n for n in by_provider["codex"].notes)


def test_openusage_force_without_antigravity_does_not_spend_the_slot(monkeypatch):
    payload = {
        "providers": {
            "codex": {
                "displayName": "Codex",
                "resources": {"session": {"kind": "consumption", "unit": "percent", "used": 5}},
            }
        }
    }
    monkeypatch.setattr(openusage, "app_cli_path", lambda: "/usr/local/bin/openusage")
    monkeypatch.setattr(openusage, "_fetch_limits", lambda **_kw: (payload, "cli"))
    openusage.collect_openusage_ai(min_intervals={"antigravity": 900.0})

    with QueryGate("antigravity", min_interval=900, wait=0) as gate:
        assert gate.allowed


# --- issue #34: cheap source first, back off a provider that keeps returning nothing


def test_antigravity_tries_oauth_before_the_agy_cli(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_json(argv, *, timeout=45.0, allow_empty=False):
        calls.append(list(argv))
        return [dict(_AGY_ROW, source="oauth")]

    monkeypatch.setattr(codexbar, "run_json", fake_run_json)
    outcome = codexbar._query_provider("antigravity")
    assert outcome[0]["source"] == "oauth"
    # One CodexBar call, with --source oauth: no agy spawn via the CLI source.
    assert len(calls) == 1 and calls[0][-2:] == ["--source", "oauth"]


def test_antigravity_falls_back_to_auto_when_oauth_has_no_credentials(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_json(argv, *, timeout=45.0, allow_empty=False):
        calls.append(list(argv))
        if "--source" in argv:
            return [{"provider": "antigravity", "error": {"message": "No stored Google credentials"}}]
        return [_AGY_ROW]

    monkeypatch.setattr(codexbar, "run_json", fake_run_json)
    assert codexbar._query_provider("antigravity") == [_AGY_ROW]
    assert ["--source" in argv for argv in calls] == [True, False]


def _streak(provider: str) -> int:
    return json.loads((throttle_dir() / f"{provider}.json").read_text()).get("empty_streak", 0)


def test_empty_answers_stretch_the_interval_and_a_usable_one_resets_it(monkeypatch):
    answers: list[object] = []
    monkeypatch.setattr(codexbar, "_query_provider", lambda provider_arg, *, timeout=45.0: answers.pop(0))
    limits = {"antigravity": 900.0}
    empty = [{"provider": "antigravity", "error": {"message": "not logged in"}}]

    for expected_streak in (1, 2, 3):
        answers.append(empty)
        codexbar._query_providers(["antigravity"], min_intervals=limits)
        assert _streak("antigravity") == expected_streak
        _backdate("antigravity", 901)

    # Third empty answer in a row: the 15m interval is now 1h, so 901s is not enough.
    with QueryGate("antigravity", min_interval=900, wait=0) as gate:
        assert gate.interval == 3600
        assert not gate.allowed
        assert "stretched to 1h00m after 3 answers in a row with no usable data" in gate.describe()

    _backdate("antigravity", 3601)
    answers.append([_AGY_ROW])
    codexbar._query_providers(["antigravity"], min_intervals=limits)
    assert _streak("antigravity") == 0


def test_empty_streak_backoff_is_capped():
    from aiuse.collectors.throttle import EMPTY_BACKOFF_CAP

    gate = QueryGate("antigravity", min_interval=900, wait=0)
    gate.state = {"empty_streak": 50}
    assert gate.interval == EMPTY_BACKOFF_CAP
    gate.state = {"empty_streak": 2}
    assert gate.interval == 900
    # A configured interval longer than the cap is never shortened.
    long_gate = QueryGate("antigravity", min_interval=EMPTY_BACKOFF_CAP * 2, wait=0)
    long_gate.state = {"empty_streak": 10}
    assert long_gate.interval == EMPTY_BACKOFF_CAP * 2


def test_openusage_records_leave_the_streak_alone():
    with QueryGate("antigravity", min_interval=900, wait=0) as gate:
        gate.state["empty_streak"] = 2
        gate.record("openusage_ai", store=False)
    assert _streak("antigravity") == 2
