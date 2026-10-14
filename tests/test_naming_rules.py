"""Repo naming rules for Cursor pool families."""

from __future__ import annotations

from pathlib import Path

from aiuse.analysis.pace import cursor_pool_family, independent_pool_key

_CURSOR_FAMILIES = frozenset({"total", "auto", "third_party", "on_demand", "grok_bot"})


def test_grokbot_banned_in_src():
    root = Path(__file__).resolve().parents[1] / "src"
    hits: list[str] = []
    for path in root.rglob("*.py"):
        if "grokbot" in path.read_text(encoding="utf-8").casefold():
            hits.append(str(path))
    assert hits == []


def test_cursor_pool_slugs_match_family():
    cases = [
        ("total", "primary", "cursor_total"),
        ("auto", "secondary", "cursor_auto"),
        ("third-party", "tertiary", "cursor_third_party"),
        ("on-demand", "on-demand", "cursor_on_demand"),
        ("grok-bot", "cursor-grok-bot", "cursor_grok_bot"),
    ]
    for label, wid, expected in cases:
        family = cursor_pool_family(label, window_id=wid)
        assert family in _CURSOR_FAMILIES
        assert independent_pool_key(label, window_id=wid) == expected
        assert independent_pool_key(label, window_id=wid) == f"cursor_{family}"
