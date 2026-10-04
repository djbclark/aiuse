"""Self-describing semantics (schema 1.1) — the 2026-10-03 misread-proofing.

Golden fixtures mirror the three real failure modes:
1. codex 5-hour 100% used + weekly 35% used read as "100% free" / usable.
2. agy Claude/GPT exhausted while its Gemini pool still had headroom.
3. null/null windows read as ok instead of unknown.
"""

from __future__ import annotations

import json
from datetime import timedelta

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    utcnow,
)


def _codex_fixture() -> dict:
    now = utcnow()
    return {
        "collected_at": now.isoformat(),
        "accounts": [
            {
                "provider": "codex",
                "account": "djbclark@gmail.com",
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "cli_binary": "codex",
                "windows": [
                    {
                        "label": "Codex 5-hour quota (1)",
                        "used_percent": 100.0,
                        "remaining_percent": 0.0,
                        "resets_at": (now + timedelta(hours=3, minutes=42)).isoformat(),
                        "window_minutes": 300,
                    },
                    {
                        "label": "Codex weekly quota (2)",
                        "used_percent": 35.0,
                        "remaining_percent": 65.0,
                        "resets_at": (now + timedelta(days=4)).isoformat(),
                        "window_minutes": 10080,
                    },
                ],
            }
        ],
    }


def _antigravity_fixture() -> dict:
    now = utcnow()
    return {
        "collected_at": now.isoformat(),
        "accounts": [
            {
                "provider": "antigravity",
                "account": None,
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "cli_binary": "agy",
                "windows": [
                    {
                        "label": "Gemini 5-hour",
                        "used_percent": 25.0,
                        "remaining_percent": 75.0,
                        "resets_at": (now + timedelta(minutes=30)).isoformat(),
                        "window_minutes": 300,
                    },
                    {
                        "label": "Gemini weekly",
                        "used_percent": 28.5,
                        "remaining_percent": 71.5,
                        "resets_at": (now + timedelta(days=3)).isoformat(),
                        "window_minutes": 10080,
                    },
                    {
                        "label": "Claude/GPT 5-hour",
                        "used_percent": 100.0,
                        "remaining_percent": 0.0,
                        "resets_at": (now + timedelta(hours=2)).isoformat(),
                        "window_minutes": 300,
                    },
                    {
                        "label": "Claude/GPT weekly",
                        "used_percent": 51.1,
                        "remaining_percent": 48.9,
                        "resets_at": (now + timedelta(days=6)).isoformat(),
                        "window_minutes": 10080,
                    },
                ],
            }
        ],
    }


def _snapshot_from_fixture(fix: dict) -> Snapshot:
    accounts = []
    for a in fix["accounts"]:
        windows = [
            QuotaWindow(
                label=w["label"],
                used_percent=w.get("used_percent"),
                remaining_percent=w.get("remaining_percent"),
                resets_at=None,
                window_minutes=w.get("window_minutes"),
            )
            for w in a["windows"]
        ]
        accounts.append(
            AccountUsage(
                source=a["source"],
                provider=a["provider"],
                account=a.get("account"),
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=windows,
            )
        )
    return Snapshot(collected_at=utcnow(), accounts=accounts)


# ── window state ────────────────────────────────────────────────────────────


def test_window_state_thresholds():
    from aiuse.analysis.selfdescribe import window_state

    assert window_state({"used_percent": 100.0, "remaining_percent": 0.0}) == "exhausted"
    assert window_state({"used_percent": 99.5, "remaining_percent": 0.5}) == "exhausted"
    assert window_state({"used_percent": 90.0, "remaining_percent": 10.0}) == "tight"
    assert window_state({"used_percent": 85.0, "remaining_percent": 15.0}) == "ok"  # <15 is tight, 15 is ok
    assert window_state({"used_percent": None, "remaining_percent": None}) == "unknown"
    # one-sided: derive from whichever number exists
    assert window_state({"used_percent": 100.0, "remaining_percent": None}) == "exhausted"
    assert window_state({"used_percent": None, "remaining_percent": 40.0}) == "ok"


