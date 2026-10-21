"""A reset time one source measured must survive another source winning selection.

CodexBar sends no reset for Antigravity's in-use 5-hour windows; OpenUsage does.
Selection keeps one source's rows, so whenever CodexBar wins, the countdown
vanishes. The runner carries the last reset an earlier reading measured onto the
window, marked as an estimate, when that is still safe: the reset has not passed,
the reading was in use, and usage has not fallen since (a fall is a reset).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from aiuse.analysis import history
from aiuse.analysis.history import RESET_CARRY_USED_TOLERANCE, find_carried_resets, save_snapshot
from aiuse.analysis.use_or_lose import _human_when
from aiuse.chat_format import _chat_deadline
from aiuse.collectors import runner
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow
from aiuse.serve import _snapshot_from_accounts_dict

NOW = datetime.now(timezone.utc)

# Every collector the runner knows, so an end-to-end run starts none of them.
_COLLECTORS = (
    "cswap",
    "codexbar",
    "grok_billing",
    "caut",
    "caam",
    "openusage_ai",
    "openusage_sh",
    "opencode_go",
    "opencode_zen",
    "openrouter",
    "deepseek",
    "sipb",
    "tokscale",
    "clinepass",
    "hermes",
    "muse",
    "qwencloud",
    "bailian",
    "paseo",
    "acp",
)


def _ago(**delta: float) -> datetime:
    return NOW - timedelta(**delta)


def _window(
    label: str = "Gemini 5-hour",
    *,
    used: float,
    resets: datetime | None = None,
    minutes: int | None = 300,
) -> QuotaWindow:
    return QuotaWindow(
        label=label,
        used_percent=used,
        remaining_percent=100.0 - used,
        resets_at=resets,
        window_minutes=minutes,
    )


def _row(
    source: str,
    *windows: QuotaWindow,
    provider: str = "antigravity",
    account: str | None = None,
) -> AccountUsage:
    return AccountUsage(source=source, provider=provider, account=account, windows=list(windows))


def _record(at: datetime, *rows: AccountUsage) -> Path:
    """Persist one earlier collection exactly as the CLI does."""
    return save_snapshot(Snapshot(collected_at=at, accounts=list(rows)), [])


def _live(*rows: AccountUsage) -> Snapshot:
    return Snapshot(collected_at=NOW, accounts=list(rows))


def _carry(snapshot: Snapshot) -> None:
    runner._carry_forward_resets(snapshot)


def _stamp_name(at: datetime) -> str:
    return f"{at:%Y-%m-%dT%H%M%S.%fZ}.json"


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Names of the snapshot files the walk opens, in order."""
    names: list[str] = []
    real_read = history._read_snapshot_file

    def spy(path: Path) -> dict[str, Any] | None:
        names.append(path.name)
        return real_read(path)

    monkeypatch.setattr(history, "_read_snapshot_file", spy)
    return names


def test_in_use_window_borrows_the_reset_another_source_measured():
    prior_at = _ago(minutes=40)
    resets = NOW + timedelta(hours=3, minutes=10)
    _record(prior_at, _row("openusage_ai", _window("Antigravity Gemini 5-hour", used=36.0, resets=resets)))
    window = _window(used=37.8)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets
    assert not window.reset_time_is_precise()
    assert window.resets_at_carried_from == {"source": "openusage_ai", "collected_at": prior_at.isoformat()}


def test_carried_reset_serializes_with_its_provenance_and_is_omitted_otherwise():
    resets = NOW + timedelta(hours=2)
    _record(_ago(minutes=30), _row("openusage_ai", _window(used=20.0, resets=resets)))
    carried = _window(used=21.0)
    plain = _window("Claude/GPT 5-hour", used=5.0, resets=resets)
    _carry(_live(_row("codexbar", carried, plain)))

    body = carried.to_dict()
    assert body["resets_at"] == resets.isoformat()
    assert body["resets_at_carried_from"]["source"] == "openusage_ai"
    assert body["resets_at_carried_from"]["collected_at"] == _ago(minutes=30).isoformat()
    # Additive and optional: a window whose reset a source measured never carries the key.
    assert "resets_at_carried_from" not in plain.to_dict()
    assert plain.reset_time_is_precise()


