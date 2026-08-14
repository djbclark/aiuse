"""Shared test fixtures."""

from __future__ import annotations

import pytest

from aiuse.analysis import history


@pytest.fixture(autouse=True)
def isolate_snapshot_dir(tmp_path, monkeypatch):
    """Keep every test out of the operator's real snapshot history.

    Persisting snapshots is on by default (``learn_from_history: auto`` implies
    ``should_persist_snapshots``), so any test that exercises the CLI end to end
    without patching this writes empty snapshots into ``~/.cache/aiuse/snapshots``.
    Those files are indistinguishable from real collections to the history pass,
    and they push genuine samples out of the newest-N window that
    ``chronic_waste_summary`` reads — a test run silently degrades the tool's
    learning on the developer's own machine.

    Tests that need their own directory still patch ``snapshot_dir`` themselves;
    this only guarantees the default is never the real one.
    """
    monkeypatch.setattr(history, "snapshot_dir", lambda: tmp_path / "snapshots")
