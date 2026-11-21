"""Matrix column order (PACE after SCOPE, OTHER last) and where the AI line goes."""

import json
from datetime import timedelta

import pytest

from aiuse.collectors import codexbar
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
from aiuse.timefmt import format_stamp
from aiuse.watch import render_watch_board


@pytest.mark.parametrize("age_hours", [2, 26])
@pytest.mark.parametrize("gated", [False, True])
def test_fresh_widget_envelope_retains_old_pi_spend_age(tmp_path, age_hours, gated):
    now = utcnow()
    measured = now - timedelta(hours=age_hours)
    cache = tmp_path / "widget.json"
    cache.write_text(
        json.dumps(
            {
                "generatedAt": now.isoformat(),
                "entries": [
                    {
                        "provider": "openai",
                        "updatedAt": measured.isoformat(),
                        "providerCost": {"used": 2.8, "currencyCode": "USD", "limit": 200},
                    }
                ],
            }
        )
    )
    accounts = codexbar._read_widget_snapshot(cache, 300, ["pi"], None)
    assert accounts is not None
    (account,) = accounts
    if age_hours > 24:
        # Credit expiry can clear this convenience field; the historical meter
        # still carries spend and must retain its original measurement clock.
        account.spent_usd = None
    if gated:
        account.rate_gated_by = {
            "provider": "pi",
            "window_label": "API rate",
            "resets_at": (now + timedelta(hours=1)).isoformat(),
        }
    snapshot = Snapshot(collected_at=now, accounts=accounts)
    (matrix_row,) = _build_matrix_rows([], snapshot, None)
    assert matrix_row.measured_at == measured
    assert matrix_row.stale
    text = render_clock_matrix([], snapshot=snapshot, color=False, width=200)
    line = _row_line(text, "openai-api")
    assert format_stamp(measured) in line
    if not gated:
        assert "↑ $2.80 PAYG" in line
        assert "↓" not in line


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
    (omp,) = [row for row in rows if row.service == "claude-api"]
    assert omp.tail_note == "↓$249.27/$250.00 plan"
    assert omp.note is None
    assert omp.scope == "—"


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
    line = _row_line(text, "claude-api")
    assert line.startswith("empty")
    assert "rate-gated until" in line
    assert "↓ $95.57 left of $250.00 plan" in line
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
    omp = _row_line(text, "claude-api")
    grok = _row_line(text, "grok")
    fable = _row_line(text, "claude", extra="fable")
    # Left-aligned: every note starts under the O of OTHER.
    assert omp[col:] == "↓$249.27/$250.00 plan"
    assert grok[col:] == "↓$1.64 prepaid"
    assert fable[col:] == "Fable-cap"
    assert "(counts down)" not in text
    # Zero-used clocked row shows -100%~ right-aligned under PACE.
    pace_end = header.index("PACE") + len("PACE")
    assert grok[pace_end - len("-100%~") : pace_end] == "-100%~"

    # An unclocked row keeps the pace placeholder right-aligned under PACE.
    now = utcnow()
    unclocked_grok = _grok(now)
    unclocked_grok.windows[0].resets_at = None
    unclocked_text = render_clock_matrix(
        [], snapshot=Snapshot(collected_at=now, accounts=[unclocked_grok]), color=False, width=200
    )
    unclocked_header = unclocked_text.splitlines()[0]
    unclocked_pace_end = unclocked_header.index("PACE") + len("PACE")
    assert _row_line(unclocked_text, "grok")[unclocked_pace_end - 1] == "—"


def test_other_column_absent_without_tail_notes():
    now = utcnow()
    grok = _grok(now)
    grok.usage_credits = None
    text = render_clock_matrix([], snapshot=Snapshot(collected_at=now, accounts=[grok]), color=False, width=200)
    assert "OTHER" not in text


