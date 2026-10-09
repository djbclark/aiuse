"""`aiuse history`: History insights from saved snapshots, never a collect (issue #13)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from aiuse import cli
from aiuse.analysis import history
from aiuse.models import provider_display_name, utcnow


def _write_late_cycle_snapshots(remaining: tuple[float, ...] = (70.0, 65.0)) -> None:
    """Codex weekly seen ~90% into its window with lots left: a history burn candidate."""
    directory = history.snapshot_dir()
    directory.mkdir(parents=True, exist_ok=True)
    now = utcnow()
    resets = now + timedelta(days=0.5)
    for i, rem in enumerate(remaining):
        ts = now - timedelta(hours=2 + i)
        payload = {
            "collected_at": ts.isoformat(),
            "accounts": [
                {
                    "source": "codexbar",
                    "provider": "codex",
                    "account": "u@x.com",
                    "billing_kind": "subscription_window",
                    "windows": [
                        {
                            "label": "Codex weekly",
                            "remaining_percent": rem,
                            "used_percent": 100 - rem,
                            "window_minutes": 10080,
                            "resets_at": resets.isoformat(),
                        }
                    ],
                }
            ],
            "alerts": [],
        }
        name = ts.strftime("%Y-%m-%dT%H%M%S.%fZ") + ".json"
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def no_collect(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("aiuse history must not collect")

    monkeypatch.setattr(cli, "run_collectors", boom)
    monkeypatch.setattr("aiuse.collectors.runner.run_collectors", boom)


def test_history_without_snapshots_exits_1_and_says_how_to_get_some(no_collect, capsys):
    assert cli.main(["history"]) == 1
    err = capsys.readouterr().err
    assert "no snapshots" in err
    assert "persist_snapshots" in err


def test_history_prints_the_burn_headline_and_the_history_section(no_collect, capsys):
    _write_late_cycle_snapshots()
    assert cli.main(["history"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("aiuse history · newest snapshot ")
    assert "read from disk, no collect" in lines[0]
    codex = provider_display_name("codex")
    assert lines[1] == f"Burn from history: {codex} weekly (~68% left late, 2 samples)"
    assert "History: 2 snapshots" in out
    assert "learning auto/on" in out
    assert "History suggests burning these" in out


def test_history_json_exposes_burn_candidates_for_scripts(no_collect, capsys):
    _write_late_cycle_snapshots()
    assert cli.main(["history", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "cache"
    assert payload["collected_at"]
    assert payload["age_seconds"] is not None
    hist = payload["history"]
    assert hist["learning_active"] is True
    assert hist["snapshot_count"] == 2
    assert [(c["provider"], c["duration_kind"]) for c in hist["burn_candidates_from_history"]] == [("codex", "weekly")]
    # Same object shape as the top-level `history` on full --json.
    assert set(hist) == {
        "snapshot_count",
        "learning_active",
        "retention_days",
        "learned_burn_rates",
        "chronic_underuse",
        "usually_left_late_cycle",
        "burn_candidates_from_history",
    }


def test_history_headline_when_nothing_is_left_late(no_collect, capsys):
    _write_late_cycle_snapshots(remaining=(10.0, 5.0))
    assert cli.main(["history"]) == 0
    out = capsys.readouterr().out
    assert "Burn from history: nothing stands out" in out


def test_history_with_learning_off_has_no_headline_and_explains(no_collect, capsys, tmp_path):
    _write_late_cycle_snapshots()
    config = tmp_path / "config.toml"
    config.write_text("[analysis]\nlearn_from_history = false\n")
    assert cli.main(["history", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "Burn from history" not in out
    assert "Learning disabled" in out


def test_history_burn_headline_caps_at_three_and_counts_the_rest():
    candidates = [
        {"provider": p, "duration_kind": "weekly", "avg_remaining_pct": 50.0, "sample_count": 1}
        for p in ("codex", "claude", "copilot", "grok", "cursor")
    ]
    line = cli.history_burn_headline({"learning_active": True, "burn_candidates_from_history": candidates})
    assert line is not None
    assert line.count("weekly") == 3
    assert line.endswith("· +2 more")
    assert "1 sample)" in line
    assert cli.history_burn_headline({"learning_active": False}) is None


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(None, "age unknown"), (30, "30s old"), (600, "10m old"), (7200, "2h old"), (3 * 86400, "3.0d old")],
)
def test_age_text(seconds, text):
    assert cli._age_text(seconds) == text