def test_null_windows_are_unknown_never_ok():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    snap = enrich_snapshot(
        {
            "collected_at": utcnow().isoformat(),
            "accounts": [
                {
                    "provider": "qwencloud",
                    "source": "qwencloud",
                    "windows": [{"label": "Qwen monthly", "used_percent": None, "remaining_percent": None}],
                }
            ],
        }
    )
    window = snap["accounts"][0]["windows"][0]
    assert window["state"] == "unknown"
    assert window["headroom_percent"] is None
    assert snap["accounts"][0]["usable_now"] is False  # no evidence of headroom
    assert "UNKNOWN (no data)" in snap["summary_lines"][0]


# ── golden: codex-shaped (failure mode 1 and 3) ─────────────────────────────


def test_codex_fixture_golden():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    snap = enrich_snapshot(_codex_fixture())
    account = snap["accounts"][0]
    assert account["usable_now"] is False
    assert account["binding_window"] == "Codex 5-hour quota (1)"
    assert account["available_at"] is not None
    line = snap["summary_lines"][0]
    assert line.startswith("codex:")
    assert "EXHAUSTED (100% used / 0% left" in line
    assert "35% used / 65% left" in line
    assert line.rstrip().endswith("NOT usable now")
    # semantics ships inside the snapshot itself
    assert snap["semantics"]["used_percent"].startswith("share CONSUMED")


# ── golden: antigravity family split (failure mode 2) ───────────────────────


def test_antigravity_fixture_family_split():
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, pool_entries

    snap = enrich_snapshot(_antigravity_fixture())
    windows = {w["label"]: w for w in snap["accounts"][0]["windows"]}
    assert windows["Gemini 5-hour"]["pool_family"] == "gemini"
    assert windows["Gemini 5-hour"]["state"] == "ok"
    assert windows["Claude/GPT 5-hour"]["pool_family"] == "claude_gpt"
    assert windows["Claude/GPT 5-hour"]["state"] == "exhausted"
    assert windows["Gemini 5-hour"]["models_hint"]
    # the account as a whole is not usable (claude_gpt binds), …
    assert snap["accounts"][0]["usable_now"] is False
    # … but the gemini family is, and --available lists only it
    pools = {p["pool_family"]: p for p in pool_entries(snap)}
    assert pools["gemini"]["usable_now"] is True
    assert pools["claude_gpt"]["usable_now"] is False
    available = available_pools(snap)
    assert [p["pool_family"] for p in available] == ["gemini"]
    assert available[0]["models_hint"]
    gemini_line = next(line for line in snap["summary_lines"] if "gemini" in line)
    assert "ok (25% used / 75% left" in gemini_line
    assert "NOT usable now" not in gemini_line


def test_available_pools_sorted_by_headroom():
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    snap = enrich_snapshot(_codex_fixture())
    fix2 = _antigravity_fixture()
    snap["accounts"].extend(enrich_snapshot(fix2)["accounts"])
    pools = available_pools(snap)
    headrooms = [p["headroom_percent"] for p in pools]
    assert headrooms == sorted(headrooms, reverse=True)
    assert all(p["usable_now"] for p in pools)


# ── agent notes ─────────────────────────────────────────────────────────────


def test_note_exhausted_write_apply_and_label(tmp_path, monkeypatch, capsys):
    from aiuse import agent_notes
    from aiuse.analysis.selfdescribe import enrich_snapshot

    monkeypatch.setattr(agent_notes, "notes_dir", lambda: tmp_path / "agent-notes")

    code = agent_notes.run_note_exhausted(["codex", "--resets-in", "4h53m", "--reason", "You've hit your usage limit"])
    assert code == 0
    notes = agent_notes.load_active_notes()
    assert len(notes) == 1
    assert notes[0]["provider"] == "codex"
    assert notes[0]["source"] == "agent-reported"

    snap = enrich_snapshot(_codex_fixture(), notes=notes)
    weekly = snap["accounts"][0]["windows"][1]  # 35% used — note flips it
    assert weekly["state"] == "exhausted"
    assert weekly["state_source"] == "agent-reported"
    assert snap["accounts"][0]["usable_now"] is False
    assert snap["agent_notes"][0]["source"] == "agent-reported"
    line = snap["summary_lines"][0]
    assert "[agent-reported]" in line


