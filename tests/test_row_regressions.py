"""Row-set, clock-column, and alignment-arrow regressions from the 3.5/3.6 pool work.

CodexBar names every vendor's rate slots ``primary`` / ``secondary`` /
``tertiary``. Those ids are Cursor pools only. Treating them as Cursor for
other vendors duplicated a ``total`` row, parked weekly/monthly on their own
``auto`` rows, and left a ``<-`` on rows that had no pace (and on Cursor
grok-bot, whose only clock is weekly).
"""

from datetime import datetime, timedelta, timezone

from aiuse.analysis.history import window_series_key
from aiuse.analysis.pace import independent_pool_key, partition_independent_pools
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, UsageCredits
from aiuse.report import _build_matrix_rows, _matrix_shows_account, _missing_clock_glyph, render_clock_matrix

NOW = datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc)


def _window(
    label: str,
    used: float,
    minutes: int,
    *,
    window_id: str | None = None,
    resets_in: timedelta | None = None,
) -> QuotaWindow:
    return QuotaWindow(
        label=label,
        used_percent=used,
        remaining_percent=max(0.0, 100.0 - used),
        window_minutes=minutes,
        window_id=window_id,
        resets_at=NOW + resets_in if resets_in is not None else None,
    )


def _account(
    provider: str,
    windows: list[QuotaWindow],
    *,
    usage_credits: UsageCredits | None = None,
) -> AccountUsage:
    return AccountUsage(
        source="codexbar",
        provider=provider,
        account="djbclark@gmail.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=windows,
        usage_credits=usage_credits,
    )


def _snapshot() -> Snapshot:
    """Fixture shaped like Daniel's board: CodexBar slot ids on every vendor."""
    return Snapshot(
        collected_at=NOW,
        accounts=[
            _account(
                "grok",
                [
                    _window(
                        "Grok usage limit",
                        100,
                        10080,
                        window_id="primary",
                        resets_in=timedelta(hours=9, minutes=41),
                    ),
                    _window("weekly plan", 100, 10080, resets_in=timedelta(hours=9, minutes=41)),
                ],
                usage_credits=UsageCredits(remaining=1.69),
            ),
            _account(
                "zai",
                [
                    # Live session meter, no reset yet — the real 5h reading.
                    _window("z.ai session", 43, 300, window_id="tertiary"),
                    # Unused 5h slot CodexBar calls primary. Must not become a total row.
                    _window("z.ai 5-hour", 0, 300, window_id="primary"),
                    _window(
                        "z.ai weekly",
                        17,
                        10080,
                        window_id="secondary",
                        resets_in=timedelta(days=6, hours=18),
                    ),
                ],
            ),
            _account(
                "codex",
                [
                    _window(
                        "Codex 5-hour quota (1)",
                        0,
                        300,
                        window_id="primary",
                        resets_in=timedelta(hours=5),
                    ),
                    _window(
                        "Codex weekly quota (2)",
                        44,
                        10080,
                        window_id="secondary",
                        resets_in=timedelta(days=5, hours=11),
                    ),
                ],
            ),
            _account(
                "antigravity",
                [
                    _window(
                        "Gemini 5-hour",
                        0,
                        300,
                        window_id="primary",
                        resets_in=timedelta(hours=4, minutes=57),
                    ),
                    _window(
                        "Gemini weekly",
                        100,
                        10080,
                        window_id="secondary",
                        resets_in=timedelta(hours=22, minutes=10),
                    ),
                    _window(
                        "Claude/GPT 5-hour",
                        0,
                        300,
                        window_id="primary",
                        resets_in=timedelta(hours=4, minutes=57),
                    ),
                    _window(
                        "Claude/GPT weekly",
                        100,
                        10080,
                        window_id="secondary",
                        resets_in=timedelta(days=2, hours=21),
                    ),
                ],
            ),
            _account(
                "claude",
                [
                    _window("Claude Code 5-hour", 0, 300, window_id="primary"),
                    _window(
                        "Claude Code weekly",
                        98,
                        10080,
                        window_id="secondary",
                        resets_in=timedelta(days=1, hours=13),
                    ),
                    _window(
                        "Claude weekly — Fable",
                        98,
                        10080,
                        resets_in=timedelta(days=1, hours=13),
                    ),
                ],
            ),
            _account(
                "opencode-go",
                [
                    _window(
                        "OpenCode Go 5-hour",
                        1,
                        300,
                        window_id="primary",
                        resets_in=timedelta(minutes=23),
                    ),
                    _window(
                        "OpenCode Go weekly",
                        87,
                        10080,
                        window_id="secondary",
                        resets_in=timedelta(days=4, hours=3),
                    ),
                    _window(
                        "OpenCode Go monthly",
                        94,
                        43200,
                        window_id="tertiary",
                        resets_in=timedelta(days=26, hours=21),
                    ),
                ],
            ),
            _account(
                "cursor",
                [
                    _window("total", 13, 44640, window_id="primary", resets_in=timedelta(days=26, hours=4)),
                    _window("auto", 12, 44640, window_id="secondary", resets_in=timedelta(days=26, hours=4)),
                    _window(
                        "third-party",
                        89,
                        44640,
                        window_id="tertiary",
                        resets_in=timedelta(days=26, hours=4),
                    ),
                    _window("on-demand", 0, 43200, window_id="on-demand", resets_in=timedelta(days=26, hours=4)),
                    _window(
                        "grok-bot",
                        31,
                        10080,
                        window_id="cursor-grok-bot",
                        resets_in=timedelta(days=3, hours=1),
                    ),
                ],
            ),
        ],
    )