def test_stale_follows_other():
    from aiuse.timefmt import format_stamp

    snap = _snapshot(_omp, _grok)
    snap.accounts[0].collected_at = utcnow() - timedelta(days=2)
    text = render_clock_matrix([], snapshot=snap, color=False, width=200)
    omp = _row_line(text, "claude-api")
    measured = snap.accounts[0].collected_at
    assert measured is not None
    assert omp.endswith("↓$249.27/$250.00 plan " + format_stamp(measured))
    assert "STALE" not in text
    col = text.splitlines()[0].index("OTHER")
    assert omp[col:].startswith("↓$249.27/$250.00 plan")


def test_ai_line_leads_the_stderr_meta():
    meta = render_stderr_meta(_snapshot(errors=["tokscale: boom"]), [], color=False).splitlines()
    assert meta[0] == AI_JSON_NOTE
    assert meta[1].startswith("Collected by aiuse ? at")
    assert meta[-1] == "Detail: aiuse --full"
    assert AI_JSON_NOTE not in render_stderr_meta(_snapshot(), [], color=False, ai_note=False)


def test_ai_line_leaves_the_legend_when_meta_shows_it():
    snap = _snapshot()
    shown = render_clock_matrix([], snapshot=snap, color=False, width=200, ai_note=False)
    assert AI_JSON_NOTE not in shown
    # -q (no meta) keeps it as the last legend line, as before.
    quiet = render_report(snap, [], config={}, color=False, glance_width=200)
    assert quiet.splitlines()[-1].strip() == AI_JSON_NOTE
    assert AI_JSON_NOTE not in render_report(snap, [], config={}, color=False, glance_width=200, ai_note=False)


def _board(width, *, quiet=False, snapshot=None, errors=None):
    now = utcnow()
    return render_watch_board(
        snapshot or _snapshot(errors=errors),
        [],
        color=False,
        quiet=quiet,
        now=now,
        next_in=47,
        sample_schedule=(now - timedelta(minutes=5), now + timedelta(minutes=10), "active"),
        width=width,
    ).splitlines()


def _table_header(board):
    return next(i for i, line in enumerate(board) if line.split()[:2] == ["##", "POOL"])


def test_watch_board_puts_ai_legend_and_footer_above_the_table():
    board = _board(80)
    assert board[0].startswith("aiuse watch")
    assert "sampler " in board[0]
    assert sum(AI_JSON_NOTE in line for line in board) == 1
    header = _table_header(board)
    ai = next(i for i, line in enumerate(board) if AI_JSON_NOTE in line)
    collected = next(i for i, line in enumerate(board) if "Collected by aiuse" in line)
    detail = next(i for i, line in enumerate(board) if line.strip() == "Detail: aiuse --full")
    legend = next(i for i, line in enumerate(board) if "u/l = percent used" in line)
    assert 0 < ai < legend < detail < header
    assert collected < header
    # Nothing below the table: the last line is a table row.
    assert board[-1].split()[0] in {"error", "empty", "n/a", "slow", "mid", "use"} or board[-1].startswith("     ")
    assert not any("Collected by aiuse" in line or "u/l =" in line for line in board[header:])


def test_watch_board_narrow_stacks_ai_and_meta_on_separate_rows():
    board = _board(80)
    ai = next(i for i, line in enumerate(board) if AI_JSON_NOTE in line)
    meta = next(i for i, line in enumerate(board) if "Collected by aiuse" in line)
    assert ai != meta
    assert board[ai].strip() == AI_JSON_NOTE
    assert board[1].strip() == AI_JSON_NOTE


def test_watch_board_wide_puts_meta_right_of_the_ai_line_on_one_row():
    board = _board(200)
    header = _table_header(board)
    table_width = max(len(line) for line in board[header:])
    row = next(line for line in board if AI_JSON_NOTE in line)
    assert row is board[1]
    assert row.startswith(AI_JSON_NOTE)
    assert "Collected by aiuse ? at" in row
    meta = row[row.index("Collected by aiuse") :]
    # Flush right: the table is capped narrower than the two lines together, so
    # the row is exactly as wide as they need, with three spaces between.
    assert len(row) == len(AI_JSON_NOTE) + 3 + len(meta)
    assert len(row) == max(table_width, len(AI_JSON_NOTE) + 3 + len(meta))
    assert row[len(AI_JSON_NOTE) :].startswith("   Collected by")
    # The meta line is no longer repeated in the footer.
    assert sum("Collected by aiuse" in line for line in board) == 1
    assert "u/l =" in board[2]


