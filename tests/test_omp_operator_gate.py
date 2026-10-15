"""The omp operator gate: "AI: Do not use without asking operator" everywhere omp shows."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiuse.analysis.selfdescribe import SEMANTICS, available_pools, enrich_snapshot, summary_line
from aiuse.analysis.use_or_lose import _mark_operator_gate
from aiuse.chat_format import render_chat_report
from aiuse.models import (
    OPERATOR_APPROVAL_NOTICE,
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
)
from aiuse.report import (
    _build_matrix_rows,
    _matrix_needed_width,
    _matrix_shows_account,
    _MatrixLayout,
    _strip_ansi,
    render_clock_matrix,
)
from aiuse.serve import _alerts_from_dicts, _snapshot_from_accounts_dict

NOW = datetime(2026, 10, 15, 11, 0, tzinfo=UTC)
EXPIRY = datetime(2026, 11, 5, 7, 59, tzinfo=UTC)
GATE_TEXT = "(AI: Do not use without asking operator)"
ARROW_LEGEND = "-> / <- in an empty clock"


def _omp(*, gated: bool = True, extra: list[QuotaWindow] | None = None, window: bool = True) -> AccountUsage:
    windows = list(extra or [])
    if window:
        windows.append(
            QuotaWindow(
                label="credits",
                used_percent=0.29,
                remaining_percent=99.71,
                resets_at=EXPIRY,
                refill_capacity=250.0,
                refill_capacity_unit="usd",
                window_id="claude-cloud-credits",
            )
        )
    return AccountUsage(
        source="codexbar",
        provider="omp",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW if window else BillingKind.PREPAID_BALANCE,
        windows=windows,
        balance_usd=249.27,
        spent_usd=0.73,
        requires_operator_approval=gated,
        collected_at=NOW,
    )


def _codex() -> AccountUsage:
    """A row with all three clocks, so it adds no arrow of its own."""
    return AccountUsage(
        source="codexbar",
        provider="codex",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(label="5-hour", used_percent=29, resets_at=NOW + timedelta(hours=2), window_minutes=300),
            QuotaWindow(label="weekly", used_percent=40, resets_at=NOW + timedelta(days=3), window_minutes=10080),
            QuotaWindow(label="monthly", used_percent=10, resets_at=NOW + timedelta(days=20), window_minutes=43200),
        ],
        collected_at=NOW,
    )


def _matrix(accounts: list[AccountUsage], *, color: bool = False, width: int = 170) -> list[str]:
    snap = Snapshot(collected_at=NOW, accounts=accounts)
    return render_clock_matrix([], snapshot=snap, color=color, width=width).splitlines()


def _omp_line(lines: list[str]) -> str:
    return next(line for line in lines if re.search(r"\bomp\b", _strip_ansi(line)))


# --- matrix -------------------------------------------------------------------


def test_matrix_centers_the_notice_across_5h_and_week():
    lines = _matrix([_omp(), _codex()])
    header, row = lines[0], _omp_line(lines)

    assert GATE_TEXT in row
    assert "->" not in row and "<-" not in row
    # The 5H column starts right after PACE; MONTH (``>0u/100l``, the widest
    # percent pair, so not left-padded) starts one gap after WEEK ends.
    region_start = header.index("PACE") + len("PACE") + 1
    region_end = row.index(">0u/100l") - 1
    text_start = row.index(GATE_TEXT)
    left = text_start - region_start
    right = region_end - (text_start + len(GATE_TEXT))
    assert left >= 0 and right >= 0
    assert abs(left - right) <= 1
    # MONTH and OTHER stay as they are, under their headers.
    assert row.index(">0u/100l") + len(">0u/100l") == header.index("MONTH") + len("MONTH")
    assert row.index("$0.73/$249.27") == header.index("OTHER")


def test_matrix_columns_stay_aligned_in_color():
    plain = _matrix([_omp(), _codex()])
    colored = _matrix([_omp(), _codex()], color=True)

    assert [_strip_ansi(line).rstrip() for line in colored] == [line.rstrip() for line in plain]


def test_matrix_gate_row_raises_no_arrow_legend():
    gated = "\n".join(_matrix([_omp(), _codex()]))
    ungated = "\n".join(_matrix([_omp(gated=False), _codex()]))

    assert ARROW_LEGEND not in gated
    # The same row without the gate shows its arrows and their legend.
    assert ARROW_LEGEND in ungated and GATE_TEXT not in ungated


def test_matrix_reserves_width_for_the_whole_notice():
    accounts = [_omp(), _codex()]
    snap = Snapshot(collected_at=NOW, accounts=accounts)
    rows = _build_matrix_rows([], snap, None)
    layout = _MatrixLayout(show_scope=any(r.scope != "—" for r in rows), show_account=_matrix_shows_account(rows))
    needed = _matrix_needed_width(rows, layout)

    lines = _matrix(accounts, width=needed)

    assert GATE_TEXT in _omp_line(lines)
    assert all(len(line) <= needed for line in lines[:3])


def test_matrix_narrow_terminal_clamps_without_misaligning():
    for width in (60, 80):
        lines = _matrix([_omp(), _codex()], width=width)
        table = lines[:3]
        assert all(len(_strip_ansi(line)) <= width for line in table)
        row = _omp_line(table)
        # Too narrow for the full notice: the short wording stays whole, never a clipped copy of the long one.
        assert GATE_TEXT in row or "(AI: ask operator)" in row
        if GATE_TEXT not in row:
            assert "Do not use" not in row
        header = table[0]
        if "MONTH" in header and ">0u/100l" in row:
            assert row.index(">0u/100l") + len(">0u/100l") == header.index("MONTH") + len("MONTH")


def test_matrix_data_in_5h_or_week_wins_over_the_notice():
    five_hour = QuotaWindow(label="5-hour", used_percent=12, resets_at=NOW + timedelta(hours=1), window_minutes=300)
    lines = _matrix([_omp(extra=[five_hour]), _codex()])

    row = _omp_line(lines)
    assert GATE_TEXT not in row
    assert "12u/88l" in row


def test_matrix_gate_row_without_clocks_still_shows_the_notice():
    lines = _matrix([_omp(window=False), _codex()])

    assert GATE_TEXT in _omp_line(lines)


def test_matrix_gate_text_is_dim_in_color():
    row = _omp_line(_matrix([_omp(), _codex()], color=True))

    assert f"\x1b[2m{GATE_TEXT}\x1b[0m" in row


# --- JSON -----------------------------------------------------------------------


def test_to_dict_has_the_gate_only_when_true():
    gated = _omp().to_dict()
    assert gated["requires_operator_approval"] is True
    assert gated["ai_notice"] == OPERATOR_APPROVAL_NOTICE
    plain = _omp(gated=False).to_dict()
    assert "requires_operator_approval" not in plain and "ai_notice" not in plain


def test_cached_snapshot_round_trip_keeps_the_gate():
    snap = Snapshot(collected_at=NOW, accounts=[_omp(), _codex()]).to_dict()

    omp, codex = _snapshot_from_accounts_dict(snap).accounts

    assert omp.requires_operator_approval is True
    assert codex.requires_operator_approval is False


def test_available_entry_and_summary_line_carry_the_gate():
    snap = Snapshot(collected_at=NOW, accounts=[_omp(), _codex()]).to_dict()
    enrich_snapshot(snap, now=NOW)

    pools = {p["provider"]: p for p in available_pools(snap)}
    assert pools["omp"]["requires_operator_approval"] is True
    assert pools["omp"]["ai_notice"] == OPERATOR_APPROVAL_NOTICE
    assert "requires_operator_approval" not in pools["codex"]
    (line,) = [line for line in snap["summary_lines"] if line.startswith("omp")]
    assert line.endswith(f" — {OPERATOR_APPROVAL_NOTICE}")
    assert summary_line(pools["omp"]).endswith(f" — {OPERATOR_APPROVAL_NOTICE}")
    assert not summary_line(pools["codex"]).endswith(OPERATOR_APPROVAL_NOTICE)
    assert "requires_operator_approval" in SEMANTICS and "ai_notice" in SEMANTICS


# --- chat -----------------------------------------------------------------------


def test_chat_adds_the_notice_under_the_status_line():
    lines = render_chat_report(Snapshot(collected_at=NOW, accounts=[_omp(), _codex()]), []).splitlines()

    i = next(n for n, line in enumerate(lines) if "**omp · credits**" in line)
    assert lines[i + 1].startswith("   ↳ `100% left · $0.73 spent · $249.27 left of $250.00")
    assert lines[i + 2] == f"   ↳ {OPERATOR_APPROVAL_NOTICE}"
    assert sum(OPERATOR_APPROVAL_NOTICE in line for line in lines) == 1


def test_chat_ungated_omp_has_no_notice():
    text = render_chat_report(Snapshot(collected_at=NOW, accounts=[_omp(gated=False)]), [])

    assert OPERATOR_APPROVAL_NOTICE not in text


def test_chat_prepaid_fallback_keeps_the_notice():
    lines = render_chat_report(Snapshot(collected_at=NOW, accounts=[_omp(window=False)]), []).splitlines()

    i = next(n for n, line in enumerate(lines) if "**omp**" in line)
    assert lines[i + 1] == f"   ↳ {OPERATOR_APPROVAL_NOTICE}"


def _alert(provider: str = "omp", *, account: str | None = None) -> UseOrLoseAlert:
    return UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider=provider,
        account=account,
        window_label="credits",
        remaining_percent=99.71,
        days_until_reset=20.9,
        plan=None,
        message=f"{provider} credits: 100% left",
        source="codexbar",
        score=50.0,
    )


def test_chat_action_item_carries_the_notice():
    alert = _alert()
    _mark_operator_gate([alert], Snapshot(collected_at=NOW, accounts=[_omp()]))
    text = render_chat_report(Snapshot(collected_at=NOW, accounts=[_omp()]), [alert])

    action = text.split("📌 **ACTION**", 1)[1].split("\n\n", 1)[0]
    assert action.rstrip().endswith(f"— {OPERATOR_APPROVAL_NOTICE}")


# --- alerts ---------------------------------------------------------------------


def test_alerts_on_gated_accounts_are_flagged_once():
    gated, other = _alert(), _alert("codex")
    snap = Snapshot(collected_at=NOW, accounts=[_omp(), _codex()])

    _mark_operator_gate([gated, other], snap)
    _mark_operator_gate([gated, other], snap)

    assert gated.requires_operator_approval is True
    assert gated.message == f"omp credits: 100% left — {OPERATOR_APPROVAL_NOTICE}"
    assert gated.to_dict()["requires_operator_approval"] is True
    assert gated.to_dict()["ai_notice"] == OPERATOR_APPROVAL_NOTICE
    assert other.requires_operator_approval is False
    assert "requires_operator_approval" not in other.to_dict()
    (restored,) = _alerts_from_dicts([gated.to_dict()])
    assert restored.requires_operator_approval is True


def test_analyze_use_or_lose_marks_gated_alerts():
    from aiuse.analysis.use_or_lose import analyze_use_or_lose

    alerts = analyze_use_or_lose(Snapshot(collected_at=NOW, accounts=[_omp(), _codex()]))

    for alert in alerts:
        assert alert.requires_operator_approval == (alert.provider == "omp")
        assert (OPERATOR_APPROVAL_NOTICE in alert.message) == (alert.provider == "omp")


# --- one source of truth --------------------------------------------------------


def test_notice_literal_lives_only_in_models():
    src = Path(__file__).resolve().parents[1] / "src" / "aiuse"
    hits = [p.relative_to(src).as_posix() for p in src.rglob("*.py") if "Do not use without asking" in p.read_text()]

    assert hits == ["models.py"]


def test_gate_cell_falls_back_to_the_short_wording_then_clamps():
    from aiuse.report import _gate_cell, _Style

    style = _Style(False)
    assert _gate_cell(style, 40).strip() == GATE_TEXT
    assert _gate_cell(style, 30).strip() == "(AI: ask operator)"
    assert len(_gate_cell(style, 30)) == 30
    assert _gate_cell(style, 10).strip().endswith("…")
