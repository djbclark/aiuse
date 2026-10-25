import pytest

from aiuse.collectors import tokscale
from aiuse.collectors.base import CollectorError, CollectorNotInstalled
from aiuse.models import Snapshot, utcnow
from aiuse.report import render_stderr_meta


def test_missing_optional_tool_raises_not_installed(monkeypatch):
    monkeypatch.setattr(tokscale, "which", lambda name: None)
    with pytest.raises(CollectorNotInstalled):
        tokscale.collect_tokscale()


def test_not_installed_is_a_collector_error():
    # Callers that catch CollectorError keep working unchanged.
    assert issubclass(CollectorNotInstalled, CollectorError)


def test_watch_footer_shows_uninstalled_tools_quietly_not_as_errors():
    snapshot = Snapshot(collected_at=utcnow(), uninstalled_tools=["tokscale", "caut"])
    footer = render_stderr_meta(snapshot, [], color=False, ai_note=False)
    assert "Optional, not installed (expected): tokscale, caut" in footer
    assert "Collector errors" not in footer


def test_real_collector_errors_still_listed_in_footer():
    snapshot = Snapshot(
        collected_at=utcnow(),
        collector_errors=["cswap: boom"],
        uninstalled_tools=["tokscale"],
    )
    footer = render_stderr_meta(snapshot, [], color=False, ai_note=False)
    assert "Collector errors:" in footer
    assert "  - cswap: boom" in footer