def test_reset_that_already_passed_is_not_carried():
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=NOW - timedelta(minutes=1))))
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None
    assert window.resets_at_carried_from is None
    assert window.reset_time_is_precise()


def test_usage_that_fell_since_means_the_window_reset():
    # 50% used with a reset still ahead, then 3% used: the window reset early.
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=50.0, resets=NOW + timedelta(hours=2))))
    window = _window(used=3.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


def test_prior_reading_with_nothing_used_is_not_carried():
    # An unused window's reset is just "now + 5h" and slides with every query.
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=0.0, resets=NOW + timedelta(hours=4, minutes=20))))
    window = _window(used=4.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


def test_unused_window_is_left_alone_without_touching_history(monkeypatch):
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=NOW + timedelta(hours=3))))
    monkeypatch.setattr(history, "_prior_readings", lambda *_a: pytest.fail("no window needs a reset"))
    window = _window(used=0.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


@pytest.mark.parametrize(
    ("recorded", "live", "borrows"),
    [
        ("me@example.com", "me@example.com", True),
        ("ME@example.com", "me@example.com", True),
        # Provider-scoped envelope (OpenUsage) with one live account: same subscription.
        (None, "me@example.com", True),
        # The collector stopped naming the account: drift, not a sibling.
        ("me@example.com", None, True),
        (None, None, True),
        ("other@example.com", "me@example.com", False),
    ],
)
def test_account_matching_never_crosses_differently_named_accounts(recorded, live, borrows):
    resets = NOW + timedelta(hours=3)
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=resets), account=recorded))
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window, account=live)))

    assert (window.resets_at == resets) is borrows


def test_anonymous_reading_is_not_lent_to_either_of_two_live_accounts():
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=NOW + timedelta(hours=3))))
    mine, theirs = _window(used=40.0), _window(used=40.0)

    _carry(_live(_row("codexbar", mine, account="me@example.com"), _row("codexbar", theirs, account="you@example.com")))

    assert mine.resets_at is None
    assert theirs.resets_at is None


def test_named_reading_goes_to_its_own_account_only():
    resets = NOW + timedelta(hours=3)
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=resets), account="me@example.com"))
    mine, theirs = _window(used=40.0), _window(used=40.0)

    _carry(_live(_row("codexbar", mine, account="me@example.com"), _row("codexbar", theirs, account="you@example.com")))

    assert mine.resets_at == resets
    assert theirs.resets_at is None


def test_window_that_already_has_its_reset_is_untouched():
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=NOW + timedelta(hours=3))))
    measured = NOW + timedelta(hours=2, minutes=5)
    window = _window(used=40.0, resets=measured)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == measured
    assert window.resets_at_carried_from is None
    assert window.reset_time_is_precise()


def test_walk_passes_readings_that_never_knew_the_reset():
    """The real Gemini 5-hour sequence: one OpenUsage reading, then hours of CodexBar without a reset."""
    origin_at = _ago(hours=3, minutes=9)
    resets = NOW + timedelta(minutes=1, seconds=15)
    _record(origin_at, _row("openusage_ai", _window("Antigravity Gemini 5-hour", used=36.0, resets=resets)))
    for minutes_ago, used in ((170, 37.8), (120, 40.8), (60, 42.7), (10, 44.7)):
        _record(_ago(minutes=minutes_ago), _row("codexbar", _window(used=used)))
    window = _window(used=44.9)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets
    assert window.resets_at_carried_from == {"source": "openusage_ai", "collected_at": origin_at.isoformat()}


