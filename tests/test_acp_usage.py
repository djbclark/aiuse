"""ACP log parsing, blend vs pin, and the usage-sources inventory."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.collectors.acp_usage import collect_acp, scan_acp_logs
from aiuse.collectors.runner import _select_and_cross_check
from aiuse.config import validate_config
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow
from aiuse.report import _context_fragment, _priority_account_line, _Style
from aiuse.usage_sources import usage_source_report


def _write(directory, name: str, events: list[dict]) -> None:
    path = directory / name
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _usage_update(used: int, size: int, *, cost: dict | None = None) -> dict:
    data = {"sessionUpdate": "usage_update", "used": used, "size": size}
    if cost is not None:
        data["cost"] = cost
    return {"kind": "update", "data": data}


def _prompt(usage: dict | None = None, quota: dict | None = None) -> dict:
    data: dict = {"stopReason": "end_turn"}
    if usage is not None:
        data["usage"] = usage
    if quota is not None:
        data["_meta"] = {"quota": quota}
    return {"kind": "prompt_response", "data": data}


def test_agy_log_is_context_fill_without_turn_usage(tmp_path):
    _write(tmp_path, "20261009-213010-agy-26889.jsonl", [_usage_update(33938, 1048576)])
    _write(tmp_path, "20261009-180000-agy-1.jsonl", [_usage_update(1, 100)])

    accounts = collect_acp(log_dir=tmp_path, max_age_hours=0, now=datetime.now(timezone.utc))

    assert len(accounts) == 1
    account = accounts[0]
    assert account.provider == "antigravity"
    assert account.source == "acp"
    assert account.windows == []
    assert account.context_usage is not None
    assert account.context_usage.used == 33938
    assert account.context_usage.size == 1048576
    assert account.context_usage.signals == ("usage_update",)
    assert account.context_usage.turn_usage is None
    body = account.to_dict()["context_usage"]
    assert body["metric"] == "acp_context"
    assert "Not a 5h" in body["note"]


def test_claude_log_keeps_turn_usage_and_per_turn_quota(tmp_path):
    _write(
        tmp_path,
        "20261008-101319-claude-30522.jsonl",
        [
            _usage_update(55190, 1000000, cost={"amount": 0.2, "currency": "USD"}),
            _prompt(
                {"totalTokens": 55190, "inputTokens": 2, "outputTokens": 4},
                {"token_count": {"totalTokens": 55190}},
            ),
        ],
    )

    accounts = collect_acp(log_dir=tmp_path, max_age_hours=0)

    context = accounts[0].context_usage
    assert context is not None
    assert context.signals == ("usage_update", "usage", "quota")
    assert context.turn_usage == {"totalTokens": 55190, "inputTokens": 2, "outputTokens": 4}
    assert context.turn_quota == {"token_count": {"totalTokens": 55190}}
    assert context.cost_amount == 0.2
    assert accounts[0].provider == "claude"


def test_copilot_turn_usage_without_quota(tmp_path):
    _write(
        tmp_path,
        "20261006-120000-copilot-1.jsonl",
        [
            _usage_update(4000, 200000),
            _prompt({"totalTokens": 80, "inputTokens": 70, "outputTokens": 10}),
        ],
    )

    context = collect_acp(log_dir=tmp_path, max_age_hours=0)[0].context_usage

    assert context is not None
    assert context.signals == ("usage_update", "usage")
    assert context.turn_quota is None
    assert context.turn_usage["totalTokens"] == 80


def test_newer_agy_refined_beats_older_agy(tmp_path):
    agy = tmp_path / "20261009-180000-agy-1.jsonl"
    refined = tmp_path / "20261009-212603-agy-refined-2.jsonl"
    _write(tmp_path, agy.name, [_usage_update(1, 100)])
    _write(tmp_path, refined.name, [_usage_update(33938, 1048576)])
    now = datetime.now(timezone.utc).timestamp()
    os.utime(agy, (now - 3600, now - 3600))
    os.utime(refined, (now, now))

    accounts = collect_acp(log_dir=tmp_path, max_age_hours=0)

    assert len(accounts) == 1
    assert accounts[0].provider == "antigravity"
    assert accounts[0].account == "agy-refined"
    assert accounts[0].context_usage is not None
    assert accounts[0].context_usage.used == 33938


def test_opencode_note_is_not_the_go_plan_meter(tmp_path):
    _write(
        tmp_path,
        "20261009-010101-opencode-9.jsonl",
        [{"kind": "result", "data": {"usage_update": {"used": 12, "size": 100}}}],
    )

    account = collect_acp(log_dir=tmp_path, max_age_hours=0)[0]

    assert account.provider == "opencode-go"
    assert account.context_usage is not None
    assert account.context_usage.used == 12
    assert "not the OpenCode Go plan meter" in account.notes[0]


def test_agents_without_usage_fields_are_not_readings(tmp_path):
    _write(tmp_path, "20261006-062314-cursor-28584.jsonl", [_prompt()])
    _write(tmp_path, "20261006-152335-qwen-91696.jsonl", [{"kind": "result", "data": {"stop_reason": "end_turn"}}])

    scan = scan_acp_logs(log_dir=tmp_path, max_age_hours=0)

    assert scan.readings == []
    assert "cursor" in scan.agents_without_usage
    assert "qwen" in scan.agents_without_usage
    assert collect_acp(log_dir=tmp_path, max_age_hours=0) == []


def test_stale_logs_stay_out_of_collection(tmp_path):
    _write(tmp_path, "20261001-000000-codex-1.jsonl", [_usage_update(10, 100)])
    path = tmp_path / "20261001-000000-codex-1.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    path.touch()
    os.utime(path, (old, old))

    assert collect_acp(log_dir=tmp_path, max_age_hours=168) == []
    scan = scan_acp_logs(log_dir=tmp_path, max_age_hours=168)
    assert scan.readings[0].stale is True
    assert scan.readings[0].provider == "codex"


def test_blend_attaches_context_and_does_not_compare_it_to_plan_percent():
    quota = AccountUsage(
        source="codexbar",
        provider="codex",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="Codex 5-hour quota", used_percent=10, remaining_percent=90)],
    )
    context_row = AccountUsage(
        source="acp",
        provider="codex",
        account="codex",
        context_usage=None,
    )
    from aiuse.models import ContextUsage

    context_row.context_usage = ContextUsage(
        used=90, size=100, used_percent=90.0, remaining_percent=10.0, agent="codex"
    )
    context_row.notes = ["ACP context from codex: 90 / 100 tokens (90.0% of the context window)."]
    context_row.collected_at = utcnow()

    selected, checks = _select_and_cross_check([quota, context_row], cswap_authoritative=False)

    assert [row.source for row in selected] == ["codexbar"]
    assert selected[0].windows[0].remaining_percent == 90
    assert selected[0].context_usage is not None
    assert selected[0].context_usage.used == 90
    assert all("percentage points" not in check.message for check in checks)

    alerts = analyze_use_or_lose(Snapshot(collected_at=utcnow(), accounts=selected), {})
    assert all("context window" not in alert.window_label for alert in alerts)


def test_acp_only_row_is_kept_and_does_not_burn():
    from aiuse.models import ContextUsage

    row = AccountUsage(
        source="acp",
        provider="antigravity",
        account="agy",
        context_usage=ContextUsage(used=100, size=1000, used_percent=10.0, remaining_percent=90.0, agent="agy"),
        collected_at=utcnow(),
    )
    selected, _checks = _select_and_cross_check([row], cswap_authoritative=False)
    assert [item.source for item in selected] == ["acp"]
    alerts = analyze_use_or_lose(Snapshot(collected_at=utcnow(), accounts=selected), {})
    assert all(alert.kind != "burn" for alert in alerts)
    line = _priority_account_line(selected[0], _Style(False), 0)
    assert "not plan quota" in line
    assert "on pace" not in line
    assert "ACP context" in _context_fragment(selected[0])


def test_blank_openusage_claude_falls_through_to_acp():
    from aiuse.models import ContextUsage

    blank = AccountUsage(
        source="openusage_ai",
        provider="claude",
        plan="Max",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[],
    )
    acp = AccountUsage(
        source="acp",
        provider="claude",
        account="claude",
        context_usage=ContextUsage(used=10, size=100, used_percent=10.0, remaining_percent=90.0, agent="claude"),
        collected_at=utcnow(),
    )

    selected, checks = _select_and_cross_check([blank, acp], cswap_authoritative=False)

    assert [row.source for row in selected] == ["acp"]
    assert selected[0].context_usage is not None
    assert all("percentage points" not in check.message for check in checks)


def test_matrix_context_only_row_is_not_a_plan_clock():
    from aiuse.models import ContextUsage
    from aiuse.report import _BAND_NA, _build_matrix_rows

    row = AccountUsage(
        source="acp",
        provider="antigravity",
        account="agy",
        context_usage=ContextUsage(used=10, size=100, used_percent=10.0, remaining_percent=90.0, agent="agy"),
        collected_at=utcnow(),
    )

    rows = _build_matrix_rows([], Snapshot(collected_at=utcnow(), accounts=[row]), {})

    assert len(rows) == 1
    assert rows[0].band == _BAND_NA
    assert rows[0].clocks == {}
    assert "not plan quota" in (rows[0].note or "")


def test_pin_drops_other_sources_and_missing_pin_is_an_error():
    quota = AccountUsage(
        source="codexbar",
        provider="antigravity",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="Gemini 5-hour", used_percent=20, remaining_percent=80)],
    )
    from aiuse.models import ContextUsage

    acp = AccountUsage(
        source="acp",
        provider="antigravity",
        account="agy",
        context_usage=ContextUsage(used=3, size=10, used_percent=30.0, remaining_percent=70.0, agent="agy"),
        collected_at=utcnow(),
    )
    pinned, _checks = _select_and_cross_check(
        [quota, acp],
        cswap_authoritative=False,
        usage_sources={"gemini": "acp"},
    )
    assert [row.source for row in pinned] == ["acp"]
    assert pinned[0].windows == []

    missing, _checks = _select_and_cross_check(
        [quota],
        cswap_authoritative=False,
        usage_sources={"antigravity": "cswap"},
    )
    assert missing[0].source == "cswap"
    assert missing[0].error is not None
    assert "pins cswap" in missing[0].error


def test_usage_sources_flag_marks_acp_active_without_the_tui(tmp_path):
    _write(tmp_path, "20261009-213010-agy-26889.jsonl", [_usage_update(10, 1000)])
    report = usage_source_report(
        {
            "collectors": {"acp": {"log_dir": str(tmp_path), "max_age_hours": 0}},
            "usage_sources": {},
        },
        which_fn=lambda _name: None,
        environ={},
    )
    antigravity = next(row for row in report["providers"] if row["provider"] == "antigravity")
    assert antigravity["mode"] == "blend"
    by_id = {source["id"]: source for source in antigravity["sources"]}
    assert by_id["acp"]["status"] == "active"
    assert by_id["acp"]["kind"] == "context"
    assert by_id["codexbar"]["status"] == "inactive"
    assert "not on PATH" in by_id["codexbar"]["detail"]
    assert "cursor" in report["acp_agents_without_usage"]

    pinned = usage_source_report(
        {
            "collectors": {"acp": {"log_dir": str(tmp_path), "max_age_hours": 0}},
            "usage_sources": {"antigravity": "acp"},
        },
        which_fn=lambda name: "/usr/bin/" + name,
        environ={},
    )
    antigravity = next(row for row in pinned["providers"] if row["provider"] == "antigravity")
    assert antigravity["mode"] == "pinned"
    by_id = {source["id"]: source for source in antigravity["sources"]}
    assert by_id["acp"]["status"] == "active"
    assert by_id["codexbar"]["status"] == "ignored"


def test_usage_sources_config_rejects_an_unknown_collector():
    assert validate_config({"usage_sources": {"claude": "blend"}}) == []
    assert validate_config({"usage_sources": {"gemini": "acp"}}) == []
    issues = validate_config({"usage_sources": {"claude": "not-a-collector"}})
    assert any(issue.startswith("error:") and "not-a-collector" in issue for issue in issues)


def test_usage_sources_command_prints_json(tmp_path, capsys):
    from aiuse.cli import main

    _write(tmp_path, "20261009-213010-agy-1.jsonl", [_usage_update(4, 8)])
    config = tmp_path / "config.toml"
    config.write_text(
        f'[collectors.acp]\nlog_dir = "{tmp_path}"\nmax_age_hours = 0\n[usage_sources]\nclaude = "cswap"\n',
        encoding="utf-8",
    )

    assert main(["usage-sources", "--config", str(config), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    claude = next(row for row in payload["providers"] if row["provider"] == "claude")
    assert claude["pinned_source"] == "cswap"
    assert any(source["id"] == "acp" and source["status"] == "ignored" for source in claude["sources"])
