"""Matrix column order (PACE after SCOPE, OTHER last) and where the AI line goes."""

from datetime import timedelta

from aiuse.collectors.cswap import _account_from_item
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, UsageCredits, utcnow
from aiuse.report import (
    AI_JSON_NOTE,
    _build_matrix_rows,
    _situation_text,
    render_clock_matrix,
    render_report,
    render_stderr_meta,
)
from aiuse.watch import render_watch_board


def _omp(now):
    return AccountUsage(
        source="codexbar",
        provider="omp",
        account="me@gmail.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="credits",
                used_percent=0.29,
                remaining_percent=99.71,
                resets_at=now + timedelta(days=20, hours=21),
                refill_capacity=250.0,
                refill_capacity_unit="usd",
                window_id="claude-cloud-credits",
            )
        ],
        balance_usd=249.27,
        spent_usd=0.73,
    )


def _grok(now):
    return AccountUsage(
        source="codexbar",
        provider="grok",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Grok usage limit",
                used_percent=0.0,
                remaining_percent=100.0,
                resets_at=now + timedelta(days=6, hours=19),
                window_minutes=10080,
            )
        ],
        usage_credits=UsageCredits(remaining=1.64, currency="USD"),
    )


def _claude(now):
    reset = (now + timedelta(days=3)).isoformat()
    return _account_from_item(
        {
            "number": 1,
            "usageStatus": "ok",
            "usage": {
                "fiveHour": {"pct": 3.0, "resetsAt": (now + timedelta(hours=3)).isoformat()},
                "sevenDay": {"pct": 40.0, "resetsAt": reset},
                "scoped": [{"name": "Fable", "pct": 2.0, "resetsAt": reset}],
            },
        },
        1,
    )


def _snapshot(*accounts, errors=None):
    now = utcnow()
    built = [make(now) for make in accounts] if accounts else [_omp(now), _grok(now), _claude(now)]
    return Snapshot(collected_at=now, accounts=built, collector_errors=errors or [])


def _row_line(text, service, *, extra=None):
    return next(
        line
        for line in text.splitlines()
        if f" {service} " in f" {line} " and (extra is None or f" {extra} " in f" {line} ")
    )


def test_omp_row_tail_note_is_dollars_left():
    rows = _build_matrix_rows([], _snapshot(_omp), None)
    (omp,) = [row for row in rows if row.service == "omp"]
    assert omp.tail_note == "$249.27"
    assert omp.note is None


def test_rate_gated_omp_is_not_a_spendable_clock():
    now = utcnow()
    omp = _omp(now)
    omp.balance_usd = 95.57
    omp.spent_usd = 104.43
    omp.rate_gated_by = {
        "provider": "claude",
        "window_label": "Claude Code weekly",
        "resets_at": (now + timedelta(hours=41)).isoformat(),
    }
    snap = Snapshot(collected_at=now, accounts=[omp])
    text = render_clock_matrix([], snapshot=snap, color=False, width=160)
    line = _row_line(text, "omp")
    assert line.startswith("empty")
    assert "rate-gated until" in line
    assert "$95.57 left" in line
    assert "99l" not in line
    assert "u/" not in line


def test_pace_column_sits_after_scope_before_the_clocks():
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=200)
    header = text.splitlines()[0].split()
    assert header[: header.index("PACE") + 1][-2:] == ["SCOPE", "PACE"]
    assert header.index("PACE") < header.index("5H") < header.index("WEEK") < header.index("MONTH")
    assert header[-1] == "OTHER"


def test_other_column_header_width_and_alignment():
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=200)
    header = text.splitlines()[0]
    col = header.index("OTHER")
    omp = _row_line(text, "omp")
    grok = _row_line(text, "grok")
    fable = _row_line(text, "claude", extra="fable")
    # Left-aligned: every note starts under the O of OTHER.
    assert omp[col:] == "$249.27"
    assert grok[col:] == "$1.64"
    assert fable[col:] == "Fable-cap"
    assert "(counts down)" not in text
    # The pace placeholder stays in its own column, right-aligned under PACE.
    pace_end = header.index("PACE") + len("PACE")
    assert grok[pace_end - 1] == "—"


