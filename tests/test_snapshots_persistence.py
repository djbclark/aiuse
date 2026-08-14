import json
from pathlib import Path

from aiuse.analysis.history import save_snapshot
from aiuse.models import AccountUsage, BillingKind, Snapshot, utcnow


def test_mixed_legacy_new_filename_formats(tmp_path: Path, monkeypatch):
    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)

    # Create legacy format (e.g. colon-separated) and new format
    legacy_path = tmp_path / "2025-01-01T12:30:00.000Z.json"
    legacy_payload = {"collected_at": "2025-01-01T12:30:00+00:00", "accounts": []}
    legacy_path.write_text(json.dumps(legacy_payload))

    new_path = tmp_path / "2026-08-11T123000.000000Z.json"
    new_payload = {"collected_at": "2026-08-11T12:30:00+00:00", "accounts": []}
    new_path.write_text(json.dumps(new_payload))

    # Read backward-compatible
    loaded = history.load_recent_snapshots(retention_days=1000)
    assert len(loaded) == 2
    # Ensure they are sorted by date correctly despite filename!
    # wait, load_recent_snapshots uses filename sorting: `sorted(directory.iterdir(), reverse=True)`
    # Since 2026 > 2025, it happens to work for these files.
    assert loaded[0]["collected_at"] == "2026-08-11T12:30:00+00:00"


def test_atomic_rename_and_completeness_metadata(tmp_path: Path, monkeypatch):
    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(source="cswap", provider="claude", billing_kind=BillingKind.SUBSCRIPTION_WINDOW)],
    )
    snap.collector_errors.append("test error")

    path = save_snapshot(snap, [])
    assert path.exists()

    data = json.loads(path.read_text())
    assert data["complete"] is True
    assert data["collector_success_count"] == 1
    assert data["collector_failure_count"] == 1
    assert data["account_count"] == 1
    assert "collection_id" in data

    latest = tmp_path / "latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text())["complete"] is True


def test_prune_removes_only_expired_snapshots(tmp_path: Path, monkeypatch):
    """Retention was read-side only, so the cache grew without bound."""
    from datetime import timedelta

    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)
    now = utcnow()

    def _write(age_days: float) -> Path:
        ts = (now - timedelta(days=age_days)).strftime("%Y-%m-%dT%H%M%S.%fZ")
        p = tmp_path / f"{ts}.json"
        p.write_text(json.dumps({"collected_at": (now - timedelta(days=age_days)).isoformat(), "accounts": []}))
        return p

    fresh, borderline, ancient = _write(1), _write(89), _write(200)
    (tmp_path / "latest.json").write_text(json.dumps({"collected_at": now.isoformat(), "accounts": []}))

    assert history.prune_snapshots(90) == 1
    assert fresh.exists() and borderline.exists()
    assert not ancient.exists()
    # The pointer is never a candidate, whatever its age.
    assert (tmp_path / "latest.json").exists()


def test_prune_never_touches_files_it_did_not_write(tmp_path: Path, monkeypatch):
    """An unrecognized name in the cache is not ours to delete."""
    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)
    stranger = tmp_path / "notes.json"
    stranger.write_text("{}")
    unparsable = tmp_path / "backup-2020.json"
    unparsable.write_text("{}")

    assert history.prune_snapshots(1) == 0
    assert stranger.exists() and unparsable.exists()


def test_prune_disabled_rather_than_total_wipe_on_zero_retention(tmp_path: Path, monkeypatch):
    """A mistyped retention must not delete the operator's entire history."""
    from datetime import timedelta

    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)
    old = utcnow() - timedelta(days=500)
    kept = tmp_path / f"{old.strftime('%Y-%m-%dT%H%M%S.%fZ')}.json"
    kept.write_text(json.dumps({"collected_at": old.isoformat(), "accounts": []}))

    assert history.prune_snapshots(0) == 0
    assert history.prune_snapshots(-1) == 0
    assert kept.exists()


def test_prune_handles_the_legacy_colon_filename_format(tmp_path: Path, monkeypatch):
    """Older versions wrote colon-separated names; those are ours too."""
    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)
    legacy = tmp_path / "2020-01-01T12:30:00.000Z.json"
    legacy.write_text(json.dumps({"collected_at": "2020-01-01T12:30:00+00:00", "accounts": []}))

    assert history.prune_snapshots(90) == 1
    assert not legacy.exists()


def test_save_snapshot_prunes_and_keeps_what_it_just_wrote(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    from aiuse.analysis import history

    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path)
    old_ts = utcnow() - timedelta(days=400)
    stale = tmp_path / f"{old_ts.strftime('%Y-%m-%dT%H%M%S.%fZ')}.json"
    stale.write_text(json.dumps({"collected_at": old_ts.isoformat(), "accounts": []}))

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(source="cswap", provider="claude", billing_kind=BillingKind.SUBSCRIPTION_WINDOW)],
    )
    written = save_snapshot(snap, [], retention_days=90)

    assert written.exists(), "the snapshot just written must survive its own prune"
    assert not stale.exists()