def test_note_exhaustion_flips_healthy_pool(tmp_path, monkeypatch):
    from aiuse import agent_notes
    from aiuse.analysis.selfdescribe import enrich_snapshot

    monkeypatch.setattr(agent_notes, "notes_dir", lambda: tmp_path / "agent-notes")
    # healthy codex: 9% used
    fix = _codex_fixture()
    fix["accounts"][0]["windows"][0].update(used_percent=9.0, remaining_percent=91.0)
    fix["accounts"][0]["windows"][1].update(used_percent=37.0, remaining_percent=63.0)

    notes = [
        {
            "provider": "codex",
            "pool_family": None,
            "resets_at": (utcnow() + timedelta(hours=4)).isoformat(),
            "reason": "429",
            "source": "agent-reported",
        }
    ]
    snap = enrich_snapshot(fix, notes=notes)
    assert snap["accounts"][0]["usable_now"] is False
    assert snap["accounts"][0]["windows"][0]["state"] == "exhausted"


def test_expired_note_is_advisory_only(tmp_path, monkeypatch):
    from aiuse import agent_notes
    from aiuse.analysis.selfdescribe import enrich_snapshot

    ndir = tmp_path / "agent-notes"
    ndir.mkdir()
    (ndir / "codex.json").write_text(
        json.dumps(
            {
                "provider": "codex",
                "pool_family": None,
                "resets_at": (utcnow() - timedelta(minutes=1)).isoformat(),
                "reason": "429",
                "source": "agent-reported",
            }
        )
    )
    monkeypatch.setattr(agent_notes, "notes_dir", lambda: ndir)
    assert agent_notes.load_active_notes() == []  # expired note pruned
    assert not (ndir / "codex.json").exists()

    fix = _codex_fixture()
    fix["accounts"][0]["windows"][0].update(used_percent=9.0, remaining_percent=91.0)
    fix["accounts"][0]["windows"][1].update(used_percent=37.0, remaining_percent=63.0)
    snap = enrich_snapshot(fix, notes=[])
    assert snap["accounts"][0]["usable_now"] is True


def test_parse_resets_in():
    from aiuse.agent_notes import parse_resets_in

    assert parse_resets_in("4h53m") == timedelta(hours=4, minutes=53)
    assert parse_resets_in("90m") == timedelta(minutes=90)
    assert parse_resets_in("2h") == timedelta(hours=2)
    assert parse_resets_in("1d2h") == timedelta(days=1, hours=2)
    try:
        parse_resets_in("soon")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# ── --available CLI ─────────────────────────────────────────────────────────


