import json
from pathlib import Path
from typing import Any

from aiuse.collectors.cline import collect_cline


def test_collect_cline_empty(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("aiuse.collectors.cline.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    accounts = collect_cline()
    assert len(accounts) == 0


def test_collect_cline_success(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("aiuse.collectors.cline.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    cline_dir = tmp_path / ".cline" / "tasks"
    cline_dir.mkdir(parents=True)
    history_path = cline_dir / "taskHistory.json"

    history_data = [
        {"id": "1", "apiProvider": "anthropic", "tokensIn": 100, "tokensOut": 50, "totalCost": 0.05},
        {"id": "2", "apiProvider": "anthropic", "tokensIn": 200, "tokensOut": 150, "totalCost": 0.10},
        {"id": "3", "apiProvider": "openai", "tokensIn": 300, "tokensOut": 50, "totalCost": 0.03},
    ]
    history_path.write_text(json.dumps(history_data))

    accounts = collect_cline()
    # 2 providers
    assert len(accounts) == 2

    # Check anthropic (mapped to claude)
    claude = next(a for a in accounts if a.provider == "claude")
    assert claude.source == "cline"
    assert claude.billing_kind.value == "payg_api"
    assert claude.usage_credits is not None
    import pytest

    assert claude.usage_credits.used == pytest.approx(0.15)
    assert claude.notes == ["Live data fetched from local Cline task history.", "tokens_in=300", "tokens_out=200"]

    # Check openai (mapped to codex)
    codex = next(a for a in accounts if a.provider == "codex")
    assert codex.source == "cline"
    assert codex.billing_kind.value == "payg_api"
    assert codex.usage_credits is not None
    assert codex.usage_credits.used == pytest.approx(0.03)
    assert codex.notes == ["Live data fetched from local Cline task history.", "tokens_in=300", "tokens_out=50"]
