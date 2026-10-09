"""Issue #33: the agy CLI burst lockout is visible without probing agy."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from aiuse import client_limits
from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, summary_line
from aiuse.collectors.runner import _apply_client_limit_notes
from aiuse.models import AccountUsage

NOW = datetime(2026, 10, 6, 10, 40, tzinfo=timezone.utc)


def _glog_stamp(at: datetime) -> str:
    local = at.astimezone()
    return local.strftime("%m%d %H:%M:%S.%f")


def _write_log(directory, name: str, lines: list[str], *, mtime: datetime = NOW) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def _failed(at: datetime, attempt: int) -> str:
    return (
        f"I{_glog_stamp(at)}    2773 run.go:395] Run: attempt {attempt} failed "
        "(RESOURCE_EXHAUSTED (code 429): Resource has been exhausted (e.g. check quota).), retrying in 4s"
    )


def _name(at: datetime) -> str:
    return "cli-" + at.astimezone().strftime("%Y%m%d_%H%M%S") + ".log"


def test_recent_429_attempts_mark_the_cli_limited(tmp_path):
    started = NOW - timedelta(minutes=10)
    _write_log(
        tmp_path,
        _name(started),
        [
            f"I{_glog_stamp(started)}       1 printmode.go:202] Print mode: starting (promptLength=6)",
            _failed(started + timedelta(seconds=4), 1),
            _failed(started + timedelta(seconds=9), 2),
        ],
    )
    limit = client_limits.scan_agy_cli_lockout(now=NOW, log_dir=tmp_path)
    assert limit is not None
    assert (limit["client"], limit["state"], limit["source"]) == ("cli", "limited", "agy-cli-log")
    assert limit["failed_attempts"] == 2 and limit["runs"] == 1
    assert limit["error"] == "RESOURCE_EXHAUSTED (code 429)"
    assert limit["last_limited_at"] == (started + timedelta(seconds=9)).isoformat()
    assert "acp-run agy" in limit["message"] and "last 9m ago" in limit["message"]


def test_old_429s_and_usage_only_logs_are_ignored(tmp_path):
    old = NOW - timedelta(hours=3)
    _write_log(tmp_path, _name(old), [_failed(old, 1)], mtime=old)
    # A file touched recently but whose 429 lines are older than the lookback.
    _write_log(tmp_path, "cli-recent-touch.log", [_failed(old, 2)])
    _write_log(
        tmp_path,
        _name(NOW - timedelta(minutes=2)),
        [f"I{_glog_stamp(NOW)}       1 printmode.go:325] Print mode: running slash command /usage"],
    )
    assert client_limits.scan_agy_cli_lockout(now=NOW, log_dir=tmp_path) is None


def test_missing_log_dir_or_zero_lookback_is_quiet(tmp_path):
    assert client_limits.scan_agy_cli_lockout(now=NOW, log_dir=tmp_path / "nope") is None
    _write_log(tmp_path, _name(NOW), [_failed(NOW - timedelta(minutes=1), 1)])
    assert client_limits.scan_agy_cli_lockout(lookback=timedelta(0), now=NOW, log_dir=tmp_path) is None
    assert client_limits.load_client_limits({"analysis": {"agy_cli_lockout_minutes": 0}}) == {}


def test_lookback_minutes_reads_config():
    assert client_limits.lookback_minutes({}) == 60
    assert client_limits.lookback_minutes({"analysis": {"agy_cli_lockout_minutes": 15}}) == 15
    assert client_limits.lookback_minutes({"analysis": {"agy_cli_lockout_minutes": "x"}}) == 60


LIMIT = {
    "client": "cli",
    "cli_binary": "agy",
    "state": "limited",
    "source": "agy-cli-log",
    "last_limited_at": "2026-10-06T10:31:01+00:00",
    "failed_attempts": 8,
    "message": "agy CLI got RESOURCE_EXHAUSTED (429) ...",
}


def _agy_snapshot() -> dict:
    return {
        "collected_at": NOW.isoformat(),
        "accounts": [
            {
                "provider": "antigravity",
                "cli_binary": "agy",
                "client_limits": [{"stale": True}],
                "windows": [
                    {"label": "Gemini 5-hour", "used_percent": 1.0, "remaining_percent": 99.0},
                    {"label": "Claude/GPT weekly", "used_percent": 51.0, "remaining_percent": 49.0},
                ],
            },
            {"provider": "codex", "windows": [{"label": "codex weekly", "used_percent": 10, "remaining_percent": 90}]},
        ],
    }


def test_enrichment_attaches_limits_but_keeps_the_pool_usable():
    snap = enrich_snapshot(_agy_snapshot(), client_limits={"antigravity": [LIMIT]})
    agy = snap["accounts"][0]
    assert agy["client_limits"] == [LIMIT]
    assert agy["usable_now"] is True  # quota is real; ACP may still serve it
    assert "client_limits" not in snap["accounts"][1]
    pools = [p for p in available_pools(snap) if p["provider"] == "antigravity"]
    assert pools and all(p["client_limits"] == [LIMIT] for p in pools)
    line = summary_line(pools[0])
    assert "[agy cli rate-limited: 429 x8" in line and "ACP may still work]" in line
    assert "client_limits" in snap["semantics"]


def test_read_time_scan_replaces_a_stale_cached_entry():
    snap = enrich_snapshot(_agy_snapshot(), client_limits={})
    assert "client_limits" not in snap["accounts"][0]
    # Without a scan result (old callers), whatever the cache held stays.
    assert enrich_snapshot(_agy_snapshot())["accounts"][0]["client_limits"] == [{"stale": True}]


def test_collection_adds_a_note_to_antigravity_rows(monkeypatch):
    monkeypatch.setattr(client_limits, "load_client_limits", lambda config=None: {"antigravity": [LIMIT]})
    agy = AccountUsage(source="codexbar", provider="antigravity")
    other = AccountUsage(source="codexbar", provider="codex")
    _apply_client_limit_notes([agy, other], {})
    _apply_client_limit_notes([agy, other], {})
    assert agy.notes == [LIMIT["message"]]
    assert other.notes == []
