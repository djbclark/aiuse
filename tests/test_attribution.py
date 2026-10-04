"""Token ledgers, adaptive sampling, and the `aiuse attribute` join."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from aiuse import attribute, ledger, sampler
from aiuse.analysis import history
from aiuse.config import DEFAULT_CONFIG, validate_config
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot

T0 = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
SETTINGS = sampler.sampling_settings(None)


def _snapshot(when: datetime, used: dict[str, float], *, provider: str = "clinepass", source: str = "codexbar"):
    minutes = {"5-hour": 300, "weekly": 10080, "monthly": 43200}
    windows = [
        QuotaWindow(label=f"ClinePass {name}", used_percent=value, window_minutes=minutes[name])
        for name, value in used.items()
    ]
    account = AccountUsage(
        source=source, provider=provider, billing_kind=BillingKind.SUBSCRIPTION_WINDOW, windows=windows
    )
    return Snapshot(collected_at=when, accounts=[account])


def _tok(client: str, model: str, provider: str, tokens: int, cost: float = 0.0) -> dict[str, Any]:
    return {
        "client": client,
        "model": model,
        "provider": provider,
        "input": tokens,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "reasoning": 0,
        "messageCount": 1,
        "cost": cost,
    }


# --- ledger -----------------------------------------------------------------


def test_capture_reads_only_today_without_a_previous_sample():
    calls: list[list[str]] = []

    def run(argv, timeout):
        calls.append(argv)
        return {"entries": [_tok("hermes", "m3", "cline", 100)]}

    sample = ledger.capture_tokscale_ledger(now=T0, run=run)
    today = T0.astimezone().date().isoformat()
    assert list(sample["days"]) == [today]
    assert calls[0][-4:] == ["--since", today, "--until", today]
    assert sample["days"][today][0]["cache_read"] == 0 and sample["days"][today][0]["messages"] == 1


def test_capture_backfills_the_day_the_previous_sample_was_taken_on():
    today = T0.astimezone().date()
    previous = {"days": {(today - timedelta(days=1)).isoformat(): []}}
    seen: list[str] = []

    def run(argv, timeout):
        seen.append(argv[argv.index("--since") + 1])
        return {"entries": []}

    ledger.capture_tokscale_ledger(now=T0, previous=previous, run=run)
    assert seen == [(today - timedelta(days=1)).isoformat(), today.isoformat()]


def test_delta_subtracts_per_day_and_survives_midnight():
    row = {"client": "hermes", "model": "m3", "provider": "cline", "output": 0, "cache_read": 0, "cache_write": 0}
    prev = {"days": {"2026-09-25": [{**row, "input": 100, "messages": 2, "cost": 1.0, "reasoning": 0}]}}
    cur = {
        "days": {
            "2026-09-25": [{**row, "input": 180, "messages": 3, "cost": 1.5, "reasoning": 0}],  # final total
            "2026-09-26": [{**row, "input": 40, "messages": 1, "cost": 0.25, "reasoning": 0}],  # new day from zero
        }
    }
    (delta,) = ledger.ledger_delta(prev, cur)
    assert delta["input"] == 120 and delta["messages"] == 2 and delta["cost"] == pytest.approx(0.75)


def test_delta_clamps_a_shrinking_total_and_drops_idle_rows():
    row = {"client": "claude", "model": "opus", "provider": "anthropic"}
    prev = {"days": {"d": [{**row, "input": 500}]}}
    cur = {"days": {"d": [{**row, "input": 300}]}}  # a session file was deleted
    assert ledger.ledger_delta(prev, cur) == []


def test_tokscale_provider_mapping_prefers_overrides_then_client_then_upstream():
    assert ledger.tokscale_row_provider({"client": "claude", "provider": "anthropic"}) == "claude"
    # Antigravity sells Claude out of Google's pool: the client decides, not the model vendor.
    assert ledger.tokscale_row_provider({"client": "antigravity-cli", "provider": "anthropic"}) == "antigravity"
    assert ledger.tokscale_row_provider({"client": "hermes", "provider": "deepseek, opencode_go"}) == "deepseek"
    assert ledger.tokscale_row_provider({"client": "hermes", "provider": "moa"}) is None
    assert ledger.tokscale_row_provider({"client": "hermes", "provider": "moa"}, {"hermes/*": "grok"}) == "grok"


def test_litellm_provider_mapping():
    assert (
        ledger.litellm_row_provider({"model": "x", "upstream": "clinepass/minimax-m3", "api_base": None}) == "clinepass"
    )
    go = {"model": "go-kimi", "upstream": "openai/kimi-k3", "api_base": "https://opencode.ai/zen/go/v1/"}
    assert ledger.litellm_row_provider(go) == "opencode-go"
    bridge = {"model": "grok-sub", "upstream": "openai/grok", "api_base": "http://127.0.0.1:14011/v1"}
    assert ledger.litellm_row_provider(bridge) is None
    assert ledger.litellm_row_provider(bridge, {"grok-*": "grok"}) == "grok"


def test_litellm_query_passes_the_range_as_psql_variables():
    captured: dict[str, Any] = {}

    class Proc:
        returncode = 0
        stdout = '[{"minute": "2026-09-25T14:01:00+00:00", "client": "hermes"}]'
        stderr = ""

    def run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return Proc()

    rows = ledger.query_litellm_ledger({"database_url": "postgresql://x/litellm"}, T0, T0 + timedelta(hours=1), run=run)
    assert rows[0]["client"] == "hermes"
    assert "start=2026-09-25 14:00:00" in captured["argv"] and "stop=2026-09-25 15:00:00" in captured["argv"]
    assert ":'start'" in captured["input"] and "2026-09-25" not in captured["input"]


# --- sampler ----------------------------------------------------------------


def _state(tier: str, last_sample: datetime, last_full: datetime, **extra: Any) -> dict[str, Any]:
    return {"tier": tier, "last_sample_at": last_sample.isoformat(), "last_full_at": last_full.isoformat(), **extra}


def test_decide_first_run_and_force_collect_fully():
    assert sampler.decide({}, SETTINGS, T0).action == "full"
    state = _state("idle", T0, T0)
    assert sampler.decide(state, SETTINGS, T0 + timedelta(minutes=1), force=True).action == "full"


def test_decide_respects_each_tiers_interval():
    idle = _state("idle", T0, T0)
    assert sampler.decide(idle, SETTINGS, T0 + timedelta(minutes=30)).action == "skip"
    assert sampler.decide(idle, SETTINGS, T0 + timedelta(minutes=60)).action == "full"
    active = _state("active", T0, T0)
    assert sampler.decide(active, SETTINGS, T0 + timedelta(minutes=10)).action == "skip"
    assert sampler.decide(active, SETTINGS, T0 + timedelta(minutes=15)).action == "full"


def test_decide_burst_samples_hot_providers_until_a_full_collection_is_due():
    hot = [{"provider": "clinepass", "source": "codexbar"}]
    state = _state("burst", T0 + timedelta(minutes=3), T0, hot=hot)
    partial = sampler.decide(state, SETTINGS, T0 + timedelta(minutes=6))
    assert partial.action == "partial" and partial.hot == hot
    state = _state("burst", T0 + timedelta(minutes=12), T0, hot=hot)
    assert sampler.decide(state, SETTINGS, T0 + timedelta(minutes=15)).action == "full"


def test_a_weekly_window_at_14_points_an_hour_is_a_burst():
    state = sampler.advance_state({}, _snapshot(T0, {"weekly": 4, "monthly": 50}), SETTINGS, partial=False)
    assert state["tier"] == "idle"
    later = _snapshot(T0 + timedelta(minutes=18), {"weekly": 8, "monthly": 52})
    state = sampler.advance_state(state, later, SETTINGS, partial=False)
    assert state["tier"] == "burst"
    assert state["hot"] == [{"provider": "clinepass", "source": "codexbar"}]


def test_a_five_hour_window_at_its_ordinary_pace_is_active_not_burst():
    state = sampler.advance_state({}, _snapshot(T0, {"5-hour": 10}), SETTINGS, partial=False)
    # 5 points in 15 minutes = 20/h, exactly the pace that fills the window.
    state = sampler.advance_state(state, _snapshot(T0 + timedelta(minutes=15), {"5-hour": 15}), SETTINGS, partial=False)
    assert state["tier"] == "active" and state["hot"] == []


def test_tier_steps_down_one_level_only_after_the_cooldown():
    state = sampler.advance_state({}, _snapshot(T0, {"weekly": 4}), SETTINGS, partial=False)
    jump = T0 + timedelta(minutes=15)
    state = sampler.advance_state(state, _snapshot(jump, {"weekly": 9}), SETTINGS, partial=False)
    assert state["tier"] == "burst"
    tiers = []
    for n in range(1, 6):  # flat from here on, sampled every 3 minutes
        flat = _snapshot(jump + timedelta(minutes=3 * n), {"weekly": 9})
        state = sampler.advance_state(state, flat, SETTINGS, partial=True)
        tiers.append((state["tier"], state["quiet_samples"], bool(state["hot"])))
    # Still a burst while the baseline is the pre-jump reading; once the
    # baseline is the jump itself the samples are quiet, and the third quiet
    # one steps down a single tier.
    assert tiers == [
        ("burst", 0, True),
        ("burst", 0, True),
        ("burst", 1, True),
        ("burst", 2, True),
        ("active", 0, False),
    ]


def test_one_tick_between_close_samples_is_not_a_burst():
    """Whole-point meters: +1 in 4 minutes reads as 15/h but is ordinary use."""
    state = sampler.advance_state({}, _snapshot(T0, {"weekly": 54}), SETTINGS, partial=False)
    close = _snapshot(T0 + timedelta(minutes=4), {"weekly": 55})
    state = sampler.advance_state(state, close, SETTINGS, partial=False)
    assert state["tier"] == "active" and state["hot"] == []
    # One more tick 15 minutes later is 6.3/h over the 19-minute baseline: still not a burst.
    later = _snapshot(T0 + timedelta(minutes=19), {"weekly": 56})
    state = sampler.advance_state(state, later, SETTINGS, partial=False)
    assert state["tier"] == "active" and state["hot"] == []


def test_a_sustained_sprint_stays_in_burst_at_the_three_minute_cadence():
    """14 points an hour arrives as whole-point ticks; the tier must not flap."""
    state = sampler.advance_state({}, _snapshot(T0, {"weekly": 4}), SETTINGS, partial=False)
    state = sampler.advance_state(
        state, _snapshot(T0 + timedelta(minutes=15), {"weekly": 7.5}), SETTINGS, partial=False
    )
    assert state["tier"] == "burst"
    for n in range(1, 11):
        minutes = 15 + 3 * n
        used = float(int(4 + 14 * minutes / 60))  # the meter shows whole points
        state = sampler.advance_state(
            state, _snapshot(T0 + timedelta(minutes=minutes), {"weekly": used}), SETTINGS, partial=True
        )
        assert state["tier"] == "burst", minutes


def test_burst_baseline_does_not_reach_across_a_window_reset():
    state = sampler.advance_state({}, _snapshot(T0, {"5-hour": 5}), SETTINGS, partial=False)
    state = sampler.advance_state(state, _snapshot(T0 + timedelta(minutes=15), {"5-hour": 0}), SETTINGS, partial=False)
    # 0 → 6 in 4 minutes after a reset; the only older reading is from the last cycle.
    state = sampler.advance_state(state, _snapshot(T0 + timedelta(minutes=19), {"5-hour": 6}), SETTINGS, partial=False)
    assert state["tier"] == "active" and state["hot"] == []


def test_a_window_reset_is_not_movement():
    state = sampler.advance_state({}, _snapshot(T0, {"5-hour": 90}), SETTINGS, partial=False)
    state = sampler.advance_state(state, _snapshot(T0 + timedelta(minutes=15), {"5-hour": 0}), SETTINGS, partial=False)
    assert state["tier"] == "idle"


def test_partial_sample_keeps_other_providers_last_values():
    both = _snapshot(T0, {"weekly": 4})
    both.accounts.append(
        AccountUsage(source="cswap", provider="claude", windows=[QuotaWindow(label="Claude weekly", used_percent=40)])
    )
    state = sampler.advance_state({}, both, SETTINGS, partial=False)
    state = sampler.advance_state(state, _snapshot(T0 + timedelta(minutes=3), {"weekly": 6}), SETTINGS, partial=True)
    assert "claude||Claude weekly" in state["last_values"]
    assert state["last_full_at"] == T0.isoformat()


def test_partial_config_enables_only_the_hot_source_and_its_codexbar_spelling():
    config = load_config_from_defaults()
    cfg = sampler.partial_config(
        config,
        [{"provider": "opencode-go", "source": "codexbar"}],
        codexbar_enabled=lambda: ["claude", "opencodego", "clinepass"],
    )
    enabled = {name for name, entry in cfg["collectors"].items() if entry.get("enabled")}
    assert enabled == {"codexbar"}
    assert cfg["collectors"]["codexbar"]["providers"] == "opencodego"
    assert config["collectors"]["cswap"]["enabled"] is True, "the caller's config is not mutated"


def test_partial_config_does_not_re_enable_a_source_the_operator_disabled():
    config = load_config_from_defaults()
    config["collectors"]["cswap"] = {"enabled": False}
    cfg = sampler.partial_config(config, [{"provider": "claude", "source": "cswap"}])
    assert cfg["collectors"]["cswap"]["enabled"] is False


def load_config_from_defaults() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def test_partial_snapshot_goes_to_its_own_directory_and_never_becomes_latest():
    history.save_snapshot(_snapshot(T0, {"weekly": 4}), [])
    path = history.save_snapshot(
        _snapshot(T0 + timedelta(minutes=3), {"weekly": 6}), [], partial_providers=["clinepass"]
    )
    assert path.parent == history.partial_sample_dir()
    data = json.loads(path.read_text())
    assert data["complete"] is False and data["partial_providers"] == ["clinepass"]
    latest = json.loads((history.snapshot_dir() / "latest.json").read_text())
    assert latest["accounts"][0]["windows"][0]["used_percent"] == 4
    assert len(history.load_recent_snapshots(retention_days=10_000)) == 1


def test_run_sample_skips_then_collects_and_records_the_ledger(monkeypatch):
    monkeypatch.setattr(ledger, "ledger_enabled", lambda config=None: True)
    monkeypatch.setattr(
        ledger, "capture_tokscale_ledger", lambda **kw: {"captured_at": T0.isoformat(), "days": {"d": []}}
    )
    calls: list[dict[str, Any]] = []

    def collect(config):
        calls.append(config)
        return _snapshot(T0 + timedelta(hours=len(calls) - 1), {"weekly": 4.0 + 10 * (len(calls) - 1)})

    out = _Out()
    config = load_config_from_defaults()
    assert sampler.run_sample(config, now=T0, collect=collect, stdout=out) == 0
    assert sampler.run_sample(config, now=T0 + timedelta(minutes=3), collect=collect, stdout=out) == 0
    assert len(calls) == 1 and out.lines[-1].startswith("skip: idle tier")
    assert sampler.run_sample(config, now=T0 + timedelta(hours=1), collect=collect, stdout=out) == 0
    assert "tier=burst" in out.lines[-1] and "next=180s" in out.lines[-1]
    assert len(list(ledger.ledger_dir().glob("*.json"))) == 2
    assert sampler.load_state()["hot"] == [{"provider": "clinepass", "source": "codexbar"}]


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        if text.strip():
            self.lines.append(text.strip())

    def flush(self) -> None:
        pass


# --- attribute --------------------------------------------------------------


def _write_ledger(when: datetime, rows: list[dict[str, Any]]) -> None:
    day = when.astimezone().date().isoformat()
    sample = {"captured_at": when.isoformat(), "days": {day: [ledger._row(r) for r in rows]}}
    ledger.save_ledger(sample, collected_at=when)


def _seed(tmp_path: Path) -> None:
    """A sprint: weekly 4 → 12 over two intervals, Hermes then Crush spending."""
    history.save_snapshot(_snapshot(T0, {"5-hour": 0, "weekly": 4, "monthly": 50}), [])
    history.save_snapshot(_snapshot(T0 + timedelta(minutes=15), {"5-hour": 20, "weekly": 8, "monthly": 52}), [])
    history.save_snapshot(
        _snapshot(T0 + timedelta(minutes=18), {"5-hour": 2, "weekly": 12, "monthly": 54}),
        [],
        partial_providers=["clinepass"],
    )
    _write_ledger(T0, [_tok("hermes", "m3", "cline", 1_000, 1.0), _tok("claude", "opus", "anthropic", 50)])
    _write_ledger(
        T0 + timedelta(minutes=15), [_tok("hermes", "m3", "cline", 4_000, 4.0), _tok("claude", "opus", "anthropic", 50)]
    )
    _write_ledger(
        T0 + timedelta(minutes=18),
        [_tok("hermes", "m3", "cline", 4_000, 4.0), _tok("crush", "k3", "cline", 1_000, 1.0), _tok("x", "y", "moa", 7)],
    )


def test_report_sets_burn_beside_tokens_per_client(tmp_path):
    _seed(tmp_path)
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=1))
    (prov,) = report["providers"]
    assert prov["provider"] == "clinepass"
    windows = {w["label"]: w for w in prov["windows"]}
    assert windows["ClinePass weekly"]["burned_points"] == 8
    # 0 → 20, then a reset to 2: 20 burned before it and 2 after.
    assert windows["ClinePass 5-hour"]["burned_points"] == 22 and windows["ClinePass 5-hour"]["resets"] == 1
    by_client = {r["client"]: r for r in prov["tokscale"]}
    assert by_client["hermes"]["input"] == 3_000 and by_client["hermes"]["share"] == 0.75
    assert by_client["crush"]["input"] == 1_000
    assert "claude" not in by_client, "an idle client contributes no rows"
    # The rate is quoted against the longest window that moved a full point.
    assert prov["per_point"] == {
        "tokscale_window": "ClinePass monthly",
        "tokscale_points": 4.0,
        "tokscale_tokens": 1000,
        "tokscale_cost": 1.0,
    }
    coverage = report["coverage"]
    assert (coverage["quota_samples"], coverage["burst_samples"], coverage["ledger_samples"]) == (2, 1, 3)
    assert coverage["litellm"] == "off" and coverage["ledger_span"][1] == (T0 + timedelta(minutes=18)).isoformat()
    assert [r["client"] for r in report["unmapped"]["tokscale"]] == ["x"]


def _raw_snapshot(when: datetime, source: str, account: str | None, label: str, used: float, resets: datetime):
    window = QuotaWindow(label=label, used_percent=used, window_minutes=10080, resets_at=resets)
    return Snapshot(
        collected_at=when, accounts=[AccountUsage(source=source, provider="claude", account=account, windows=[window])]
    )


def test_two_sources_labelling_one_window_differently_are_one_series():
    resets = T0 + timedelta(days=3)
    history.save_snapshot(_raw_snapshot(T0, "cswap", "me@example.com", "Claude Code weekly", 40, resets), [])
    history.save_snapshot(_raw_snapshot(T0 + timedelta(hours=1), "openusage_ai", None, "Claude weekly", 43, resets), [])
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=2))
    (window,) = report["providers"][0]["windows"]
    assert window["burned_points"] == 3 and window["label"] == "Claude Code weekly"
    assert window["account"] == "me@example.com"


def test_a_fall_without_a_reset_time_change_is_a_bad_reading_not_a_reset():
    resets = T0 + timedelta(days=3)
    for minutes, used in ((0, 40), (60, 0), (120, 42)):  # a collector answered 0 once
        when = T0 + timedelta(minutes=minutes)
        history.save_snapshot(_raw_snapshot(when, "cswap", "me@example.com", "Claude Code weekly", used, resets), [])
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=3))
    (window,) = report["providers"][0]["windows"]
    assert window["burned_points"] == 2 and window["resets"] == 0


def test_a_fall_after_the_reset_moment_is_a_reset():
    first = T0 + timedelta(minutes=30)
    history.save_snapshot(_raw_snapshot(T0, "cswap", "me@example.com", "Claude Code weekly", 90, first), [])
    later = T0 + timedelta(hours=1)
    history.save_snapshot(
        _raw_snapshot(later, "cswap", "me@example.com", "Claude Code weekly", 5, first + timedelta(days=7)), []
    )
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=2))
    (window,) = report["providers"][0]["windows"]
    assert window["burned_points"] == 5 and window["resets"] == 1


def test_a_window_whose_duration_one_source_omits_is_still_one_series():
    """CodexBar reports Copilot's premium window without a duration; tokscale with one."""

    def copilot(when, source, account, minutes, used):
        window = QuotaWindow(label="GitHub Copilot premium requests", used_percent=used, window_minutes=minutes)
        return Snapshot(
            collected_at=when,
            accounts=[AccountUsage(source=source, provider="copilot", account=account, windows=[window])],
        )

    history.save_snapshot(copilot(T0, "tokscale", None, 43800, 20), [])
    history.save_snapshot(copilot(T0 + timedelta(hours=1), "codexbar", "me (Individual)", None, 22), [])
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=2))
    (window,) = report["providers"][0]["windows"]
    assert window["burned_points"] == 2 and window["account"] == "me (Individual)"


