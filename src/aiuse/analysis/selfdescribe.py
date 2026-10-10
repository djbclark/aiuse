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

from aiuse.models import canonical_provider, claude_model_scope, parse_dt, utcnow

SCHEMA_VERSION = "1.1"

# state thresholds on remaining_percent
EXHAUSTED_BELOW = 1.0  # remaining <= 1 -> exhausted
TIGHT_BELOW = 15.0  # remaining < 15 -> tight

# age threshold for the read-time `fresh` flag (config: analysis.fresh_threshold_seconds)
FRESH_THRESHOLD_SECONDS_DEFAULT = 1500.0  # 25 min

SEMANTICS: dict[str, str] = {
    "used_percent": "share CONSUMED; 100 means exhausted, 0 means untouched",
    "remaining_percent": "share still available; decide from this",
    "headroom_percent": "window: alias of remaining_percent; routing entry: least applicable headroom, with Fable's cap scaled to shared-weekly units",
    "state": "exhausted (<=1% left) | tight (<15% left) | ok | unknown (no data); never compute it yourself",
    "usable_now": "false if any applicable window is exhausted or no shared quota has data; Claude model caps restrict that model only, but shared limits restrict every Claude model",
    "binding_window": "label of the window with the least remaining_percent — the one that stops you first",
    "available_at": "earliest resets_at among exhausted windows; null when nothing is exhausted or no reset time is known",
    "pool_family": "routing family, not necessarily independent quota: antigravity gemini vs claude_gpt are independent; Claude default is shared quota and fable is a model sublimit within it",
    "quota_scope": "shared or model_sublimit; a model_sublimit is not additional quota and cannot exhaust the whole account",
    "shared_pool_family": "parent routing family whose windows also constrain this model sublimit",
    "max_share_of_parent_percent": "model cap as a share of the shared weekly budget; Fable <=50%, not an extra 50%",
    "headroom_basis": "shared_weekly for Fable routing entries: min(shared headroom, 0.5 * Fable cap headroom); window percentages remain relative to their own limits",
    "age_seconds": "seconds since collected_at, computed at read time; stale data can hide a fresh exhaustion",
    "fresh": "age_seconds <= fresh threshold (default 1500s); when false, re-collect before trusting ok states",
    "summary_lines": "one human line per pool; always shows used AND left, never a bare percentage",
    "agent_notes": "exhaustion overrides reported by agents (source: agent-reported); expire at their reset time, after which a live collector reading wins",
    "client_limits": "per-client rate limits the quota windows cannot show, read passively (e.g. agy CLI 429s from its own logs); state limited means that client is failing now while another client (agy ACP) may still work; usable_now stays quota-based",
    "excluded": "--available only: usable pools the operator ruled out (analysis.excluded_pools), with the reason; never route to them",
    "disabled_services": "operator-disabled providers (config [disabled_services]) with the reason; no rows are collected for them — do not route to or spend them until the operator removes the entry",
    "context_usage": "ACP context-window fill and this turn's token counts. used/size is the session context, not a 5h or weekly plan. turn_quota is per-turn tokens. Plan windows stay on windows[]",
}

