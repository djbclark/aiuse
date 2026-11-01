"""Gemini 4 Argon is its own pool family (schema 1.5), not a window of Flash `gemini`.

2026-10-31: a real `agy --model gemini-4-argon` call moved only the Argon
weekly window; Gemini weekly (Flash) did not move.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        gemini = "Gemini" in window["label"]
        window["pool_family"] = "gemini" if gemini else "claude_gpt"
        window["models_hint"] = (
            "agy models — gemini-* models draw this pool"
            if gemini
            else "agy models — claude-* / gpt-* models draw this pool"
        )
    windows = {w["label"]: w for w in enrich_snapshot(stored)["accounts"][0]["windows"]}
    assert windows["Gemini 4 Argon weekly"]["pool_family"] == "gemini_argon"
    assert windows["Gemini 4 Argon weekly"]["models_hint"] == "gemini-4-argon* draws this pool"
    assert (
        windows["Gemini weekly"]["models_hint"]
        == "agy models — gemini-* models (except gemini-4-argon*) draw this pool"
    )
    assert windows["Claude/GPT weekly"]["models_hint"] == "agy models — claude-* / gpt-* models draw this pool"


# ── round 2 (adversary review of 464e785) ───────────────────────────────────

FROZEN_NOW = datetime(2026, 10, 18, 13, 48, 18, tzinfo=timezone.utc)
FLAT_RESETS = "2026-10-24T18:18:05+00:00"


def _flat_snapshot(collected: datetime) -> dict:
    """Every antigravity window flat (same used_percent and reset) at ``collected``."""
    windows = []
    for label, used, minutes, wid in (
        ("Gemini 5-hour", 3.0, 300, "antigravity-quota-summary-gemini-5h"),
        ("Gemini weekly", 10.0, 10080, "antigravity-quota-summary-gemini-weekly"),
        ("Gemini 4 Argon weekly", 20.0, 10080, ARGON_ID),
        ("Claude/GPT weekly", 30.0, 10080, "antigravity-quota-summary-3p-weekly"),
    ):
        window = _window(label, used, minutes, window_id=wid)
        window["resets_at"] = FLAT_RESETS
        windows.append(window)
    return {
        "collected_at": collected.isoformat(),
        "accounts": [
            {
                "provider": "antigravity",
                "account": None,
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "cli_binary": "agy",
                "windows": windows,
            }
        ],
    }


def _write_agy_turn(log_dir: Path, *, model: str) -> None:
    when = datetime(2026, 10, 18, 13, 0, 0, tzinfo=timezone.utc)
    name = f"{when.astimezone().strftime('%Y%m%d-%H%M%S')}-agy-1.jsonl"
    events = [
        {"t": when.timestamp(), "kind": "initialize", "data": {}},
        {"t": when.timestamp() + 0.1, "kind": "set_config", "data": {"id": "model", "value": model}},
        {"t": when.timestamp() + 1.0, "kind": "prompt_response", "data": {"stopReason": "end_turn"}},
    ]
    (log_dir / name).write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _frozen_after_turn(tmp_path, model: str) -> dict:
    from aiuse.analysis.selfdescribe import enrich_snapshot

    times = [datetime(2026, 10, 18, h, m, tzinfo=timezone.utc) for h, m in ((12, 1), (13, 14), (13, 34))]
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    _write_agy_turn(log_dir, model=model)
    return enrich_snapshot(
        _flat_snapshot(times[-1]),
        now=FROZEN_NOW,
        history=[_flat_snapshot(t) for t in times],
        check_frozen=True,
        min_frozen_seconds=2700,
        acp_log_dir=str(log_dir),
    )


def _frozen_labels(snap: dict) -> set[str]:
    return {w["label"] for w in snap["accounts"][0]["windows"] if w.get("measurement_frozen")}


def test_frozen_argon_window_leaves_flash_and_claude_gpt_usable(tmp_path):
    from aiuse.analysis.selfdescribe import available_pools

    snap = _frozen_after_turn(tmp_path, "gemini-4-argon")
    # F5: an Argon turn freezes only the Argon window; the flat Flash windows stay unfrozen.
    assert _frozen_labels(snap) == {"Gemini 4 Argon weekly"}
    pools = _families(snap)
    assert pools["gemini_argon"]["usable_now"] is False
    assert pools["gemini"]["usable_now"] is True
    assert pools["claude_gpt"]["usable_now"] is True
    assert {p["pool_family"] for p in available_pools(snap)} == {"gemini", "claude_gpt"}
    # the account-level flag stays for account-level consumers
    assert snap["accounts"][0]["measurement_frozen"] is True
    assert snap["accounts"][0]["usable_now"] is False


def test_frozen_flash_window_leaves_argon_and_claude_gpt_usable(tmp_path):
    from aiuse.analysis.selfdescribe import available_pools

    snap = _frozen_after_turn(tmp_path, "gemini-3-flash")
    assert _frozen_labels(snap) == {"Gemini 5-hour", "Gemini weekly"}
    pools = _families(snap)
    assert pools["gemini"]["usable_now"] is False
    assert pools["gemini_argon"]["usable_now"] is True
    assert pools["claude_gpt"]["usable_now"] is True
    assert {p["pool_family"] for p in available_pools(snap)} == {"gemini_argon", "claude_gpt"}


@pytest.mark.parametrize(
    ("window_id", "name"),
    [
        ("antigravity-quota-summary-gemini-argon-wtu-weekly", "argon"),
        ("antigravity-quota-summary-gemini-4-argon-wtu-weekly", "argon"),
        ("antigravity-quota-summary-gemini-4.1-argon-wtu-weekly", "argon"),
        ("antigravity-quota-summary-gemini-carbon-wtu-weekly", "carbon"),
        ("antigravity-quota-summary-gemini-argon-5h", "argon"),
        # Flash ids, with or without the wtu infix: never a family of their own.
        ("antigravity-quota-summary-gemini-weekly", None),
        ("antigravity-quota-summary-gemini-5h", None),
        ("antigravity-quota-summary-gemini-wtu-weekly", None),
        ("antigravity-quota-summary-gemini-flash-weekly", None),
        ("antigravity-quota-summary-3p-weekly", None),
        (None, None),
    ],
)
def test_gemini_id_regex_names(window_id, name):
    from aiuse.analysis.pace import gemini_frontier_name

    assert gemini_frontier_name(None, window_id) == name


@pytest.mark.parametrize(
    ("label", "window_id", "expected"),
    [
        ("Gemini weekly", ARGON_ID, "Gemini 4 Argon weekly"),
        ("Gemini 4 Argon weekly", ARGON_ID, "Gemini 4 Argon weekly"),
        ("Gemini weekly", "antigravity-quota-summary-gemini-4-carbon-wtu-weekly", "Gemini 4 Carbon weekly"),
        ("Gemini weekly", "antigravity-quota-summary-gemini-carbon-wtu-weekly", "Gemini Carbon weekly"),
        ("Gemini 5-hour", "antigravity-quota-summary-gemini-argon-5h", "Gemini 4 Argon 5-hour"),
        ("Gemini weekly", "antigravity-quota-summary-gemini-weekly", "Gemini weekly"),
        ("Gemini weekly", None, "Gemini weekly"),
    ],
)
def test_canonical_gemini_label(label, window_id, expected):
    from aiuse.analysis.pace import canonical_gemini_label

    assert canonical_gemini_label(label, window_id) == expected


def test_collector_names_the_argon_window_when_codexbar_titles_it_gemini_weekly():
    from aiuse.collectors.codexbar import _from_row

    def extra(title: str, wid: str) -> dict:
        return {
            "id": wid,
            "title": title,
            "window": {"usedPercent": 40, "resetsAt": "2026-11-03T02:35:35Z", "windowMinutes": 10080},
        }

    acc = _from_row(
        {
            "provider": "antigravity",
            "source": "cli",
            "usage": {
                "extraRateWindows": [
                    extra("Gemini weekly", ARGON_ID),
                    extra("Gemini weekly", "antigravity-quota-summary-gemini-weekly"),
                ]
            },
        }
    )
    assert [w.label for w in acc.windows] == ["Gemini 4 Argon weekly", "Gemini weekly"]


def test_enrichment_canonicalises_a_stored_plain_label():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    snap = enrich_snapshot(_snapshot(argon_label="Gemini weekly"))
    labels = sorted(w["label"] for w in snap["accounts"][0]["windows"] if w["pool_family"].startswith("gemini"))
    assert labels == ["Gemini 4 Argon weekly", "Gemini 5-hour", "Gemini weekly"]
    assert _families(snap)["gemini_argon"]["binding_window"] == "Gemini 4 Argon weekly"


def test_argon_window_without_gemini_in_label_still_gets_the_family():
    """The id alone decides (matrix/history keys already did; JSON pool_family now agrees)."""
    from aiuse.analysis.pace import independent_pool_key
    from aiuse.analysis.selfdescribe import pool_family_id

    assert pool_family_id("antigravity", "Argon weekly", ARGON_ID) == "gemini_argon"
    assert independent_pool_key("Argon weekly", window_id=ARGON_ID, provider="antigravity") == "gemini_argon"
    assert pool_family_id("antigravity", "Claude/GPT weekly", "antigravity-quota-summary-3p-weekly") == "claude_gpt"


def test_flash_models_hint_excludes_argon():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    windows = {w["label"]: w for w in enrich_snapshot(_snapshot())["accounts"][0]["windows"]}
    assert "except gemini-4-argon*" in windows["Gemini weekly"]["models_hint"]


def _burn_alert(label: str, *, remaining: float, kind: str = "burn"):
    from aiuse.models import Urgency, UseOrLoseAlert

    return UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="antigravity",
        account=None,
        window_label=label,
        remaining_percent=remaining,
        days_until_reset=2.0,
        plan=None,
        message=f"{label} alert",
        source="test",
        score=50.0,
        kind=kind,
        window_minutes=10080,
    )


def _labels_for_argon_and_flash() -> tuple[str, str]:
    from aiuse.analysis.pace import canonical_gemini_label

    return (
        canonical_gemini_label("Gemini weekly", ARGON_ID),
        canonical_gemini_label("Gemini weekly", "antigravity-quota-summary-gemini-weekly"),
    )


def test_chat_format_action_items_keep_argon_and_flash_apart():
    from aiuse.chat_format import _build_action_items

    argon_label, flash_label = _labels_for_argon_and_flash()
    alerts = [_burn_alert(argon_label, remaining=60.0), _burn_alert(flash_label, remaining=40.0)]
    assert len(_build_action_items(alerts)) == 2
    # the unfixed shape: two plain "Gemini weekly" labels collapse into one pool
    assert len(_build_action_items([_burn_alert("Gemini weekly", remaining=60.0)] * 2)) == 1


def test_ranking_flash_alert_does_not_mask_argon_alert():
    from aiuse.analysis.ranking import _pool_id_for_label, pool_masked

    argon_label, flash_label = _labels_for_argon_and_flash()
    assert _pool_id_for_label(argon_label, "antigravity") == "gemini_argon"
    assert _pool_id_for_label(flash_label, "antigravity") == "gemini"
    argon = _burn_alert(argon_label, remaining=60.0)
    flash_empty = _burn_alert(flash_label, remaining=0.0)
    assert pool_masked(argon, [argon, flash_empty]) is False


def test_attribute_series_key_splits_argon_from_flash():
    from aiuse.analysis.history import window_series_key

    argon_label, flash_label = _labels_for_argon_and_flash()
    argon_key = window_series_key("antigravity", argon_label, 10080)
    assert argon_key == "antigravity:gemini_argon:weekly"
    assert argon_key != window_series_key("antigravity", flash_label, 10080)


def test_frontier_model_prefix_table_covers_every_named_frontier_hint():
    """A frontier name with a window hint but no model prefix maps its turns to Flash."""
    from aiuse.analysis.pace import GEMINI_FRONTIER_MODEL_PREFIXES
    from aiuse.analysis.selfdescribe import _GEMINI_FRONTIER_HINTS

    assert set(_GEMINI_FRONTIER_HINTS) <= set(GEMINI_FRONTIER_MODEL_PREFIXES.values())


def test_schema_semantics_publish_the_model_family_mapping():
    from aiuse.analysis.pace import GEMINI_FRONTIER_MODEL_PREFIXES
    from aiuse.analysis.selfdescribe import SEMANTICS
    from aiuse.quota_refusals import pool_family_for_acp_model

    text = SEMANTICS["model_pool_family"]
    for prefix, name in GEMINI_FRONTIER_MODEL_PREFIXES.items():
        assert f"{prefix}* -> gemini_{name}" in text
        assert pool_family_for_acp_model("antigravity", prefix) == f"gemini_{name}"
