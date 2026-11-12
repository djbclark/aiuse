"""Paseo agent errors mark pools exhausted (or throttled, never exhausted).

Evidence 2026-11-12: Paseo agents saw "Individual quota reached. Please
upgrade your subscription to increase your limits. Resets in 141h24m34s."
(antigravity Gemini Argon) and "... Resets in 42h10m16s." (Claude/GPT) while
aiuse still reported 36%/31% left; a Pi z.ai agent saw a short 429
(``code 1302``) that must never flip a pool.
"""

from __future__ import annotations

import json
from datetime import timedelta

import aiuse.paseo_refusals as paseo_refusals
from aiuse import agent_notes, cli
from aiuse.agent_notes import load_active_notes
from aiuse.client_limits import load_client_limits
from aiuse.models import parse_dt, utcnow
from aiuse.paseo_refusals import (
    parse_resets_in_horizon,
    scan_paseo_quota_refusals,
    scan_paseo_throttle,
)


def _write_agent(slug: str, agent_id: str, doc: dict) -> None:
    # Through the module attribute: the conftest autouse fixture patches
    # paseo_refusals.paseo_agents_dir, and a from-import would bypass it.
    directory = paseo_refusals.paseo_agents_dir() / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{agent_id}.json").write_text(json.dumps(doc))


def _agent(
    provider: str,
    model: str,
    error: str,
    *,
    now,
    status: str = "error",
    age: timedelta = timedelta(minutes=2),
) -> dict:
    return {
        "provider": provider,
        "lastStatus": status,
        "lastError": error,
        "updatedAt": (now - age).isoformat().replace("+00:00", "Z"),
        "runtimeInfo": {"provider": provider, "model": model},
    }


AGY_GPT = "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 42h10m16s."
AGY_ARGON = "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 141h24m34s."
ZAI_429 = '429: {"code":"1302","message":"Rate limit reached for requests"}'


# ── reset parsing ───────────────────────────────────────────────────────────


def test_resets_in_horizon_parses_hours_minutes_seconds():
    assert parse_resets_in_horizon(AGY_ARGON) == timedelta(hours=141, minutes=24, seconds=34)
    assert parse_resets_in_horizon(AGY_GPT) == timedelta(hours=42, minutes=10, seconds=16)
    assert parse_resets_in_horizon("no reset here") is None


# ── exhausted refusals ──────────────────────────────────────────────────────


def test_agy_claude_gpt_refusal_marks_claude_gpt_pool(tmp_path):
    now = utcnow()
    _write_agent("proj", "aaaa-1", _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now))
    refusals = scan_paseo_quota_refusals(now=now)
    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal["provider"] == "antigravity"
    assert refusal["pool_family"] == "claude_gpt"
    assert parse_dt(refusal["resets_at"]) is not None
    assert "paseo agent error" in refusal["reason"]


def test_reset_is_anchored_to_the_error_time_not_the_scan(tmp_path):
    now = utcnow()
    doc = _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now)
    event = parse_dt(doc["updatedAt"])
    assert event is not None
    _write_agent("proj", "aaaa-2", doc)
    refusal = scan_paseo_quota_refusals(now=now)[0]
    assert parse_dt(refusal["resets_at"]) == event + timedelta(hours=42, minutes=10, seconds=16)


def test_agy_argon_refusal_marks_gemini_argon_pool():
    now = utcnow()
    _write_agent("proj", "bbbb-1", _agent("antigravity", "gemini-4-argon-high", AGY_ARGON, now=now))
    refusals = scan_paseo_quota_refusals(now=now)
    assert [(r["provider"], r["pool_family"]) for r in refusals] == [("antigravity", "gemini_argon")]


def test_two_agents_same_pool_deduplicate_newest_wins():
    now = utcnow()
    _write_agent(
        "proj", "cccc-1", _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now, age=timedelta(minutes=30))
    )
    _write_agent("proj", "cccc-2", _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now))
    assert len(scan_paseo_quota_refusals(now=now)) == 1


def test_short_reset_is_never_an_exhaustion():
    now = utcnow()
    _write_agent(
        "proj",
        "dddd-1",
        _agent("antigravity", "claude-sonnet-5-5-medium", "Individual quota reached. Resets in 2h30m.", now=now),
    )
    assert scan_paseo_quota_refusals(now=now) == []


