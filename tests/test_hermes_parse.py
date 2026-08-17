import json
from pathlib import Path
from typing import Any

from aiuse.collectors.hermes import collect_hermes


def test_collect_hermes_empty(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("aiuse.collectors.hermes.Path.home", lambda: tmp_path)
    # The ~/.local/state/hermes/ doesn't exist
    accounts = collect_hermes()
    assert accounts == []


def test_collect_hermes_success(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("aiuse.collectors.hermes.Path.home", lambda: tmp_path)

    hermes_dir = tmp_path / ".local" / "state" / "hermes" / "ai-frontier-weekly-research"
    hermes_dir.mkdir(parents=True)
    history_path = hermes_dir / "latest-usage.json"

    # We will put one success case and one failure case
    history_path.write_text(
        json.dumps(
            {
                "estimated_cost_usd": 0.05,
                "input_tokens": 300,
                "output_tokens": 200,
                "total_tokens": 500,
                "api_calls": 5,
                "model": "claude-3-5-sonnet",
                "provider": "anthropic",
                "failed": False,
            }
        ),
        encoding="utf-8",
    )

    hermes_dir2 = tmp_path / ".local" / "state" / "hermes" / "another-session"
    hermes_dir2.mkdir(parents=True)
    history_path2 = hermes_dir2 / "latest-usage.json"
    history_path2.write_text(
        json.dumps(
            {
                "estimated_cost_usd": 0.01,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "api_calls": 2,
                "model": "gpt-4o",
                "provider": "openai",
                "failed": True,
                "failure": "Codex provider quota exhausted (429); retry after 482396s. Credentials are still valid.",
            }
        ),
        encoding="utf-8",
    )

    # Add a fallback test
    hermes_dir3 = tmp_path / ".local" / "state" / "hermes" / "fallback-session"
    hermes_dir3.mkdir(parents=True)
    history_path3 = hermes_dir3 / "latest-usage.json"
    history_path3.write_text(
        json.dumps(
            {
                "estimated_cost_usd": None,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "api_calls": None,
                "model": None,
                "provider": None,
                "failed": True,
                "failure": "Anthropic connection error.",
            }
        ),
        encoding="utf-8",
    )

    accounts = collect_hermes()
    assert len(accounts) == 2

    # Ensure accounts are separated by canonical provider IDs
    claude = next(a for a in accounts if a.provider == "claude")
    assert claude.source == "hermes"
    assert claude.billing_kind.value == "payg_api"
    assert claude.usage_credits is not None
    assert claude.usage_credits.used == 0.05
    assert claude.notes == [
        "Live data fetched from local Hermes session logs.",
        "Tokens: 300 in / 200 out",
        "Recorded failures: 1",
    ]
    assert claude.error == "Anthropic connection error."

    codex = next(a for a in accounts if a.provider == "codex")
    assert codex.source == "hermes"
    assert codex.billing_kind.value == "payg_api"
    assert codex.usage_credits is not None
    assert codex.usage_credits.used == 0.01
    assert "Tokens: 100 in / 50 out" in codex.notes
    assert codex.error == "Codex provider quota exhausted (429); retry after 482396s. Credentials are still valid."
