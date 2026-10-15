"""Claude has shared quota with model caps, not additive model pools."""

import re
from datetime import timedelta

import pytest

from aiuse.analysis.history import window_series_key
from aiuse.analysis.pace import governing_partition
from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, pool_entries
from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.chat_format import render_chat_report
from aiuse.collectors.cswap import _account_from_item
from aiuse.models import RoutingContext, Snapshot, claude_model_scope, utcnow
from aiuse.report import render_clock_matrix


def _snapshot(*, weekly_used=85.0, fable_used=100.0, session_used=3.0):
    now = utcnow()
    reset = (now + timedelta(days=3)).isoformat()
    account = _account_from_item(
        {
            "number": 1,
            "usageStatus": "ok",
            "usage": {
                "fiveHour": {
                    "pct": session_used,
                    "resetsAt": (now + timedelta(hours=3)).isoformat(),
                },
                "sevenDay": {"pct": weekly_used, "resetsAt": reset},
                "scoped": [{"name": "Fable", "pct": fable_used, "resetsAt": reset}],
            },
        },
        1,
    )
    return Snapshot(collected_at=now, accounts=[account])


@pytest.mark.parametrize("duration", [None, 10080])
@pytest.mark.parametrize("reverse", [False, True])
def test_exhausted_fable_cannot_govern_shared_quota(duration, reverse):
    windows = _snapshot().accounts[0].windows
    windows[-1].window_minutes = duration
    if reverse:
        windows.reverse()
    governing, children = governing_partition(windows)
    assert governing is not None
    assert governing.label == "Claude Code weekly"
    assert any("Fable" in child.label for child in children)


@pytest.mark.parametrize("weekly_used,session_used", [(100.0, 3.0), (85.0, 100.0)])
def test_shared_exhaustion_blocks_fable_too(weekly_used, session_used):
    snap = enrich_snapshot(_snapshot(weekly_used=weekly_used, session_used=session_used, fable_used=20.0).to_dict())
    assert snap["accounts"][0]["usable_now"] is False
    assert available_pools(snap) == []
    assert all(p["usable_now"] is False for p in pool_entries(snap))


def test_fable_exhaustion_leaves_shared_quota_available():
    snap = enrich_snapshot(_snapshot().to_dict())
    account = snap["accounts"][0]
    assert account["usable_now"] is True
    assert account["binding_window"] == "Claude Code weekly"
    assert account["binding_headroom_percent"] == 15.0
    assert account["available_at"] is None
    pools = {p["pool_family"]: p for p in pool_entries(snap)}
    assert pools["default"]["headroom_percent"] == 15.0
    assert pools["fable"]["usable_now"] is False
    assert pools["fable"]["shared_pool_family"] == "default"
    assert [p["pool_family"] for p in available_pools(snap)] == ["default"]
    cap = account["windows"][-1]
    assert cap["used_percent"] == 100.0
    assert cap["quota_scope"] == "model_sublimit"
    assert cap["max_share_of_parent_percent"] == 50.0
    assert "<=50% of shared weekly" in snap["summary_lines"][-1]


@pytest.mark.parametrize("weekly_used,fable_used,expected", [(85.0, 80.0, 10.0), (85.0, 20.0, 15.0)])
def test_fable_effective_headroom_is_limited_by_both(weekly_used, fable_used, expected):
    snap = enrich_snapshot(_snapshot(weekly_used=weekly_used, fable_used=fable_used).to_dict())
    fable = next(p for p in available_pools(snap) if p["pool_family"] == "fable")
    assert fable["headroom_percent"] == expected
    assert fable["headroom_basis"] == "shared_weekly"
    assert len(fable["windows"]) == 3


def test_no_shared_data_is_not_evidence_of_model_availability():
    snapshot = _snapshot(fable_used=10.0)
    snapshot.accounts[0].windows = snapshot.accounts[0].windows[-1:]
    snap = enrich_snapshot(snapshot.to_dict())
    assert snap["accounts"][0]["usable_now"] is False
    assert available_pools(snap) == []
    assert governing_partition(snapshot.accounts[0].windows)[0] is None


def test_unknown_model_cap_is_not_evidence_of_model_availability():
    snapshot = _snapshot()
    cap = snapshot.accounts[0].windows[-1]
    cap.used_percent = cap.remaining_percent = None
    assert [p["pool_family"] for p in available_pools(enrich_snapshot(snapshot.to_dict()))] == ["default"]


@pytest.mark.parametrize("width", [80, 110])
def test_table_keeps_overall_week_and_shows_fable_row(width):
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=width)
    row = next(line for line in text.splitlines() if " claude " in f" {line} " and " fable " not in f" {line} ")
    assert "85u/15l" in row
    assert "100u/0l" not in row
    assert "EXHAUSTED" not in row
    fable = next(line for line in text.splitlines() if " fable " in f" {line} ")
    assert fable.startswith("empty")
    assert "100u/0l" in fable
    assert "Fable-cap" in fable
    assert "Fable's included allowance is used up" in text
    assert "<=50% of shared weekly" not in text
    assert all(len(line) <= width for line in text.splitlines())