def test_already_reset_refusal_is_ignored():
    now = utcnow()
    _write_agent(
        "proj",
        "eeee-1",
        _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now, age=timedelta(days=3)),
    )
    assert scan_paseo_quota_refusals(now=now) == []


def test_non_error_and_malformed_files_are_ignored():
    now = utcnow()
    _write_agent("proj", "ffff-1", _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now, status="idle"))
    (paseo_refusals.paseo_agents_dir() / "proj").mkdir(exist_ok=True)
    (paseo_refusals.paseo_agents_dir() / "proj" / "ffff-2.json").write_text("{not json")
    assert scan_paseo_quota_refusals(now=now) == []


def test_missing_agents_dir_is_empty(tmp_path):
    assert scan_paseo_quota_refusals(agents_dir=tmp_path / "absent", now=utcnow()) == []


# ── metered OpenAI platform (pi + openai/*) ────────────────────────────────


SPEND_LIMIT = (
    "[System Error] Your organization has reached its configured enforced spend limit. "
    "Update your limit at https://platform.openai.com/settings/organization/limits."
)


def test_pi_openai_spend_limit_marks_openai_api_pool_never_codex():
    now = utcnow()
    _write_agent("proj", "llll-1", _agent("pi", "openai/gpt-6.1-sol", SPEND_LIMIT, now=now))
    refusals = scan_paseo_quota_refusals(now=now)
    assert [(r["provider"], r["pool_family"]) for r in refusals] == [("pi", "openai_api")]
    assert not any(r["provider"] == "codex" for r in refusals)


def test_spend_limit_without_reset_gets_a_bounded_fallback_and_says_so():
    now = utcnow()
    _write_agent("proj", "mmmm-1", _agent("pi", "openai/gpt-6.1-sol", SPEND_LIMIT, now=now))
    refusal = scan_paseo_quota_refusals(now=now)[0]
    resets = parse_dt(refusal["resets_at"])
    assert resets is not None
    delta = resets - now
    assert timedelta(hours=23, minutes=55) <= delta <= timedelta(hours=24, minutes=5)
    assert "operator" in refusal["reason"]


def test_spend_limit_is_not_a_throttle_and_models_map_explicitly():
    now = utcnow()
    _write_agent("proj", "nnnn-1", _agent("pi", "openai/gpt-6.1-sol", SPEND_LIMIT, now=now))
    assert scan_paseo_throttle(now=now) is None
    from aiuse.paseo_refusals import pool_family_for_paseo_model

    assert pool_family_for_paseo_model("pi", "openai/gpt-6.1-sol") == "openai_api"
    assert pool_family_for_paseo_model("pi", "zai/glm-5.3-flash") is None
    assert pool_family_for_paseo_model("antigravity", "claude-sonnet-5-5-medium") == "claude_gpt"
    assert pool_family_for_paseo_model("antigravity", "gemini-4-argon-high") == "gemini_argon"


# ── short rate limit: throttled, never exhausted ────────────────────────────


def test_zai_429_is_throttled_and_never_exhausted():
    now = utcnow()
    _write_agent("proj", "gggg-1", _agent("pi", "zai/glm-5.3-flash", ZAI_429, now=now))
    assert scan_paseo_quota_refusals(now=now) == []
    entry = scan_paseo_throttle(now=now)
    assert entry is not None
    assert entry["client"] == "paseo-agent"
    assert entry["state"] == "limited"
    assert entry["provider_hint"] == "pi"
    event = parse_dt(entry["last_limited_at"])
    assert event is not None
    assert parse_dt(entry["cooldown_until"]) == event + timedelta(minutes=10)


def test_throttle_ages_out_of_the_cooldown():
    now = utcnow()
    _write_agent(
        "proj",
        "hhhh-1",
        _agent("pi", "zai/glm-5.3-flash", ZAI_429, now=now, age=timedelta(minutes=45)),
    )
    assert scan_paseo_throttle(now=now) is None
    assert scan_paseo_quota_refusals(now=now) == []


def test_quota_refusal_is_not_mistaken_for_a_throttle():
    now = utcnow()
    _write_agent("proj", "iiii-1", _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now))
    assert scan_paseo_throttle(now=now) is None


# ── integration: notes + client_limits ──────────────────────────────────────