def test_report_intervals_line_each_step_up_with_who_spent(tmp_path):
    _seed(tmp_path)
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=1))
    first, second = report["providers"][0]["intervals"]
    assert first["burned"]["ClinePass weekly"] == 4 and [r["client"] for r in first["tokscale"]] == ["hermes"]
    assert [r["client"] for r in second["tokscale"]] == ["crush"]
    text = attribute.render_report(report, intervals=True)
    assert "burned   8.0 pts" in text and "hermes  m3" in text and "←  crush 1.0k" in text
    assert "no quota provider mapped" in text


def test_report_joins_the_litellm_spend_log_by_key_alias(tmp_path):
    _seed(tmp_path)
    config = {"attribution": {"litellm": {"database_url": "postgresql://x/litellm", "provider_map": {}}}}

    def query(settings, start, end, timeout):
        return [
            {
                "minute": (T0 + timedelta(minutes=5)).isoformat(),
                "client": "hindsight",
                "model": "clinepass-minimax-m3",
                "upstream": "clinepass/minimax-m3",
                "api_base": "https://api.cline.bot/api/v1/chat/completions",
                "requests": 10,
                "failures": 1,
                "input": 9_000,
                "output": 1_000,
                "reasoning": 400,
            }
        ]

    report = attribute.build_report(config, T0 - timedelta(minutes=1), T0 + timedelta(hours=1), litellm_query=query)
    prov = report["providers"][0]
    assert prov["litellm"][0]["client"] == "hindsight" and prov["litellm"][0]["share"] == 1.0
    assert prov["per_point"]["litellm_tokens"] == 2500
    assert prov["intervals"][0]["litellm"][0]["requests"] == 10 and prov["intervals"][1]["litellm"] == []
    assert report["coverage"]["litellm"] == "on"


