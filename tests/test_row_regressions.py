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
from aiuse.report import _build_matrix_rows, _show_alignment_arrow, render_clock_matrix

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
    assert grok[0].tail_note is not None and "extra credits" in grok[0].tail_note

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
    assert len(claude) == 1
    assert claude[0].scope == "—"
    assert set(claude[0].clocks) == {"5h", "weekly"}
    assert any("Fable" in note for note in claude[0].sublimit_notes)

    oc = _rows("oc-go")
    assert len(oc) == 1
    assert oc[0].scope == "—"
    assert set(oc[0].clocks) == {"5h", "weekly", "monthly"}

    cursor = _rows("cursor")
    scopes = {row.scope: set(row.clocks) for row in cursor}
    assert "total" not in scopes
    assert scopes["auto"] == {"monthly"}
    assert scopes["third-party"] == {"monthly"}
    assert scopes["on-demand"] == {"monthly"}
    assert scopes["grok-bot"] == {"weekly"}


def test_alignment_arrow_only_on_paced_rows_that_stop_before_monthly():
    text = render_clock_matrix([], snapshot=_snapshot(), color=False, width=220)
    grok = _line(text, "grok")
    zai = _line(text, "zai/zcode")
    codex = _line(text, "codex")
    gemini = _line(text, "agy", "gemini")
    claude_gpt = _line(text, "agy", "claude/gpt")
    claude = _line(text, "claude")
    oc = _line(text, "oc-go")
    grok_bot = _line(text, "cursor", "grok-bot")
    auto = _line(text, "cursor", "auto")

    assert "100u/0l" in grok and "extra credits" in grok and "<-" not in grok
    assert " total " not in f" {grok} "
    assert grok.index("100u/0l") < grok.index("extra credits")

    assert zai.index("43u/57l") < zai.index("17u/83l")
    assert "0u/100l" not in zai
    assert " total " not in f" {zai} " and " auto " not in f" {zai} "
    assert "<-" in zai

    assert codex.index("0u/100l") < codex.index("44u/56l")
    assert " total " not in f" {codex} " and " auto " not in f" {codex} "
    assert "<-" in codex

    for line in (gemini, claude_gpt):
        assert line.index("0u/100l") < line.index("100u/0l")
        assert "<-" not in line
        assert "EXHAUSTED" in line

    assert claude.index("0u/100l") < claude.index("98u/2l")
    assert "<-" in claude
    assert "Fable cap" in text

    assert oc.index("1u/99l") < oc.index("87u/13l") < oc.index("94u/6l")
    assert "<-" not in oc

    assert "31u/69l" in grok_bot
    assert "<-" not in grok_bot
    assert "<-" not in auto
    assert "13u/87l" in auto or "12u/88l" in auto

    built = {row.service: row for row in _build_matrix_rows([], _snapshot(), None) if row.service != "cursor"}
    assert _show_alignment_arrow(built["grok"]) is False
    assert _show_alignment_arrow(built["zai/zcode"]) is True
    assert _show_alignment_arrow(built["oc-go"]) is False
    grok_bot_row = next(row for row in _rows("cursor") if row.scope == "grok-bot")
    assert grok_bot_row.pace_percent is not None
    assert _show_alignment_arrow(grok_bot_row) is False