@pytest.mark.parametrize(
    "fable_used,sign",
    [(20.0, "-"), (95.0, "+")],
)
def test_fable_row_shows_its_own_score(fable_used, sign):
    text = render_clock_matrix([], snapshot=_snapshot(fable_used=fable_used), color=False, width=160)
    fable = next(line for line in text.splitlines() if " fable " in f" {line} ")
    assert fable.startswith("slow") or fable.startswith("mid")
    assert re.search(rf"\{sign}\d+%", fable)
    assert "Fable-cap" in fable


def test_fable_at_two_percent_left_is_the_bottom_of_slow():
    from aiuse.analysis.ranking import _BAND_CONSERVE, _BAND_EMPTY
    from aiuse.report import _build_matrix_rows

    built = _build_matrix_rows([], _snapshot(weekly_used=40.0, fable_used=98.0, session_used=10.0), None)
    fable = next(row for row in built if row.scope == "fable")
    shared = next(row for row in built if row.service == "claude" and row.scope == "—")
    assert fable.band == _BAND_CONSERVE
    assert fable.unranked is False
    assert fable.queue_score == 27
    assert fable.tail_note == "Fable-cap"
    assert shared.band != _BAND_EMPTY


def test_open_fable_scores_below_the_shared_row():
    from aiuse.analysis.ranking import _BAND_MID
    from aiuse.report import _build_matrix_rows

    built = _build_matrix_rows([], _snapshot(weekly_used=50.0, fable_used=20.0, session_used=10.0), None)
    fable = next(row for row in built if row.scope == "fable")
    shared = next(row for row in built if row.service == "claude" and row.scope == "—")
    assert fable.band == _BAND_MID
    assert shared.queue_score is not None
    assert fable.queue_score == max(50, shared.queue_score - 12)
    assert fable.queue_score < shared.queue_score


def test_spent_shared_window_empties_fable_without_changing_the_cap_cell():
    text = render_clock_matrix(
        [], snapshot=_snapshot(weekly_used=20.0, fable_used=10.0, session_used=100.0), color=False, width=160
    )
    fable = next(line for line in text.splitlines() if " fable " in f" {line} ")
    assert fable.startswith("empty")
    assert "90u/10l" in fable or "10u/90l" in fable


def test_chat_keeps_shared_account_status_and_explains_cap():
    text = render_chat_report(_snapshot(), [])
    heading = next(line for line in text.splitlines() if "**claude" in line)
    assert not heading.startswith("🔴")
    assert "15% left" in text
    assert "Fable allowance" in text
    assert "half of the plan's shared weekly allowance" in text
    assert "not extra quota" in text
    assert "Fable cap (<=" not in text
    assert "same exhausted" not in text


@pytest.mark.parametrize("mode", ["pace", "multi_dim", "legacy"])
def test_model_cap_does_not_produce_an_account_exhaustion_alert(mode):
    snap = _snapshot()
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "scoring_mode": mode,
                "learn_from_history": False,
                "provider_overrides": {"claude": {"shared_allotment": True}},
            }
        },
    )
    assert not any("Fable" in a.window_label for a in alerts)
    for alert in alerts:
        assert alert.remaining_percent != 0.0
        if mode == "pace":
            assert "Fable cap exhausted" in alert.message
            assert "no need to burn it separately" not in alert.message


def test_identical_overall_and_model_readings_keep_both_windows():
    snap = _snapshot(weekly_used=100.0)
    assert len(snap.accounts[0].windows) == 3


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Claude Code weekly — Fable", "Fable"),
        ("Claude Fable", "Fable"),
        ("Claude Code weekly — Claude Opus", "Opus"),
        ("Claude Sonnet", "Sonnet"),
        ("Claude/GPT weekly", None),
        ("Claude Code weekly", None),
    ],
)
def test_scope_labels(label, expected):
    assert claude_model_scope(label) == expected


def test_history_keeps_shared_and_model_meters_separate():
    assert window_series_key("claude", "Claude Code weekly", 10080) == "claude:-:weekly"
    assert window_series_key("claude", "Claude Code weekly — Fable", 10080) == "claude:model_fable:weekly"


def test_model_exhaustion_note_does_not_block_shared_quota():
    snap = enrich_snapshot(
        _snapshot(fable_used=20.0).to_dict(),
        notes=[{"provider": "claude", "pool_family": "fable", "resets_at": utcnow().isoformat()}],
    )
    assert snap["accounts"][0]["usable_now"] is True
    assert [p["pool_family"] for p in available_pools(snap)] == ["default"]


def test_overall_exhaustion_note_blocks_every_model():
    snap = enrich_snapshot(
        _snapshot(fable_used=20.0).to_dict(),
        notes=[{"provider": "claude", "pool_family": "default", "resets_at": utcnow().isoformat()}],
    )
    assert available_pools(snap) == []


def test_cached_model_alert_is_not_restored_as_whole_account_exhaustion():
    from aiuse.serve import _alerts_from_dicts

    assert (
        _alerts_from_dicts(
            [{"provider": "claude", "window_label": "Claude Code weekly — Fable", "remaining_percent": 0.0}]
        )
        == []
    )


def test_chat_primary_routing_does_not_treat_fable_cap_as_other_models_quota():
    context = RoutingContext(primary_model="claude-sonnet", primary_provider="claude", fallback_provider="codex")
    text = render_chat_report(_snapshot(weekly_used=60.0), [], routing_context=context)
    assert "Keep claude as primary" in text
    assert "Switch primary to codex" not in text