def test_cli_available_reads_cache_and_filters(tmp_path, monkeypatch, capsys):
    from aiuse.analysis import history
    from aiuse.cli import main

    cache = tmp_path / "snapshots"
    cache.mkdir()
    (cache / "latest.json").write_text(json.dumps(_antigravity_fixture()))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)

    assert main(["--available", "--json", "-q"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "cache"
    assert payload["schema_version"] == "1.1"
    assert [p["pool_family"] for p in payload["available"]] == ["gemini"]
    assert payload["available"][0]["provider"] == "antigravity"
    assert payload["semantics"]["used_percent"]
    assert payload["age_seconds"] is not None
    assert payload["agent_notes"] == []


def test_cli_available_exit_three_when_nothing_usable(tmp_path, monkeypatch, capsys):
    from aiuse.analysis import history
    from aiuse.cli import main

    cache = tmp_path / "snapshots"
    cache.mkdir()
    (cache / "latest.json").write_text(json.dumps(_codex_fixture()))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)

    assert main(["--available", "--json", "-q"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] == []


def test_cli_note_exhausted_dispatch(tmp_path, monkeypatch, capsys):
    from aiuse import agent_notes
    from aiuse.cli import main

    monkeypatch.setattr(agent_notes, "notes_dir", lambda: tmp_path / "agent-notes")
    monkeypatch.setattr("aiuse.agent_notes.notes_dir", lambda: tmp_path / "agent-notes")
    assert main(["note-exhausted", "codex", "--resets-in", "1h", "--reason", "429"]) == 0
    out = capsys.readouterr().out
    assert "noted: codex exhausted until" in out


# ── cache file shape: flat + snapshot mirror ────────────────────────────────


def test_save_snapshot_writes_enriched_flat_and_mirror(tmp_path, monkeypatch):
    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)
    snap = _snapshot_from_fixture(_codex_fixture())
    path = history.save_snapshot(snap, [])
    data = json.loads(path.read_text())
    assert data["schema_version"] == "1.1"
    # flat path still works (aiuse-pools reads .accounts[])
    assert data["accounts"][0]["windows"][0]["state"] == "exhausted"
    assert data["accounts"][0]["usable_now"] is False
    # envelope path now works on the cache file too
    assert data["snapshot"]["accounts"][0]["windows"][0]["state"] == "exhausted"
    assert data["semantics"]["used_percent"]
    assert data["summary_lines"]
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert latest["snapshot"]["accounts"] == latest["accounts"]


# ── freshness ───────────────────────────────────────────────────────────────


def test_freshness_threshold():
    from aiuse.analysis.selfdescribe import freshness

    now = utcnow()
    assert freshness((now - timedelta(minutes=5)).isoformat(), now=now) == {
        "age_seconds": 300.0,
        "fresh": True,
    }
    result = freshness((now - timedelta(minutes=40)).isoformat(), now=now)
    assert result["fresh"] is False
    assert freshness(None) == {"age_seconds": None, "fresh": False}


# ── human output never prints a bare percentage ─────────────────────────────


def test_plain_output_never_bare_percentage():
    import re

    from aiuse.analysis.suggest import format_suggestion_line
    from aiuse.models import Urgency, UseOrLoseAlert
    from aiuse.report import render_report, render_status_line

    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="djbclark@gmail.com",
        window_label="Codex 5-hour quota (1)",
        remaining_percent=0.0,
        days_until_reset=0.25,
        plan="plus",
        message="burn",
        source="codexbar",
        score=80.0,
        kind="burn",
    )
    snap = _snapshot_from_fixture(_codex_fixture())
    outputs = [
        render_report(snap, [alert], color=False),
        render_status_line(snap, [alert]),
        format_suggestion_line(alert),
    ]
    bare = re.compile(r"\d+(?:\.\d+)?%")
    labeled = re.compile(r"%\s*(used|left|waste)")
    for text in outputs:
        for line in text.splitlines():
            for match in bare.finditer(line):
                tail = line[match.start() :]
                assert labeled.search(tail), f"bare percentage {match.group()!r} in line: {line!r}"


def test_summary_line_names_the_binary_when_it_differs_from_the_provider_id():
    from aiuse.analysis.selfdescribe import summary_line

    window = {"label": "Cursor Auto", "state": "ok", "used_percent": 1.0, "headroom_percent": 99.0}
    cursor = {"provider": "cursor", "pool_family": "auto", "cli_binary": "cursor-agent", "windows": [window]}
    assert summary_line(cursor).startswith("cursor auto [cursor-agent]: Cursor Auto: ok")
    codex = {"provider": "codex", "pool_family": None, "cli_binary": "codex", "windows": [window]}
    assert summary_line(codex).startswith("codex: ")


def test_reset_times_are_local_twelve_hour():
    from datetime import timedelta

    from aiuse.analysis.selfdescribe import _reset_fragment
    from aiuse.models import utcnow
    from aiuse.report import format_clock

    soon, later = utcnow() + timedelta(hours=3), utcnow() + timedelta(days=4)
    assert _reset_fragment(soon.isoformat()) == f", resets {format_clock(soon)}"
    assert _reset_fragment(later.isoformat()) == f", resets {format_clock(later, date=True)}"
    assert _reset_fragment(soon.isoformat()).endswith(("am", "pm"))
