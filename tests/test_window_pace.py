from datetime import datetime, timedelta, timezone

from aiuse.analysis.pace import (
    format_pace,
    overall_pace,
    overall_pace_early,
    overall_pace_percent,
    window_is_early,
    window_pace,
    window_pace_percent,
    window_pace_ratio,
)
from aiuse.analysis.selfdescribe import SCHEMA_VERSION, available_pools, enrich_snapshot, summary_line
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot
from aiuse.report import render_clock_matrix, render_stderr_meta

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def window(used=25, minutes=300):
    return QuotaWindow(
        label="weekly" if minutes == 10080 else "5h",
        used_percent=used,
        window_minutes=minutes,
        resets_at=NOW + timedelta(minutes=minutes / 2),
    )


def test_unblended_window_pace_and_max():
    assert window_pace_percent(window(), NOW) == -50
    assert window_pace_percent(window(75), NOW) == 50
    assert window_pace_ratio(window(75), NOW) == 1.5
    assert overall_pace_percent([window(), window(75)], NOW) == 50
    for quota in [
        window(100),
        QuotaWindow(label="unknown", used_percent=20),
        QuotaWindow(label="unknown", used_percent=20, resets_at=NOW),
    ]:
        assert window_pace_percent(quota, NOW) is None
    assert overall_pace_percent([], NOW) is None


def test_model_cap_weekly_fallback():
    quota = QuotaWindow(label="Claude weekly — Fable", used_percent=25, resets_at=NOW + timedelta(days=3.5))
    assert window_pace_percent(quota, NOW) == -50


def snapshot():
    return Snapshot(
        collected_at=NOW,
        accounts=[
            AccountUsage(provider="codex", source="test", windows=[window(), window(75, 10080)]),
            AccountUsage(provider="claude", source="test", windows=[window(100), window(75, 10080)]),
            AccountUsage(provider="jev", source="test", billing_kind=BillingKind.PREPAID_BALANCE, balance_usd=5),
        ],
    )


def test_json_and_available_use_collection_time():
    data = enrich_snapshot(snapshot().to_dict(), now=NOW + timedelta(days=20))
    assert SCHEMA_VERSION == "1.4"
    assert data["accounts"][0]["overall_pace_percent"] == 50
    assert data["accounts"][0]["windows"][0]["pace_percent"] == -50
    assert data["accounts"][0]["windows"][0]["pace_ratio"] == 0.5
    assert data["accounts"][1]["windows"][0]["pace_percent"] is None
    assert "pace +50%" in summary_line(available_pools(data)[0])


def test_matrix_pace_fixed_columns_and_footer():
    snap = snapshot()
    text = render_clock_matrix([], snapshot=snap, color=False, width=200)
    lines = text.splitlines()
    first = next(line for line in lines if "codex" in line)
    second = next(line for line in lines if "claude" in line)
    prepaid = next(line for line in lines if "Jev" in line)
    assert "-50%" in first and "+50%" in first
    assert "-50%" not in second
    assert "EXHAUSTED" not in second
    assert "% <-" not in first and "% <-" not in second
    assert prepaid.index("$5.00") > first.rfind("+50%")
    assert "$" not in first + second
    assert "PACE = the row" in text and "+N%/-N%" in text
    assert "Pace:" not in render_stderr_meta(snap, [], color=False)
    narrow = render_clock_matrix([], snapshot=snap, color=False, width=65)
    assert "-50%" not in next(line for line in narrow.splitlines() if "codex" in line)


def test_long_nonquota_notes_do_not_shift_fixed_columns():
    snap = snapshot()
    snap.accounts.append(AccountUsage(provider="sipb", source="test", raw={"status": {"up": True, "latency_s": 0.4}}))
    baseline = render_clock_matrix([], snapshot=snap, color=False, width=200)
    codex = next(line for line in baseline.splitlines() if "codex" in line)
    sipb = next(line for line in baseline.splitlines() if "mit-sipb" in line)
    assert sipb.index("unlimited") > codex.rfind("+50%")
    assert "PACE" in baseline.splitlines()[0]
    assert "last resort" in sipb


def test_missing_reset_has_no_cell_pace():
    quota = window()
    quota.resets_at = None
    snap = Snapshot(collected_at=NOW, accounts=[AccountUsage(provider="codex", source="test", windows=[quota])])
    line = next(
        line for line in render_clock_matrix([], snapshot=snap, color=False, width=200).splitlines() if "codex" in line
    )
    assert "25u/75l" in line
    assert "-50%" not in line
    assert snap.to_dict()["accounts"][0]["overall_pace_percent"] is None


def test_exhausted_window_blanks_overall_pace():
    assert window_pace_ratio(window(100), NOW) is None
    assert overall_pace_percent([window(), window(100)], NOW) is None
    assert window_pace_percent(window(50), NOW) == 0


