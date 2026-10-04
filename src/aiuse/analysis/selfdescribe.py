"""Self-describing quota semantics (schema 1.1) — read the JSON, no docs needed.

Born from three real misreads on 2026-10-03:

1. ``used_percent: 100`` read as "100% free" (it means 100% CONSUMED).
2. agy's Claude/GPT pool read as "agy exhausted" while its Gemini pool still
   had ~75% left — one vendor, several independent pools by model family.
3. codex 5-hour 100% used + weekly 35% used read as usable — but the fullest
   window of an account binds, so codex was unusable for ~5 h.

Everything here is additive: no existing field is removed or renamed and the
numbers themselves are untouched — only their meaning is made explicit, so an
agent that has never opened a doc cannot get it wrong. Pure functions over
snapshot-shaped dicts (no I/O) so live ``--json``, the on-disk cache,
``aiuse serve`` and ``aiuse --available`` all share one truth.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from aiuse.models import parse_dt, utcnow

SCHEMA_VERSION = "1.1"

# state thresholds on remaining_percent
EXHAUSTED_BELOW = 1.0  # remaining <= 1 -> exhausted
TIGHT_BELOW = 15.0  # remaining < 15 -> tight

# age threshold for the read-time `fresh` flag (config: analysis.fresh_threshold_seconds)
FRESH_THRESHOLD_SECONDS_DEFAULT = 1500.0  # 25 min

SEMANTICS: dict[str, str] = {
    "used_percent": "share CONSUMED; 100 means exhausted, 0 means untouched",
    "remaining_percent": "share still available; decide from this",
    "headroom_percent": "alias of remaining_percent, spelled for humans",
    "state": "exhausted (<=1% left) | tight (<15% left) | ok | unknown (no data); never compute it yourself",
    "usable_now": "false if any window of the account/pool is exhausted, or no window has data; true only with evidence of headroom",
    "binding_window": "label of the window with the least remaining_percent — the one that stops you first",
    "available_at": "earliest resets_at among exhausted windows; null when nothing is exhausted or no reset time is known",
    "pool_family": "vendors that split quota by model family (antigravity: gemini vs claude_gpt; claude: default vs fable; cursor: auto/included/other); one family can be exhausted while another is fine — retry on the other family before abandoning the vendor",
    "age_seconds": "seconds since collected_at, computed at read time; stale data can hide a fresh exhaustion",
    "fresh": "age_seconds <= fresh threshold (default 1500s); when false, re-collect before trusting ok states",
    "summary_lines": "one human line per pool; always shows used AND left, never a bare percentage",
    "agent_notes": "exhaustion overrides reported by agents (source: agent-reported); expire at their reset time, after which a live collector reading wins",
}

# (substring, family, models_hint) matched against the lowercased window label,
# first hit wins. Only vendors that genuinely split pools by model family.
_FAMILY_RULES: dict[str, list[tuple[str, str, str]]] = {
    "antigravity": [
        ("claude/gpt", "claude_gpt", "agy models — claude-* / gpt-* models draw this pool"),
        ("gemini", "gemini", "agy models — gemini-* models draw this pool"),
    ],
    "claude": [
        ("fable", "fable", "Claude Fable pool (reasoning-tier models)"),
        ("5-hour", "default", "ordinary Claude models"),
        ("weekly", "default", "ordinary Claude models"),
    ],
    "cursor": [
        ("grok", "grok_bot", "Cursor Grok Bot slot"),
        ("auto", "auto", "Cursor 'Auto' model selection"),
        ("included", "included", "Cursor included models"),
        ("other", "other", "Cursor other-models slot"),
    ],
}


def window_remaining(window: Mapping[str, Any]) -> float | None:
    """Effective remaining percent: remaining if given, else 100 - used."""
    rem = window.get("remaining_percent")
    if rem is not None:
        return float(rem)
    used = window.get("used_percent")
    if used is not None:
        return max(0.0, 100.0 - float(used))
    return None


def window_state(window: Mapping[str, Any]) -> str:
    """exhausted | tight | ok | unknown — explicit so nobody computes it."""
    rem = window_remaining(window)
    if rem is None:
        return "unknown"
    if rem <= EXHAUSTED_BELOW:
        return "exhausted"
    if rem < TIGHT_BELOW:
        return "tight"
    return "ok"


def window_family(provider: str, label: str) -> tuple[str | None, str | None]:
    """(pool_family, models_hint) for split vendors; (None, None) otherwise."""
    rules = _FAMILY_RULES.get(provider)
    if not rules:
        return None, None
    low = str(label).lower()
    for needle, family, hint in rules:
        if needle in low:
            return family, hint
    return None, None


def enrich_window(window: dict[str, Any], *, provider: str) -> dict[str, Any]:
    """Add state / headroom_percent (+ pool_family, models_hint) to one window.

    Drops any stale agent-reported markers first so re-enriching a stored
    snapshot after a note expires returns the window to its collector truth.
    """
    window.pop("state_source", None)
    window.pop("agent_reported", None)
    window["state"] = window_state(window)
    window["headroom_percent"] = window_remaining(window)
    family, hint = window_family(provider, str(window.get("label") or ""))
    if family is not None:
        window["pool_family"] = family
        window["models_hint"] = hint
    return window


def _apply_note_to_window(window: dict[str, Any], note: Mapping[str, Any]) -> bool:
    if window.get("state") == "exhausted":
        return False
    window["state"] = "exhausted"
    window["state_source"] = "agent-reported"
    window["agent_reported"] = {
        "resets_at": note.get("resets_at"),
        "reason": note.get("reason"),
    }
    return True


def _note_matches(note: Mapping[str, Any], provider: str, window: Mapping[str, Any]) -> bool:
    if str(note.get("provider") or "") != provider:
        return False
    family = note.get("pool_family")
    if not family:
        return True
    return window.get("pool_family") == family


def enrich_account(
    account: dict[str, Any],
    *,
    collected_at: str | None = None,
    now: datetime | None = None,
    notes: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add self-describing fields to one account dict (windows enriched too)."""
    now = now or utcnow()
    provider = str(account.get("provider") or "")
    windows = [w for w in (account.get("windows") or []) if isinstance(w, dict)]
    for window in windows:
        enrich_window(window, provider=provider)
    account["windows"] = windows

    for note in notes or []:
        for window in windows:
            if _note_matches(note, provider, window):
                _apply_note_to_window(window, note)

    states = [str(w.get("state")) for w in windows]
    if any(s == "exhausted" for s in states):
        usable_now: bool | None = False
    elif windows and any(s != "unknown" for s in states):
        usable_now = True
    else:
        # no windows at all, or every window unknown: no evidence of headroom
        usable_now = False
    account["usable_now"] = usable_now

    numbered = [w for w in windows if w.get("headroom_percent") is not None]
    if numbered:
        binding = min(numbered, key=lambda w: float(w["headroom_percent"]))
        account["binding_window"] = str(binding.get("label") or "")
        account["binding_headroom_percent"] = float(binding["headroom_percent"])
    else:
        account["binding_window"] = None
        account["binding_headroom_percent"] = None

    reset_times = [
        parse_dt(w.get("resets_at")) for w in windows if w.get("state") == "exhausted" and w.get("resets_at")
    ]
    reset_times = [t for t in reset_times if t is not None]
    if reset_times:
        account["available_at"] = min(reset_times).isoformat()
    else:
        account["available_at"] = None

    acct_collected = account.get("collected_at") or collected_at
    if acct_collected:
        account["collected_at"] = acct_collected
        dt = parse_dt(acct_collected)
        account["age_seconds"] = round((now - dt).total_seconds(), 1) if dt else None
    else:
        account["age_seconds"] = None
    return account