def test_report_survives_a_failing_spend_log(tmp_path):
    _seed(tmp_path)
    config = {"attribution": {"litellm": {"database_url": "postgresql://x/litellm"}}}

    def query(settings, start, end, timeout):
        raise ledger.CollectorError("connection refused")

    report = attribute.build_report(config, T0 - timedelta(minutes=1), T0 + timedelta(hours=1), litellm_query=query)
    assert report["coverage"]["litellm"] == "error: connection refused" and report["providers"]


def test_provider_filter_and_empty_range(tmp_path):
    _seed(tmp_path)
    report = attribute.build_report({}, T0 - timedelta(minutes=1), T0 + timedelta(hours=1), provider="claude")
    assert report["providers"] == []
    assert "Nothing moved" in attribute.render_report(report)


def test_parse_when():
    assert attribute.parse_when("90m", T0) == T0 - timedelta(minutes=90)
    assert attribute.parse_when("7d", T0) == T0 - timedelta(days=7)
    assert attribute.parse_when("2026-09-25T10:00:00+00:00", T0) == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert attribute.parse_when("2026-09-25", T0).tzinfo is not None
    with pytest.raises(attribute.AttributeArgError):
        attribute.parse_when("yesterday-ish", T0)


# --- cli + config -----------------------------------------------------------