def test_cached_window_fields_are_refreshed_at_collection_time():
    data = snapshot().to_dict()
    data["accounts"][0]["windows"][0]["pace_percent"] = 12345
    enriched = enrich_snapshot(data, now=NOW + timedelta(days=20))
    assert enriched["accounts"][0]["windows"][0]["pace_percent"] == -50


def test_early_boundary():
    # 1000 minutes duration
    # 90 minutes elapsed (910 min left) -> elapsed 0.09 -> early (<0.10)
    w_early = QuotaWindow(
        label="5h",
        used_percent=20,
        window_minutes=1000,
        resets_at=NOW + timedelta(minutes=910),
    )
    # 110 minutes elapsed (890 min left) -> elapsed 0.11 -> not early (>=0.10)
    w_not_early = QuotaWindow(
        label="5h",
        used_percent=20,
        window_minutes=1000,
        resets_at=NOW + timedelta(minutes=890),
    )
    assert window_is_early(w_early, NOW) is True
    assert window_pace(w_early, NOW)[1] is True
    assert window_is_early(w_not_early, NOW) is False
    assert window_pace(w_not_early, NOW)[1] is False


def test_format_pace():
    assert format_pace(1351, True) == "+1351%~"
    assert format_pace(-20, False) == "-20%"
    assert format_pace(50, False) == "+50%"
    assert format_pace(0, True) == "+0%~"
    assert format_pace(0, False) == "+0%"


def test_overall_early_follows_max_window():
    # Window A: elapsed 0.50, pace -20% (not early)
    wa = QuotaWindow(
        label="5h",
        used_percent=40,
        window_minutes=1000,
        resets_at=NOW + timedelta(minutes=500),
    )
    # Window B: elapsed 0.05, used 73% -> pace +1360% (early)
    wb = QuotaWindow(
        label="monthly",
        used_percent=73,
        window_minutes=10000,
        resets_at=NOW + timedelta(minutes=9500),
    )
    pct, early = overall_pace([wa, wb], NOW)
    assert pct == 1360
    assert early is True
    assert overall_pace_early([wa, wb], NOW) is True

    # When not-early window has higher pace:
    # Window C: elapsed 0.50, used 90% -> pace +80% (not early)
    wc = QuotaWindow(
        label="5h",
        used_percent=90,
        window_minutes=1000,
        resets_at=NOW + timedelta(minutes=500),
    )
    # Window D: elapsed 0.05, used 2% -> pace -60% (early)
    wd = QuotaWindow(
        label="monthly",
        used_percent=2,
        window_minutes=10000,
        resets_at=NOW + timedelta(minutes=9500),
    )
    pct2, early2 = overall_pace([wc, wd], NOW)
    assert pct2 == 80
    assert early2 is False
    assert overall_pace_early([wc, wd], NOW) is False


def test_matrix_cell_shows_tilde_and_columns_aligned():
    w_early = QuotaWindow(
        label="monthly",
        used_percent=73,
        window_minutes=43200,
        resets_at=NOW + timedelta(days=29),
    )
    snap = Snapshot(
        collected_at=NOW,
        accounts=[
            AccountUsage(provider="opencode-go", source="test", windows=[w_early]),
            AccountUsage(provider="codex", source="test", windows=[window(), window(75, 10080)]),
        ],
    )
    text = render_clock_matrix([], snapshot=snap, color=False, width=200)
    lines = text.splitlines()
    oc_line = next(line for line in lines if "oc-go" in line)
    codex_line = next(line for line in lines if "codex" in line)
    assert "+1360%~" in oc_line
    assert "+50%" in codex_line
    assert "~" not in codex_line
    # Month column in oc-go shows ~ and PACE column shows +1360%~
    assert oc_line.count("+1360%~") == 2
    # Ensure footer explains ~
    assert "~ = early: under a tenth of the window has elapsed" in text


def test_json_fields_pace_early_and_overall_pace_early():
    w_early = QuotaWindow(
        label="monthly",
        used_percent=73,
        window_minutes=43200,
        resets_at=NOW + timedelta(days=29),
    )
    snap = Snapshot(
        collected_at=NOW,
        accounts=[
            AccountUsage(provider="opencode-go", source="test", windows=[w_early]),
            AccountUsage(provider="codex", source="test", windows=[window(100)]),
        ],
    )
    data = enrich_snapshot(snap.to_dict(), now=NOW)
    # opencode-go has early window
    assert data["accounts"][0]["overall_pace_early"] is True
    assert data["accounts"][0]["windows"][0]["pace_early"] is True
    assert "pace +1360%~" in summary_line(available_pools(data)[0])
    # codex is exhausted -> pace is None, pace_early is False, overall_pace_early is False
    assert data["accounts"][1]["overall_pace_percent"] is None
    assert data["accounts"][1]["overall_pace_early"] is False
    assert data["accounts"][1]["windows"][0]["pace_percent"] is None
    assert data["accounts"][1]["windows"][0]["pace_early"] is False
    # SEMANTICS contains the new glossary keys
    assert "pace_early" in data["semantics"]
    assert "overall_pace_early" in data["semantics"]