def test_other_column_absent_without_tail_notes():
    now = utcnow()
    grok = _grok(now)
    grok.usage_credits = None
    text = render_clock_matrix([], snapshot=Snapshot(collected_at=now, accounts=[grok]), color=False, width=200)
    assert "OTHER" not in text


def test_stale_follows_other():
    snap = _snapshot(_omp, _grok)
    snap.accounts[0].collected_at = utcnow() - timedelta(days=2)
    text = render_clock_matrix([], snapshot=snap, color=False, width=200)
    omp = _row_line(text, "omp")
    assert omp.endswith("$249.27 STALE")
    col = text.splitlines()[0].index("OTHER")
    assert omp[col:].startswith("$249.27")


def test_ai_line_leads_the_stderr_meta():
    meta = render_stderr_meta(_snapshot(errors=["tokscale: boom"]), [], color=False).splitlines()
    assert meta[0] == AI_JSON_NOTE
    assert meta[1].startswith("Collected at")
    assert meta[-1] == "Detail: ai --full"
    assert AI_JSON_NOTE not in render_stderr_meta(_snapshot(), [], color=False, ai_note=False)


def test_ai_line_leaves_the_legend_when_meta_shows_it():
    snap = _snapshot()
    shown = render_clock_matrix([], snapshot=snap, color=False, width=200, ai_note=False)
    assert AI_JSON_NOTE not in shown
    # -q (no meta) keeps it as the last legend line, as before.
    quiet = render_report(snap, [], config={}, color=False, glance_width=200)
    assert quiet.splitlines()[-1].strip() == AI_JSON_NOTE
    assert AI_JSON_NOTE not in render_report(snap, [], config={}, color=False, glance_width=200, ai_note=False)


def test_watch_board_puts_ai_legend_and_footer_above_the_table():
    now = utcnow()
    board = render_watch_board(
        _snapshot(),
        [],
        color=False,
        now=now,
        next_in=47,
        sample_schedule=(now - timedelta(minutes=5), now + timedelta(minutes=10), "active"),
    ).splitlines()
    assert board[0].startswith("aiuse watch")
    assert board[1].startswith("sampler:")
    assert board[2].strip() == AI_JSON_NOTE
    assert sum(AI_JSON_NOTE in line for line in board) == 1
    header = next(i for i, line in enumerate(board) if line.split()[:2] == ["##", "SERVICE"])
    collected = next(i for i, line in enumerate(board) if "Collected at" in line)
    detail = next(i for i, line in enumerate(board) if line.strip() == "Detail: ai --full")
    legend = next(i for i, line in enumerate(board) if "u/l = percent used" in line)
    assert 2 < legend < collected < detail < header
    # Nothing below the table: the last line is a table row.
    assert board[-1].split()[0] in {"error", "empty", "n/a", "slow", "mid", "use"} or board[-1].startswith("     ")
    assert not any("Collected at" in line or "u/l =" in line for line in board[header:])


def test_watch_board_quiet_keeps_the_ai_line_and_drops_the_footer():
    board = render_watch_board(_snapshot(), [], color=False, quiet=True).splitlines()
    assert board[1].strip() == AI_JSON_NOTE
    assert not any("Collected at" in line for line in board)


def test_situation_text_keeps_the_closing_bracket_when_it_truncates():
    assert _situation_text("unlimited · slow — use sparingly / last resort", 28) == "* [unlimited · slow — use …]"
    assert _situation_text("session expired", 80) == "* [session expired]"


def test_situation_note_starts_at_the_first_column():
    now = utcnow()
    broken = AccountUsage(source="tokscale", provider="opencode", error="session expired")
    snap = Snapshot(collected_at=now, accounts=[_omp(now), broken])
    text = render_clock_matrix([], snapshot=snap, color=False, width=200)
    header = text.splitlines()[0]
    error = next(line for line in text.splitlines() if line.startswith("error"))
    # No clock cells: the explanation replaces SCOPE onward, left-justified.
    assert error[header.index("SCOPE") :].startswith("* [session expired]")
    assert "—" not in error.split("*", 1)[0]