def test_load_active_notes_persists_the_paseo_refusal():
    now = utcnow()
    _write_agent("proj", "jjjj-1", _agent("antigravity", "gemini-4-argon-high", AGY_ARGON, now=now))
    notes = load_active_notes(now=now)
    argon = [n for n in notes if n.get("provider") == "antigravity" and n.get("pool_family") == "gemini_argon"]
    assert len(argon) == 1
    assert argon[0]["source"] == "agent-reported"
    assert "paseo agent error" in argon[0]["reason"]
    assert (agent_notes.notes_dir() / "antigravity-gemini_argon.json").is_file()


def test_load_client_limits_surfaces_the_paseo_throttle_on_pi():
    now = utcnow()
    _write_agent("proj", "kkkk-1", _agent("pi", "zai/glm-5.3-flash", ZAI_429, now=now))
    limits = load_client_limits({})
    assert [entry["client"] for entry in limits.get("pi", [])] == ["paseo-agent"]
    # quota-based fields are untouched by construction: no usable_now key on the entry
    assert all("usable_now" not in entry for entry in limits["pi"])


def test_load_client_limits_without_paseo_errors_has_no_pi_entry():
    limits = load_client_limits({})
    assert "pi" not in limits


# ── the 2026-11-12 repro: refusal beats an optimistic reading ───────────────


def _optimistic_cache() -> dict:
    """What aiuse showed today: agy pools with headroom, hours-old cache."""
    now = utcnow()
    return {
        "collected_at": (now - timedelta(minutes=14)).isoformat(),
        "accounts": [
            {
                "provider": "antigravity",
                "account": None,
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "cli_binary": "agy",
                "windows": [
                    {
                        "label": "Claude/GPT weekly",
                        "used_percent": 64.0,
                        "remaining_percent": 36.0,
                        "resets_at": (now + timedelta(days=6)).isoformat(),
                        "window_minutes": 10080,
                    },
                    {
                        "label": "Gemini weekly",
                        "used_percent": 20.0,
                        "remaining_percent": 80.0,
                        "resets_at": (now + timedelta(days=3)).isoformat(),
                        "window_minutes": 10080,
                    },
                ],
            }
        ],
    }


def _available_families(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("")
    assert cli.main(["--available", "--json", "-q", "--config", str(config)]) == 0
    payload = json.loads(capsys.readouterr().out)
    return {p["pool_family"] or "default": p for p in payload["available"] if p["provider"] == "antigravity"}


def test_refusal_beats_optimistic_reading_until_reset(tmp_path, capsys):
    from aiuse.analysis import history

    snap = _optimistic_cache()
    cache = history.snapshot_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "latest.json").write_text(json.dumps(snap))

    # Before the refusal: the optimistic reading lists both pools.
    families = _available_families(tmp_path, capsys)
    assert set(families) == {"claude_gpt", "gemini"}
    assert families["claude_gpt"]["headroom_percent"] > 30

    # A paseo agent hits Individual quota reached on the Claude/GPT pool.
    now = utcnow()
    _write_agent("proj", "rrrr-1", _agent("antigravity", "claude-sonnet-5-5-medium", AGY_GPT, now=now))

    # After: claude_gpt is out of the routing shortlist, gemini stays, and the
    # persisted note is what holds it out (visible to every later read).
    families = _available_families(tmp_path, capsys)
    assert set(families) == {"gemini"}
    assert (agent_notes.notes_dir() / "antigravity-claude_gpt.json").is_file()


def test_note_is_pruned_when_a_fresh_probe_succeeds():
    now = utcnow()
    evidence_file = paseo_refusals.paseo_agents_dir() / "proj" / "ssss-1.json"
    doc = _agent("pi", "openai/gpt-6.1-sol", SPEND_LIMIT, now=now)
    _write_agent("proj", "ssss-1", doc)
    notes = load_active_notes(now=now)
    assert any(n.get("pool_family") == "openai_api" for n in notes)
    assert (agent_notes.notes_dir() / "pi-openai_api.json").is_file()

    # The operator raises the limit; the agent's next run succeeds: the daemon
    # state file no longer carries the refusal.
    doc["lastStatus"] = "idle"
    doc["lastError"] = None
    doc["updatedAt"] = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    evidence_file.write_text(json.dumps(doc))

    # No reset in the refusal text — only the fresh successful probe clears it.
    assert paseo_refusals.paseo_note_evidence_gone(str(evidence_file))
    notes = load_active_notes(now=now + timedelta(minutes=6))
    assert not any(n.get("pool_family") == "openai_api" for n in notes)
    assert not (agent_notes.notes_dir() / "pi-openai_api.json").exists()
