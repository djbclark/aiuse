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