# (substring, family, models_hint) matched against the lowercased window label,
# first hit wins. Claude families are routing views of one shared pool.
_FAMILY_RULES: dict[str, list[tuple[str, str, str]]] = {
    "antigravity": [
        ("claude/gpt", "claude_gpt", "agy models — claude-* / gpt-* models draw this pool"),
        ("gemini", "gemini", "agy models — gemini-* models draw this pool"),
    ],
    "claude": [
        ("5-hour", "default", "Claude models without a reported model cap; quota is shared with capped models"),
        ("weekly", "default", "Claude models without a reported model cap; quota is shared with capped models"),
    ],
    "cursor": [
        ("grok", "grok", "Cursor grok model slot"),
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
    model = claude_model_scope(label) if provider == "claude" else None
    if model is not None:
        return model.casefold().replace(" ", "_"), f"Claude {model}; also draws shared Claude quota"
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
    for key in ("quota_scope", "shared_pool_family", "max_share_of_parent_percent"):
        window.pop(key, None)
    if provider == "claude":
        model = claude_model_scope(str(window.get("label") or ""))
        window["quota_scope"] = "model_sublimit" if model else "shared"
        if model:
            window["shared_pool_family"] = "default"
            if model.casefold() == "fable":
                window["max_share_of_parent_percent"] = 50.0
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

    applicable = [w for w in windows if w.get("quota_scope") != "model_sublimit"]
    states = [str(w.get("state")) for w in applicable]
    if any(s == "exhausted" for s in states):
        usable_now: bool | None = False
    elif applicable and any(s != "unknown" for s in states):
        usable_now = True
    else:
        # no windows at all, or every window unknown: no evidence of headroom
        usable_now = False
    account["usable_now"] = usable_now

    numbered = [w for w in applicable if w.get("headroom_percent") is not None]
    if numbered:
        binding = min(numbered, key=lambda w: float(w["headroom_percent"]))
        account["binding_window"] = str(binding.get("label") or "")
        account["binding_headroom_percent"] = float(binding["headroom_percent"])
    else:
        account["binding_window"] = None
        account["binding_headroom_percent"] = None

    reset_times = [
        parse_dt(w.get("resets_at")) for w in applicable if w.get("state") == "exhausted" and w.get("resets_at")
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
    client_limits: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Enrich accounts in place and attach summary_lines / semantics.

    ``client_limits`` (provider -> entries, from aiuse.client_limits) replaces
    each matching account's ``client_limits``; read-time evidence wins over
    whatever a cached snapshot carried.
    """
    now = now or utcnow()
    accounts = [a for a in (snap.get("accounts") or []) if isinstance(a, dict)]
    collected = snap.get("collected_at")
    for account in accounts:
        enrich_account(account, collected_at=collected, now=now, notes=notes)
        if client_limits is not None:
            limits = client_limits.get(str(account.get("provider") or ""))
            if limits:
                account["client_limits"] = [dict(entry) for entry in limits]
            else:
                account.pop("client_limits", None)
    snap["accounts"] = accounts
    snap["summary_lines"] = summary_lines(pool_entries(snap))
    snap["semantics"] = dict(SEMANTICS)
    # Snapshots written before this field existed have no key; every reader
    # can rely on it being present (empty = nothing disabled).
    snap.setdefault("disabled_services", {})
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
            model_sublimit = provider == "claude" and family != "default"
            family_has_data = any(str(w.get("state") or "unknown") != "unknown" for w in windows)
            shared = by_family.get("default", []) if model_sublimit else windows
            if model_sublimit:
                windows = shared + windows
            states = [str(w.get("state") or "unknown") for w in windows]
            numbered = [w for w in windows if w.get("headroom_percent") is not None]
            if any(s == "exhausted" for s in states):
                usable: bool | None = False
            elif family_has_data and any(str(w.get("state") or "unknown") != "unknown" for w in shared):
                usable = True
            else:
                usable = False
            resets = [
                parse_dt(w.get("resets_at")) for w in windows if w.get("state") == "exhausted" and w.get("resets_at")
            ]
            resets = [t for t in resets if t is not None]

            def effective_headroom(window: Mapping[str, Any]) -> float:
                share = float(window.get("max_share_of_parent_percent", 100.0)) / 100.0
                return float(window["headroom_percent"]) * share

            binding = min(numbered, key=effective_headroom) if numbered else None
            pools.append(
                {
                    "provider": provider,
                    "account": account.get("account"),
                    "pool_family": family if family != "default" or _vendor_splits(provider) else None,
                    "cli_binary": account.get("cli_binary"),
                    "usable_now": usable,
                    "binding_window": str(binding.get("label") or "") if binding else None,
                    "headroom_percent": effective_headroom(binding) if binding else None,
                    **(
                        {"shared_pool_family": "default", "headroom_basis": "shared_weekly"}
                        if model_sublimit and family == "fable"
                        else {"shared_pool_family": "default"}
                        if model_sublimit
                        else {}
                    ),
                    "available_at": min(resets).isoformat() if resets else None,
                    "models_hint": hints.get(family),
                    "age_seconds": account.get("age_seconds"),
                    **({"client_limits": account["client_limits"]} if account.get("client_limits") else {}),
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
        if window.get("quota_scope") == "model_sublimit":
            cap = window.get("max_share_of_parent_percent")
            seg += f" [cap <={cap:g}% of shared weekly]" if cap is not None else " [cap within shared weekly]"
        if window.get("state_source") == "agent-reported":
            seg += " [agent-reported]"
        segments.append(seg)
    line = f"{lead}: " + "; ".join(segments)
    for limit in pool.get("client_limits") or []:
        if isinstance(limit, dict) and limit.get("state") == "limited":
            client = limit.get("cli_binary") or limit.get("client") or "client"
            line += (
                f" [{client} {limit.get('client') or 'client'} rate-limited: 429 x{limit.get('failed_attempts', '?')}"
            )
            when = parse_dt(limit.get("last_limited_at"))
            if when is not None:
                line += f", last {max(0, int((utcnow() - when).total_seconds() // 60))}m ago"
            line += "; ACP may still work]"
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


def apply_exclusions(
    pools: Iterable[dict[str, Any]], exclusions: Mapping[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split routing entries into (kept, excluded) by ``analysis.excluded_pools``.

    Keys are ``"provider"`` (every pool of that vendor) or
    ``"provider/pool_family"`` (``default`` names the unsplit pool); values are
    the operator's reason or ``true``. An exclusion is a routing decision, not
    a quota fact: it drops the pool from the shortlist and leaves its numbers
    alone, so the full report still shows the windows.
    """
    rules: list[tuple[str, str | None, str]] = []
    for key, value in (exclusions or {}).items():
        provider, _, family = str(key).partition("/")
        provider = canonical_provider(provider.strip())
        if not provider:
            continue
        reason = value.strip() if isinstance(value, str) and value.strip() else "excluded by operator"
        rules.append((provider, family.strip().casefold() or None, reason))
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for pool in pools:
        pool_provider = canonical_provider(str(pool.get("provider") or ""))
        pool_family = str(pool.get("pool_family") or "default").casefold()
        match = next(
            (r for p, f, r in rules if p == pool_provider and (f is None or f == pool_family)),
            None,
        )
        if match is None:
            kept.append(pool)
        else:
            excluded.append(
                {
                    "provider": pool.get("provider"),
                    "account": pool.get("account"),
                    "pool_family": pool.get("pool_family"),
                    "cli_binary": pool.get("cli_binary"),
                    "headroom_percent": pool.get("headroom_percent"),
                    "reason": match,
                }
            )
    return kept, excluded