def test_cli_attribute_prints_json_and_rejects_a_bad_range(tmp_path, capsys):
    from aiuse.cli import main

    _seed(tmp_path)
    assert (
        main(["attribute", "--since", "2026-09-25T13:59:00+00:00", "--until", "2026-09-25T15:00:00+00:00", "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["providers"][0]["provider"] == "clinepass"
    assert main(["attribute", "--since", "soonish"]) == 2
    assert "invalid time" in capsys.readouterr().err


def test_cli_sample_skips_without_collecting(monkeypatch, capsys):
    from aiuse import cli

    now = datetime.now(timezone.utc)
    sampler.save_state(_state("idle", now, now))
    monkeypatch.setattr(cli, "run_collectors", lambda config: pytest.fail("must not collect"))
    assert cli.main(["sample"]) == 0
    assert capsys.readouterr().out == "", "a skip is silent when nobody is watching"


def test_default_config_validates_and_bad_sampling_values_are_reported():
    assert validate_config(load_config_from_defaults()) == []
    issues = validate_config({"sampling": {"burst_interval": 0, "typo": 1}, "attribution": {"litellm": {"nope": 1}}})
    assert any("sampling.burst_interval must be positive" in i for i in issues)
    assert any("unknown sampling key 'typo'" in i for i in issues)
    assert any("unknown attribution.litellm key 'nope'" in i for i in issues)
