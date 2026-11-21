"""`unavailable[]` in `aiuse --available --json` (schema 1.8).

`available` lists only usable_now pools and `excluded` the operator-ruled-out
ones, so an exhausted, near-empty or rate-gated pool appeared in neither and a
consumer could not tell "empty until Monday" from "missing". `unavailable`
names every other pool entry, why it is out, which window binds and when the
whole pool is usable again.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from aiuse import cli
from aiuse.analysis import history
from aiuse.analysis.selfdescribe import (
    SEMANTICS,
    UNAVAILABLE_REASONS,
    available_pools,
    enrich_snapshot,
    unavailable_pools,
)
from aiuse.models import parse_dt, utcnow

NOW = utcnow()


def _window(
    label: str, remaining: float | None, days: float, minutes: int | None, window_id: str | None = None
) -> dict:
    window: dict = {"label": label, "window_minutes": minutes, "window_id": window_id}
    if remaining is not None:
        window["used_percent"] = 100.0 - remaining
        window["remaining_percent"] = remaining
        window["resets_at"] = (NOW + timedelta(days=days)).isoformat()
    return window


def _account(provider: str, account: str | None, *windows: dict, **extra: object) -> dict:
    return {
        "provider": provider,
        "account": account,
        "source": "codexbar",
        "billing_kind": "subscription_window",
        "collected_at": NOW.isoformat(),
        "windows": list(windows),
        **extra,
    }


def _snap(*accounts: dict) -> dict:
    return enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": list(accounts)}, now=NOW)


def _by_provider(items: list[dict]) -> dict[str, dict]:
    return {i["provider"]: i for i in items}


def _same(a: str | None, b: str) -> bool:
    return a is not None and parse_dt(a) == parse_dt(b)


def test_exhausted_weekly_names_its_window_reset_and_zero_headroom():
    weekly = _window("Codex weekly", 0.0, 3, 10080)
    snap = _snap(_account("codex", "me", _window("Codex 5-hour", 80.0, 0.1, 300), weekly))
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["provider"] == "codex"
    assert item["account"] == "me"
    assert item["reason"] == "exhausted"
    assert item["binding_window"] == "Codex weekly"
    assert _same(item["available_at"], weekly["resets_at"])
    assert item["headroom_percent"] == 0.0
    assert item["pool_id"] == "openai:codex"
    assert "rate_gated_by" not in item
    assert available_pools(snap) == []


def test_near_empty_is_its_own_reason():
    weekly = _window("Weekly", 2.0, 2, 10080)
    snap = _snap(_account("opencode-go", "t-e", weekly, _window("Monthly", 70.0, 6, 43200)))
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["reason"] == "near_empty"
    assert item["binding_window"] == "Weekly"
    assert item["headroom_percent"] == pytest.approx(2.0)
    assert _same(item["available_at"], weekly["resets_at"])


def test_monthly_binds_over_a_daily_that_resets_first():
    daily = _window("Daily", 0.0, 1, 1440)
    monthly = _window("Monthly", 0.0, 6, 43200)
    (item,) = unavailable_pools(_snap(_account("opencode-go", "t-e", daily, monthly)), now=NOW)
    assert item["reason"] == "exhausted"
    assert item["binding_window"] == "Monthly"
    assert _same(item["available_at"], monthly["resets_at"])


def test_near_empty_weekly_with_exhausted_monthly_is_exhausted_and_monthly_binds():
    weekly = _window("Weekly", 2.0, 2, 10080)
    monthly = _window("Monthly", 0.0, 6, 43200)
    (item,) = unavailable_pools(_snap(_account("opencode-go", "t-e", weekly, monthly)), now=NOW)
    assert item["reason"] == "exhausted"
    assert item["binding_window"] == "Monthly"


def test_rate_gated_omp_carries_the_gate_and_its_reset():
    gate_reset = (NOW + timedelta(days=4)).isoformat()
    omp = _account(
        "omp",
        "grant",
        _window("credits", 60.0, 18, None, window_id="claude-cloud-credits"),
    )
    snap = _snap(omp)
    # enrichment recomputes the gate from the Claude windows; set the derived result directly
    snap["accounts"][0]["rate_gated_by"] = {
        "provider": "claude",
        "account": "me",
        "window_label": "Claude Code weekly",
        "resets_at": gate_reset,
        "evidence": "test",
    }
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["provider"] == "omp"
    assert item["reason"] == "rate_gated"
    assert item["binding_window"] == "Claude Code weekly"
    assert _same(item["available_at"], gate_reset)
    assert item["rate_gated_by"]["window_label"] == "Claude Code weekly"
    assert item["headroom_percent"] == pytest.approx(60.0)


def test_unmeasured_pool_is_no_reading_with_null_reset_and_headroom():
    snap = _snap(_account("zai", None, _window("weekly", None, 0, 10080)))
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["reason"] == "no_reading"
    assert item["available_at"] is None
    assert item["headroom_percent"] is None


def test_headroom_is_the_smallest_remaining_across_windows():
    weekly = _window("Weekly", 30.0, 2, 10080)
    monthly = _window("Monthly", 0.0, 6, 43200)
    (item,) = unavailable_pools(_snap(_account("opencode-go", "a", weekly, monthly)), now=NOW)
    assert item["headroom_percent"] == 0.0


def test_usable_pools_are_not_listed_and_the_empty_case_is_an_empty_list():
    snap = _snap(_account("codex", "me", _window("Codex weekly", 60.0, 3, 10080)))
    assert unavailable_pools(snap, now=NOW) == []


def test_operator_excluded_pool_is_not_listed():
    snap = _snap(
        _account("codex", "me", _window("Codex weekly", 0.0, 3, 10080)),
        _account("opencode-go", "a", _window("Weekly", 0.0, 2, 10080)),
    )
    items = unavailable_pools(snap, now=NOW, exclusions={"codex": "operator said no"})
    assert [i["provider"] for i in items] == ["opencode-go"]


def test_wanted_filter_narrows_the_list():
    snap = _snap(
        _account("codex", "me", _window("Codex weekly", 0.0, 3, 10080)),
        _account("opencode-go", "a", _window("Weekly", 0.0, 2, 10080)),
    )
    items = unavailable_pools(snap, now=NOW, wanted=lambda p: p.get("provider") == "codex")
    assert [i["provider"] for i in items] == ["codex"]


def _cursor_snapshot() -> dict:
    windows = [
        _window("total", 0.0, 21, 43200, "primary"),
        _window("auto", 0.0, 21, 43200, "secondary"),
        _window("third-party", 0.0, 21, 43200, "tertiary"),
        _window("grok-bot", 80.0, 5, 10080, "cursor-grok-bot"),
    ]
    return _snap(_account("cursor", "me", *windows))


def test_hidden_total_ceiling_row_is_omitted_by_default():
    items = unavailable_pools(_cursor_snapshot(), now=NOW)
    assert sorted(i["pool_family"] for i in items) == ["auto", "third_party"]
    assert all(i["reason"] == "exhausted" for i in items)


def test_total_row_is_listed_when_the_operator_shows_total_rows():
    items = unavailable_pools(_cursor_snapshot(), now=NOW, show_total_rows=True)
    assert "total" in {i["pool_family"] for i in items}


def test_items_sort_soonest_reset_first_and_unknown_last():
    soon = _window("Weekly", 0.0, 1, 10080)
    later = _window("Weekly", 0.0, 5, 10080)
    snap = _snap(
        _account("zai", None, _window("weekly", None, 0, 10080)),
        _account("opencode-go", "late", later),
        _account("codex", "soon", soon),
    )
    assert [i["provider"] for i in unavailable_pools(snap, now=NOW)] == ["codex", "opencode-go", "zai"]


def test_stale_gated_reading_carries_the_refresh_gate_fields():
    stale_at = (NOW - timedelta(seconds=700)).isoformat()
    agy = _account("antigravity", None, _window("Gemini weekly", 0.0, 2, 10080), collected_at=stale_at)
    snap = enrich_snapshot(
        {"collected_at": NOW.isoformat(), "accounts": [agy]},
        now=NOW,
        config={"analysis": {"fresh_threshold_seconds_by_provider": {"antigravity": 600.0}}},
    )
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["refresh_blocked_by_gate"] is True
    assert item["gate_remaining_seconds"] == pytest.approx(200.0, abs=1.0)
    assert item["refreshable_at"]


def test_every_item_field_is_documented_in_the_semantics_map():
    gate_reset = (NOW + timedelta(days=4)).isoformat()
    omp = _account(
        "omp",
        "grant",
        _window("credits", 60.0, 18, None),
    )
    stale = _account(
        "antigravity",
        None,
        _window("Gemini weekly", 0.0, 2, 10080),
        collected_at=(NOW - timedelta(seconds=700)).isoformat(),
    )
    snap = enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": [omp, stale]}, now=NOW)
    snap["accounts"][0]["rate_gated_by"] = {
        "provider": "claude",
        "window_label": "w",
        "resets_at": gate_reset,
        "evidence": "t",
    }
    text = SEMANTICS["unavailable"]
    keys = {key for item in unavailable_pools(snap, now=NOW) for key in item}
    assert {"provider", "account", "pool_family", "pool_id", "reason", "binding_window", "available_at"} <= keys
    for key in keys:
        assert key in text, key
    for reason in UNAVAILABLE_REASONS:
        assert reason in SEMANTICS["unavailable_reason"]


# ── envelope ────────────────────────────────────────────────────────────────


def _write_cache(accounts: list[dict]) -> None:
    snap = {"collected_at": utcnow().isoformat(), "accounts": accounts}
    cache = history.snapshot_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "latest.json").write_text(json.dumps(snap))


def _payload(tmp_path, capsys, config_text: str = "", *, rc: int = 0) -> dict:
    config = tmp_path / "config.toml"
    config.write_text(config_text)
    assert cli.main(["--available", "--json", "-q", "--config", str(config)]) == rc
    return json.loads(capsys.readouterr().out)


def test_envelope_lists_unavailable_beside_available_and_keeps_fresh(tmp_path, capsys):
    _write_cache(
        [
            _account("codex", "me", _window("Codex weekly", 70.0, 3, 10080)),
            _account("opencode-go", "t-e", _window("Weekly", 2.0, 2, 10080), _window("Monthly", 0.0, 6, 43200)),
        ]
    )
    payload = _payload(tmp_path, capsys)
    assert payload["schema_version"] == "1.8"
    assert [p["provider"] for p in payload["available"]] == ["codex"]
    assert payload["excluded"] == []
    (item,) = payload["unavailable"]
    assert item["provider"] == "opencode-go"
    assert item["reason"] == "exhausted"
    assert item["binding_window"] == "Monthly"
    assert payload["fresh"] is True
    assert payload["stale_pools"] == []
    assert "unavailable" in payload["semantics"]


def test_envelope_unavailable_is_an_empty_list_when_everything_is_usable(tmp_path, capsys):
    _write_cache([_account("codex", "me", _window("Codex weekly", 70.0, 3, 10080))])
    payload = _payload(tmp_path, capsys)
    assert payload["unavailable"] == []


def test_envelope_does_not_duplicate_an_excluded_pool(tmp_path, capsys):
    _write_cache(
        [
            _account("codex", "me", _window("Codex weekly", 70.0, 3, 10080)),
            _account("opencode-go", "t-e", _window("Weekly", 0.0, 2, 10080)),
        ]
    )
    cfg = '[analysis.excluded_pools]\n"opencode-go" = "no thanks"\ncodex = "also no"\n'
    payload = _payload(tmp_path, capsys, cfg, rc=3)
    assert payload["available"] == []
    assert [e["provider"] for e in payload["excluded"]] == ["codex"]
    assert payload["unavailable"] == []