def test_side_by_side_right_aligns_to_the_board_width_and_ignores_ansi():
    from aiuse.report import ClockMatrixParts

    parts = ClockMatrixParts(table=[], center_width=40, avail=60)
    row = parts.side_by_side("AI", "meta")
    assert row == "AI" + " " * 34 + "meta"
    styled = parts.side_by_side("\x1b[2mAI\x1b[0m", "\x1b[2mmeta\x1b[0m")
    assert styled is not None
    assert len(styled.replace("\x1b[2m", "").replace("\x1b[0m", "")) == 40
    # Wider than the table but within the terminal: as wide as they need, 3-space gap.
    assert parts.side_by_side("a" * 20, "b" * 30) == "a" * 20 + "   " + "b" * 30
    # Beyond the terminal: never truncate, never overflow; the caller stacks.
    assert parts.side_by_side("a" * 30, "b" * 30) is None
    # No table to align to.
    assert ClockMatrixParts(table=[], center_width=0, avail=60).side_by_side("a", "b") is None


def test_watch_board_wide_keeps_collector_errors_and_detail_in_the_footer():
    board = _board(200, errors=["tokscale: boom"])
    header = _table_header(board)
    assert any("tokscale: boom" in line for line in board[:header])
    assert any(line.strip() == "Detail: aiuse --full" for line in board[:header])
    assert sum("Collected by aiuse" in line for line in board) == 1


def test_watch_board_wide_but_too_tight_for_both_falls_back_to_stacked_rows():
    # Find the narrowest width where the two lines still share a row, then go one below.
    joined = next(w for w in range(60, 260) if any(AI_JSON_NOTE in r and "Collected by" in r for r in _board(w)))
    assert not any(AI_JSON_NOTE in r and "Collected by" in r for r in _board(joined - 1)) or joined == 60
    stacked = _board(joined - 1)
    assert sum("Collected by aiuse" in line for line in stacked) == 1
    assert not any(AI_JSON_NOTE in line and "Collected by" in line for line in stacked)


def test_watch_board_quiet_keeps_the_ai_line_and_drops_the_footer():
    for width in (80, 200):
        board = _board(width, quiet=True)
        assert board[1].strip() == AI_JSON_NOTE
        assert not any("Collected by aiuse" in line for line in board)


def test_watch_board_meta_shows_the_version_recorded_on_the_snapshot():
    snap = _snapshot()
    snap.aiuse_version = "4.9.9"
    meta = next(line for line in _board(200, snapshot=snap) if "Collected by" in line)
    assert "Collected by aiuse 4.9.9 at" in meta
    assert "aiuse ?" not in meta


def test_situation_text_keeps_the_closing_bracket_when_it_truncates():
    assert _situation_text("unlimited · slow — use sparingly / last resort", 28) == "* [unlimited · slow — use …]"
    assert _situation_text("session expired", 80) == "* [session expired]"


def test_situation_note_keeps_scope_before_the_first_data_column():
    now = utcnow()
    broken = AccountUsage(source="tokscale", provider="opencode", error="session expired")
    codex = AccountUsage(
        source="codexbar",
        provider="codex",
        windows=[QuotaWindow(label="weekly", used_percent=10.0, window_minutes=10080)],
    )
    snap = Snapshot(collected_at=now, accounts=[codex, broken])
    text = render_clock_matrix([], snapshot=snap, color=False, width=200)
    header = text.splitlines()[0]
    error = next(line for line in text.splitlines() if line.startswith("error"))
    # No clock cells: retain SCOPE, then render the left-justified explanation.
    scope_and_note = error[header.index("SCOPE") :]
    assert scope_and_note.split("*", 1)[0].strip() == "—"
    assert scope_and_note[scope_and_note.index("*") :].startswith("* [session expired]")