def test_a_drop_after_the_reset_reading_blocks_the_carry():
    # Reset 2h ahead at 80% used; an hour ago the window read 2% (it was reset
    # early and has been refilled since), so the old reset is another cycle's.
    _record(_ago(hours=3), _row("openusage_ai", _window(used=80.0, resets=NOW + timedelta(hours=2))))
    _record(_ago(hours=1), _row("codexbar", _window(used=2.0)))
    window = _window(used=85.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


def test_carry_chains_name_the_reading_that_measured_the_reset():
    origin_at = _ago(hours=2)
    resets = NOW + timedelta(hours=3)
    # What the previous collection stored after carrying it: the origin must survive.
    passed_on = _window(used=41.0)
    passed_on.carry_reset(resets, source="openusage_ai", collected_at=origin_at)
    _record(_ago(minutes=10), _row("codexbar", passed_on))
    window = _window(used=42.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets
    assert window.resets_at_carried_from == {"source": "openusage_ai", "collected_at": origin_at.isoformat()}


def test_readings_older_than_the_window_are_another_cycle():
    # Reading 5h10m ago: past the 300-minute window, so never a prior of this cycle.
    _record(_ago(minutes=310), _row("openusage_ai", _window(used=36.0, resets=NOW + timedelta(minutes=10))))
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


def test_any_duration_carries_a_weekly_window():
    resets = NOW + timedelta(days=4)
    _record(_ago(days=2), _row("openusage_ai", _window("Gemini weekly", used=28.0, resets=resets, minutes=10080)))
    window = _window("Gemini weekly", used=30.0, minutes=10080)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets


def test_window_without_a_duration_is_skipped_without_touching_history(monkeypatch):
    monkeypatch.setattr(history, "_prior_readings", lambda *_a: pytest.fail("a window without a duration is no series"))
    window = _window("DeepSeek prepaid balance", used=12.0, minutes=None)

    _carry(_live(_row("codexbar", window, provider="deepseek")))

    assert window.resets_at is None


def test_other_pools_of_the_provider_are_not_borrowed():
    _record(
        _ago(minutes=40), _row("openusage_ai", _window("Claude/GPT 5-hour", used=36.0, resets=NOW + timedelta(hours=3)))
    )
    window = _window("Gemini 5-hour", used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


@pytest.mark.parametrize(
    ("prior_used", "borrows"),
    [
        (40.0 + RESET_CARRY_USED_TOLERANCE - 0.1, True),  # whole-point rounding between sources
        (40.0 + RESET_CARRY_USED_TOLERANCE + 0.1, False),  # a real fall
    ],
)
def test_used_percent_tolerance_absorbs_source_rounding_but_not_a_reset(prior_used, borrows):
    resets = NOW + timedelta(hours=3)
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=prior_used, resets=resets)))
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert (window.resets_at == resets) is borrows


def test_remaining_percent_stands_in_for_a_missing_used_percent():
    resets = NOW + timedelta(hours=3)
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=resets)))
    window = QuotaWindow(label="Gemini 5-hour", remaining_percent=60.0, window_minutes=300)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets


def test_readings_collected_after_this_one_are_not_priors():
    _record(NOW + timedelta(minutes=1), _row("openusage_ai", _window(used=36.0, resets=NOW + timedelta(hours=3))))
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


def test_no_history_is_no_carry():
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at is None


def test_latest_json_alone_is_a_reading():
    resets = NOW + timedelta(hours=3)
    path = _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=resets)))
    path.unlink()  # only the latest.json mirror is left
    assert [p.name for p in history.snapshot_dir().iterdir()] == ["latest.json"]
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets


def test_unreadable_files_are_skipped():
    resets = NOW + timedelta(hours=3)
    _record(_ago(minutes=40), _row("openusage_ai", _window(used=36.0, resets=resets)))
    (history.snapshot_dir() / _stamp_name(_ago(minutes=5))).write_text("{not json", encoding="utf-8")
    (history.snapshot_dir() / _stamp_name(_ago(minutes=6))).write_text("[1, 2]", encoding="utf-8")
    (history.snapshot_dir() / _stamp_name(_ago(minutes=7))).write_text(json.dumps({"accounts": "no"}), encoding="utf-8")
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets


def test_walk_ends_at_the_deciding_file_and_never_opens_what_is_past_the_window(opened):
    resets = NOW + timedelta(hours=3)
    for i in range(30, 0, -1):  # thirty collections older than the 5h window
        _record(_ago(hours=6, minutes=10 * i), _row("codexbar", _window(used=1.0)))
    decisive = _record(_ago(minutes=10), _row("openusage_ai", _window(used=36.0, resets=resets)))
    _record(_ago(minutes=5), _row("codexbar", _window(used=38.0)))
    window = _window(used=40.0)

    _carry(_live(_row("codexbar", window)))

    assert window.resets_at == resets
    # latest.json (opened once to learn it mirrors the newest file, then reused
    # as that file) and the decisive reading; nothing else, nothing older.
    assert opened == ["latest.json", decisive.name]


