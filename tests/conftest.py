"""Shared test fixtures."""

from __future__ import annotations

import pytest

from aiuse import agent_notes, client_limits
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
    monkeypatch.setattr(agent_notes, "notes_dir", lambda: tmp_path / "agent-notes")
    # Never read the developer's real agy CLI logs (issue #33 lockout scan).
    monkeypatch.setattr(client_limits, "agy_cli_log_dir", lambda: tmp_path / "agy-cli-log")


# The width the suite renders at unless a test says otherwise. Wide enough that
# the table's TABLE_MAX_WIDTH cap is the binding constraint rather than the
# terminal, which is the configuration most assertions were written against.
DEFAULT_TEST_COLUMNS = "120"


@pytest.fixture(autouse=True)
def deterministic_terminal_width(monkeypatch):
    """Pin ``$COLUMNS`` so renderer output does not depend on the dev's terminal.

    ``report.terminal_width()`` goes through ``shutil.get_terminal_size``, which
    prefers ``$COLUMNS``. Until now nothing set it, so the suite passed only
    because it happened to be unset in CI and in most shells — every width
    assertion was silently reading whatever window the developer had open, and a
    run inside a narrow pane could fail tests that have nothing to do with the
    change under test. Pin it once here rather than in each affected test.
    """
    monkeypatch.setenv("COLUMNS", DEFAULT_TEST_COLUMNS)


@pytest.fixture(autouse=True)
def no_real_token_ledger(monkeypatch):
    """Keep the suite from shelling out to the developer's real ``tokscale``.

    ``attribution.ledger`` defaults to ``auto``, which turns on wherever
    tokscale is installed, so any end-to-end CLI test would otherwise scan the
    operator's session files. Tests of the ledger itself pass a fake runner.
    """
    from aiuse import ledger

    monkeypatch.setattr(ledger, "ledger_enabled", lambda config=None: False)


@pytest.fixture(autouse=True)
def isolate_user_config(tmp_path, monkeypatch):
    """Keep the operator's real ``~/.config/aiuse/config.toml`` out of every test.

    ``load_config()`` with no path reads the XDG user config, so settings such as
    ``[disabled_services]`` silently changed test outcomes on the developer's own
    machine (``--available`` exited 3 because the real config disables
    antigravity, the provider the fixture is built from). Tests that need a
    config pass one explicitly or patch ``default_config_dir`` themselves.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
