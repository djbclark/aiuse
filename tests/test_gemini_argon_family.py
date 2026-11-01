"""Gemini 4 Argon is its own pool family (schema 1.5), not a window of Flash `gemini`.

2026-10-31: a real `agy --model gemini-4-argon` call moved only the Argon
weekly window; Gemini weekly (Flash) did not move.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from aiuse.models import utcnow

ARGON_ID = "antigravity-quota-summary-gemini-argon-wtu-weekly"


def _window(label: str, used: float, minutes: int, *, window_id: str | None = None) -> dict:
    now = utcnow()
    return {
        "label": label,
        "used_percent": used,
        "remaining_percent": 100.0 - used,
        "resets_at": (now + timedelta(days=2)).isoformat(),
        "window_minutes": minutes,
        "window_id": window_id,
    }


def _snapshot(*, flash_weekly: float = 10.0, argon: float = 20.0, argon_label: str = "Gemini 4 Argon weekly") -> dict:
    return {
        "collected_at": utcnow().isoformat(),
        "accounts": [
            {
                "provider": "antigravity",
                "account": None,
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "cli_binary": "agy",
                "windows": [
                    _window("Gemini 5-hour", 3.0, 300, window_id="antigravity-quota-summary-gemini-5h"),
                    _window("Gemini weekly", flash_weekly, 10080, window_id="antigravity-quota-summary-gemini-weekly"),
                    _window(argon_label, argon, 10080, window_id=ARGON_ID),
                    _window("Claude/GPT weekly", 30.0, 10080, window_id="antigravity-quota-summary-3p-weekly"),
                ],
            }
        ],
    }


def _families(snap: dict) -> dict[str, dict]:
    from aiuse.analysis.selfdescribe import pool_entries

    return {p["pool_family"]: p for p in pool_entries(snap)}


def _note(family: str | None) -> dict:
    return {
        "provider": "antigravity",
        "pool_family": family,
        "resets_at": (utcnow() + timedelta(hours=4)).isoformat(),
        "reason": "QUOTA_EXHAUSTED",
        "source": "agent-reported",
    }


@pytest.mark.parametrize(
    ("label", "window_id", "family"),
    [
        ("Gemini 4 Argon weekly", ARGON_ID, "gemini_argon"),
        # CodexBar has titled the Argon window plain "Gemini weekly": the id wins.
        ("Gemini weekly", ARGON_ID, "gemini_argon"),
        # No id: the model name in the label is enough.
        ("Gemini 4 Argon weekly", None, "gemini_argon"),
        ("Gemini 5-hour", "antigravity-quota-summary-gemini-5h", "gemini"),
        ("Gemini weekly", "antigravity-quota-summary-gemini-weekly", "gemini"),
        ("Gemini weekly", None, "gemini"),
        ("Gemini Flash weekly", None, "gemini"),
        # A future frontier model is its own family, never folded into Argon or Flash.
        ("Gemini 4 Carbon weekly", "antigravity-quota-summary-gemini-carbon-wtu-weekly", "gemini_carbon"),
        ("Gemini 4 Carbon weekly", None, "gemini_carbon"),
        ("Claude/GPT weekly", "antigravity-quota-summary-3p-weekly", "claude_gpt"),
    ],
)
def test_antigravity_gemini_family_rule(label, window_id, family):
    from aiuse.analysis.pace import independent_pool_key
    from aiuse.analysis.selfdescribe import pool_family_id

    assert pool_family_id("antigravity", label, window_id) == family
    assert independent_pool_key(label, window_id=window_id, provider="antigravity") == family


def test_argon_window_gets_family_and_hint():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    snap = enrich_snapshot(_snapshot())
    windows = {w["label"]: w for w in snap["accounts"][0]["windows"]}
    assert windows["Gemini 4 Argon weekly"]["pool_family"] == "gemini_argon"
    assert windows["Gemini 4 Argon weekly"]["models_hint"] == "gemini-4-argon* draws this pool"
    assert windows["Gemini weekly"]["pool_family"] == "gemini"
    assert windows["Gemini 5-hour"]["pool_family"] == "gemini"


def test_argon_and_flash_are_separate_available_rows():
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    snap = enrich_snapshot(_snapshot())
    rows = {p["pool_family"]: p for p in available_pools(snap)}
    assert {"gemini", "gemini_argon", "claude_gpt"} <= set(rows)
    assert {p["provider"] for p in rows.values()} == {"antigravity"}
    assert rows["gemini_argon"]["binding_window"] == "Gemini 4 Argon weekly"
    assert rows["gemini_argon"]["headroom_percent"] == pytest.approx(80.0)
    assert rows["gemini_argon"]["models_hint"] == "gemini-4-argon* draws this pool"
    assert rows["gemini"]["binding_window"] == "Gemini weekly"
    assert rows["gemini"]["headroom_percent"] == pytest.approx(90.0)


def test_exhausted_argon_leaves_flash_usable_and_listed():
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    snap = enrich_snapshot(_snapshot(argon=100.0))
    pools = _families(snap)
    assert pools["gemini_argon"]["usable_now"] is False
    assert pools["gemini"]["usable_now"] is True
    listed = {p["pool_family"] for p in available_pools(snap)}
    assert "gemini_argon" not in listed
    assert "gemini" in listed


def test_exhausted_flash_weekly_leaves_argon_listed():
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    snap = enrich_snapshot(_snapshot(flash_weekly=100.0))
    pools = _families(snap)
    assert pools["gemini"]["usable_now"] is False
    assert pools["gemini_argon"]["usable_now"] is True
    listed = {p["pool_family"] for p in available_pools(snap)}
    assert "gemini_argon" in listed
    assert "gemini" not in listed


def test_live_shape_flash_tight_argon_half():
    """Gemini weekly ~4% left, Argon ~50% left (2026-10-31 live snapshot shape)."""
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    snap = enrich_snapshot(_snapshot(flash_weekly=96.0, argon=49.0, argon_label="Gemini weekly"))
    rows = {p["pool_family"]: p for p in available_pools(snap)}
    assert rows["gemini_argon"]["headroom_percent"] == pytest.approx(51.0)
    assert rows["gemini"]["headroom_percent"] == pytest.approx(4.0)


def test_argon_exhaustion_note_does_not_mark_flash():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    snap = enrich_snapshot(_snapshot(), notes=[_note("gemini_argon")])
    pools = _families(snap)
    assert pools["gemini_argon"]["usable_now"] is False
    assert pools["gemini"]["usable_now"] is True
    windows = {w["label"]: w for w in snap["accounts"][0]["windows"]}
    assert windows["Gemini 4 Argon weekly"]["state"] == "exhausted"
    assert windows["Gemini weekly"]["state"] != "exhausted"
    assert windows["Gemini 5-hour"]["state"] != "exhausted"


def test_flash_exhaustion_note_does_not_mark_argon():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    snap = enrich_snapshot(_snapshot(), notes=[_note("gemini")])
    pools = _families(snap)
    assert pools["gemini"]["usable_now"] is False
    assert pools["gemini_argon"]["usable_now"] is True


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("gemini-4-argon", "gemini_argon"),
        ("gemini-4-argon-preview", "gemini_argon"),
        ("Gemini-4-Argon", "gemini_argon"),
        ("gemini-3-flash", "gemini"),
        ("gemini-3.1-pro", "gemini"),
        ("claude-opus-5-5", "claude_gpt"),
        ("gpt-6-astra", "claude_gpt"),
        (None, None),
    ],
)
def test_model_to_family_mapping(model, family):
    from aiuse.quota_refusals import pool_family_for_acp_model

    assert pool_family_for_acp_model("antigravity", model) == family
    assert pool_family_for_acp_model("codex", model) is None


def test_reenriching_a_stored_snapshot_replaces_the_old_gemini_hint():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    stored = _snapshot()
    for window in stored["accounts"][0]["windows"]:
        window["pool_family"] = "gemini" if "Gemini" in window["label"] else "claude_gpt"
        window["models_hint"] = "agy models — gemini-* models draw this pool"
    windows = {w["label"]: w for w in enrich_snapshot(stored)["accounts"][0]["windows"]}
    assert windows["Gemini 4 Argon weekly"]["pool_family"] == "gemini_argon"
    assert windows["Gemini 4 Argon weekly"]["models_hint"] == "gemini-4-argon* draws this pool"
    assert windows["Gemini weekly"]["models_hint"] == "agy models — gemini-* models draw this pool"