def _rows(service: str) -> list:
    built = _build_matrix_rows([], _snapshot(), None)
    return [row for row in built if row.service == service]


def _line(text: str, service: str, scope: str | None = None) -> str:
    matches = []
    for line in text.splitlines():
        padded = f" {line} "
        if f" {service} " not in padded:
            continue
        if scope is not None and f" {scope} " not in padded:
            continue
        if line.startswith("        "):
            continue
        matches.append(line)
    assert len(matches) == 1, matches
    return matches[0]


def test_codexbar_slot_ids_are_not_cursor_pools_for_other_vendors():
    assert independent_pool_key("z.ai weekly", window_id="secondary", provider="zai") is None
    assert independent_pool_key("z.ai 5-hour", window_id="primary", provider="zai") is None
    assert independent_pool_key("Codex weekly quota (2)", window_id="secondary", provider="codex") is None
    assert independent_pool_key("Gemini weekly", window_id="secondary", provider="antigravity") == "gemini"
    # Cursor still keys off the slot id when the title is renamed.
    assert independent_pool_key("renamed", window_id="primary", provider="cursor") == "cursor_total"
    assert independent_pool_key("renamed", window_id="secondary", provider="cursor") == "cursor_auto"
    assert window_series_key("zai", "z.ai weekly", 10080, "secondary") == "zai:-:weekly"
    assert window_series_key("codex", "Codex 5-hour quota (1)", 300, "primary") == "codex:-:5h"
    assert window_series_key("cursor", "renamed", 44640, "primary") == "cursor:cursor_total:monthly"

    zai_windows = _snapshot().accounts[1].windows
    assert len(partition_independent_pools(zai_windows, provider="zai")) == 1


def test_no_duplicate_total_rows_and_clocks_share_a_row():
    grok = _rows("grok")
    assert len(grok) == 1
    assert grok[0].scope == "—"
    assert set(grok[0].clocks) == {"weekly"}
    assert grok[0].tail_note is not None and "(counts down)" in grok[0].tail_note

    zai = _rows("zai/zcode")
    assert len(zai) == 1
    assert zai[0].scope == "—"
    assert set(zai[0].clocks) == {"5h", "weekly"}
    # The unused primary slot must not replace the live session reading.
    assert zai[0].clocks["5h"].used_percent == 43
    assert zai[0].clocks["weekly"].used_percent == 17

    codex = _rows("codex")
    assert len(codex) == 1
    assert codex[0].scope == "—"
    assert set(codex[0].clocks) == {"5h", "weekly"}
    assert codex[0].clocks["5h"].used_percent == 0
    assert codex[0].clocks["weekly"].used_percent == 44

    agy = _rows("agy")
    assert len(agy) == 2
    assert {row.scope for row in agy} == {"gemini", "claude/gpt"}
    assert all(set(row.clocks) == {"5h", "weekly"} for row in agy)

    claude = _rows("claude")
    assert len(claude) == 2
    shared = next(row for row in claude if row.scope == "—")
    fable = next(row for row in claude if row.scope == "fable")
    assert set(shared.clocks) == {"5h", "weekly"}
    assert fable.unranked is False
    assert fable.queue_score == 27
    assert set(fable.clocks) == {"weekly"}
    assert fable.tail_note == "Fable-cap"
    assert not any("Fable" in note for note in shared.sublimit_notes)

    oc = _rows("oc-go")
    assert len(oc) == 1
    assert oc[0].scope == "—"
    assert set(oc[0].clocks) == {"5h", "weekly", "monthly"}

    cursor = _rows("cursor")
    scopes = {row.scope: set(row.clocks) for row in cursor}
    assert "total" not in scopes
    assert scopes["auto"] == {"monthly"}
    assert scopes["3rd-party"] == {"monthly"}
    assert scopes["grok-bot"] == {"weekly"}