def enrich_snapshot(
    snap: dict[str, Any],
    *,
    now: datetime | None = None,
    notes: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Enrich accounts in place and attach summary_lines / semantics."""
    now = now or utcnow()
    accounts = [a for a in (snap.get("accounts") or []) if isinstance(a, dict)]
    collected = snap.get("collected_at")
    for account in accounts:
        enrich_account(account, collected_at=collected, now=now, notes=notes)
    snap["accounts"] = accounts
    snap["summary_lines"] = summary_lines(pool_entries(snap))
    snap["semantics"] = dict(SEMANTICS)
    if notes:
        snap["agent_notes"] = [
            {
                "provider": n.get("provider"),
                "pool_family": n.get("pool_family"),
                "resets_at": n.get("resets_at"),
                "reason": n.get("reason"),
                "source": "agent-reported",
            }
            for n in notes
        ]
    return snap


def freshness(
    collected_at: str | None,
    *,
    now: datetime | None = None,
    threshold_seconds: float = FRESH_THRESHOLD_SECONDS_DEFAULT,
) -> dict[str, Any]:
    """Read-time age/fresh pair for a snapshot's collected_at."""
    now = now or utcnow()
    dt = parse_dt(collected_at)
    if dt is None:
        return {"age_seconds": None, "fresh": False}
    age = max(0.0, (now - dt).total_seconds())
    return {"age_seconds": round(age, 1), "fresh": age <= threshold_seconds}


def pool_entries(snap: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One entry per (account, pool_family) — the unit an orchestrator routes on."""
    pools: list[dict[str, Any]] = []
    for account in snap.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        provider = str(account.get("provider") or "")
        by_family: dict[str, list[dict[str, Any]]] = {}
        hints: dict[str, str] = {}
        for window in account.get("windows") or []:
            if not isinstance(window, dict):
                continue
            family = str(window.get("pool_family") or "default")
            by_family.setdefault(family, []).append(window)
            if window.get("models_hint"):
                hints[family] = str(window["models_hint"])
        for family, windows in by_family.items():
            states = [str(w.get("state") or "unknown") for w in windows]
            numbered = [w for w in windows if w.get("headroom_percent") is not None]
            if any(s == "exhausted" for s in states):
                usable: bool | None = False
            elif any(s != "unknown" for s in states):
                usable = True
            else:
                usable = False
            resets = [
                parse_dt(w.get("resets_at")) for w in windows if w.get("state") == "exhausted" and w.get("resets_at")
            ]
            resets = [t for t in resets if t is not None]
            binding = min(numbered, key=lambda w: float(w["headroom_percent"])) if numbered else None
            pools.append(
                {
                    "provider": provider,
                    "account": account.get("account"),
                    "pool_family": family if family != "default" or _vendor_splits(provider) else None,
                    "cli_binary": account.get("cli_binary"),
                    "usable_now": usable,
                    "binding_window": str(binding.get("label") or "") if binding else None,
                    "headroom_percent": float(binding["headroom_percent"]) if binding else None,
                    "available_at": min(resets).isoformat() if resets else None,
                    "models_hint": hints.get(family),
                    "age_seconds": account.get("age_seconds"),
                    "windows": windows,
                }
            )
    return pools


def _vendor_splits(provider: str) -> bool:
    return provider in _FAMILY_RULES


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "?"
    if 0.0 < value < 1.0:
        return "<1"
    return f"{value:.0f}"


def _reset_fragment(resets_at: Any) -> str:
    dt = parse_dt(resets_at)
    if dt is None:
        return ""
    # Local 12-hour time, the same form the watch board uses.
    from aiuse.report import format_clock

    return f", resets {format_clock(dt, date=dt - utcnow() > timedelta(hours=24))}"


def summary_line(pool: Mapping[str, Any]) -> str:
    """`codex 5-hour: EXHAUSTED (100% used / 0% left, resets 15:42); weekly … -> NOT usable now`"""
    provider = str(pool.get("provider") or "?")
    family = pool.get("pool_family")
    lead = f"{provider} {family}" if family else provider
    # The command you actually type, when it is not the provider id: `cursor`
    # opens the editor (the agent is cursor-agent), antigravity is `agy`.
    binary = pool.get("cli_binary")
    if binary and str(binary) != provider:
        lead += f" [{binary}]"
    segments: list[str] = []
    for window in pool.get("windows") or []:
        label = str(window.get("label") or "?")
        state = str(window.get("state") or "unknown")
        used = window.get("used_percent")
        left = window.get("headroom_percent")
        if window.get("headroom_percent") is None and window.get("used_percent") is None:
            segments.append(f"{label}: UNKNOWN (no data)")
            continue
        if used is None and left is not None:
            used = max(0.0, 100.0 - float(left))
        if left is None and used is not None:
            left = max(0.0, 100.0 - float(used))
        word = {"exhausted": "EXHAUSTED", "tight": "TIGHT", "ok": "ok", "unknown": "UNKNOWN"}.get(state, state)
        seg = f"{label}: {word} ({_fmt_pct(used)}% used / {_fmt_pct(left)}% left{_reset_fragment(window.get('resets_at'))})"
        if window.get("state_source") == "agent-reported":
            seg += " [agent-reported]"
        segments.append(seg)
    line = f"{lead}: " + "; ".join(segments)
    if pool.get("usable_now") is False:
        line += " -> NOT usable now"
    elif all(str(w.get("state")) == "unknown" for w in pool.get("windows") or []):
        line += " -> unknown"
    return line


def summary_lines(pools: Iterable[Mapping[str, Any]]) -> list[str]:
    return [summary_line(p) for p in pools]


def available_pools(snap: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Only usable_now pools, most headroom first — what `--available` prints."""
    pools = [p for p in pool_entries(snap) if p.get("usable_now")]
    pools.sort(key=lambda p: (-(p.get("headroom_percent") or 0.0), str(p.get("provider"))))
    return pools