def test_one_pass_serves_every_window_and_opens_each_file_once(opened):
    five_hour_resets = NOW + timedelta(hours=3)
    weekly_resets = NOW + timedelta(days=3)
    _record(
        _ago(minutes=50),
        _row(
            "openusage_ai",
            _window("Gemini 5-hour", used=36.0, resets=five_hour_resets),
            _window("Gemini weekly", used=60.0, resets=weekly_resets, minutes=10080),
        ),
    )
    _record(_ago(minutes=20), _row("codexbar", _window("Gemini 5-hour", used=37.0)))
    five_hour = _window("Gemini 5-hour", used=38.0)
    weekly = _window("Gemini weekly", used=61.0, minutes=10080)

    _carry(_live(_row("codexbar", five_hour, weekly)))

    assert five_hour.resets_at == five_hour_resets
    assert weekly.resets_at == weekly_resets
    assert len(opened) == len(set(opened))


def test_find_carried_resets_reports_each_window_with_its_origin():
    origin_at = _ago(minutes=40)
    resets = NOW + timedelta(hours=3)
    _record(origin_at, _row("openusage_ai", _window(used=36.0, resets=resets)))
    window = _window(used=40.0)

    ((found_window, carried),) = find_carried_resets(_live(_row("codexbar", window)))

    assert found_window is window
    assert (carried.resets_at, carried.source, carried.collected_at) == (resets, "openusage_ai", origin_at)
    # Finding is not applying: the live window is only changed by the runner hook.
    assert window.resets_at is None


def test_run_collectors_carries_the_reset_onto_the_row_that_won_selection(monkeypatch):
    now = utcnow()
    resets = now + timedelta(hours=3, minutes=10)
    _record(
        now - timedelta(minutes=40),
        _row("openusage_ai", _window("Antigravity Gemini 5-hour", used=36.0, resets=resets)),
    )
    codexbar = AccountUsage(
        source="codexbar",
        provider="antigravity",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[_window("Gemini 5-hour", used=37.8)],
    )
    monkeypatch.setattr(runner, "collect_codexbar", lambda **_kw: [codexbar])

    snapshot = runner.run_collectors({"collectors": {name: {"enabled": name == "codexbar"} for name in _COLLECTORS}})

    (account,) = snapshot.accounts
    (window,) = account.windows
    assert account.source == "codexbar"
    assert window.resets_at == resets
    assert not window.reset_time_is_precise()
    assert snapshot.to_dict()["accounts"][0]["windows"][0]["resets_at_carried_from"]["source"] == "openusage_ai"


def test_marker_survives_the_snapshot_cache_round_trip():
    origin_at = _ago(minutes=40)
    resets = NOW + timedelta(hours=3)
    window = _window(used=38.0)
    window.carry_reset(resets, source="openusage_ai", collected_at=origin_at)
    path = _record(_ago(minutes=5), _row("codexbar", window))

    payload = json.loads(path.read_text(encoding="utf-8"))

    expected = {"source": "openusage_ai", "collected_at": origin_at.isoformat()}
    # Both spellings of the cache file carry it (flat accounts[] and the snapshot mirror).
    assert payload["accounts"][0]["windows"][0]["resets_at_carried_from"] == expected
    assert payload["snapshot"]["accounts"][0]["windows"][0]["resets_at_carried_from"] == expected
    # And what `aiuse watch` / `serve` rebuild from disk still renders it as an estimate.
    (restored,) = _snapshot_from_accounts_dict(payload["snapshot"]).accounts[0].windows
    assert restored.resets_at == resets
    assert restored.resets_at_carried_from == expected
    assert not restored.reset_time_is_precise()


def test_carried_five_hour_reset_reads_in_hours_not_as_a_day_sized_estimate():
    # `~` marks an estimate; a carried 5-hour reset is one, but it is hours away.
    assert _human_when(0.125, estimated=True) == "in ~3h"
    assert _chat_deadline(0.125, estimated=True) == "~3h"
    # Day-sized estimates (date-only resets) keep their day wording.
    assert _human_when(1.38, estimated=True) == "in ~1 day"
    assert _chat_deadline(2.4, estimated=True) == "~2d"