def _no_post_pace_arrow(line: str) -> None:
    """The in-cell arrow is not repeated after the PACE column."""
    assert "% <-" not in line
    assert "%<-" not in line


def test_empty_clock_arrows_point_at_the_windows_the_row_has():
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=220)
    grok = _line(text, "grok")
    zai = _line(text, "zai/zcode")
    codex = _line(text, "codex")
    gemini = _line(text, "agy", "gemini")
    claude_gpt = _line(text, "agy", "claude/gpt")
    claude = next(
        line
        for line in text.splitlines()
        if " claude " in f" {line} " and " fable " not in f" {line} " and not line.startswith("        ")
    )
    fable = next(line for line in text.splitlines() if " fable " in f" {line} ")
    oc = _line(text, "oc-go")
    grok_bot = _line(text, "cursor", "grok-bot")
    auto = _line(text, "cursor", "auto")

    assert "ACCT" not in text.splitlines()[0]
    assert "100u/0l" in grok and "(counts down)" in grok
    assert grok.index("->") < grok.index("100u/0l") < grok.index("<-") < grok.index("(counts down)")
    _no_post_pace_arrow(grok)

    assert zai.index("43u/57l") < zai.index("17u/83l") < zai.index("<-")
    assert "->" not in zai
    _no_post_pace_arrow(zai)

    assert codex.index("0u/100l") < codex.index("44u/56l") < codex.index("<-")
    _no_post_pace_arrow(codex)

    for line in (gemini, claude_gpt):
        assert line.index("0u/100l") < line.index("100u/0l") < line.index("<-")
        _no_post_pace_arrow(line)

    assert claude.index("0u/100l") < claude.index("98u/2l") < claude.index("<-")
    assert "Fable cap" not in claude
    assert "fable" in fable
    assert fable.index("->") < fable.index("<-")
    assert "Fable-cap" in fable
    _no_post_pace_arrow(fable)

    assert oc.index("1u/99l") < oc.index("87u/13l") < oc.index("94u/6l")
    assert "<-" not in oc and "->" not in oc

    assert "31u/69l" in grok_bot
    assert grok_bot.index("->") < grok_bot.index("31u/69l") < grok_bot.index("<-")
    assert "->" in auto and "<-" not in auto
    assert "13u/87l" in auto or "12u/88l" in auto
    for line in (grok, zai, codex, oc, grok_bot, auto, fable):
        _no_post_pace_arrow(line)

    built = _build_matrix_rows([], _snapshot(), None)
    by_service = {}
    for row in built:
        by_service.setdefault(row.service, []).append(row)
    grok_row = by_service["grok"][0]
    assert _missing_clock_glyph(grok_row, 0) == "->"
    assert _missing_clock_glyph(grok_row, 2) == "<-"
    assert not _matrix_shows_account(built)


def test_empty_clock_arrows_sit_centered_under_the_column_label():
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=220)
    header = text.splitlines()[0]
    grok = _line(text, "grok")
    for label, glyph in (("5H", "->"), ("MONTH", "<-")):
        label_mid = header.index(label) * 2 + len(label)
        glyph_mid = grok.index(glyph) * 2 + len(glyph)
        assert abs(label_mid - glyph_mid) <= 1, (label, header, grok)


def test_overflowing_legend_wraps_between_clauses_and_centers():
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=150)
    lines = text.splitlines()
    assert all(len(line) <= 150 for line in lines)
    pace = next(line for line in lines if "+N%/-N% = pace" in line)
    assert pace.startswith(" ") and pace.rstrip().endswith("PACE = the row’s highest")
    early = lines[lines.index(pace) + 1]
    assert early.startswith(" ") and early.strip() == "~ = early: under a tenth of the window has elapsed"
