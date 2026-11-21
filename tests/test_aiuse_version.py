"""aiuse_version: every collection records the build that took it (schema 1.7).

The version is what lets history be corrected retroactively once a build is
found buggy (docs/data-corrections.md), so these tests pin where it is stamped,
where it is persisted, that an old record without it still loads, and that a
loaded snapshot shows the RECORDED build rather than the running one.
"""

from __future__ import annotations

import json
from datetime import timedelta

from aiuse import __version__, cli
from aiuse.analysis import history
from aiuse.analysis.selfdescribe import SCHEMA_VERSION, SEMANTICS
from aiuse.collectors import runner
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow
from aiuse.report import render_report, render_stderr_meta
from aiuse.serve import _snapshot_from_accounts_dict
from aiuse.tui.builders import build_report_sections

_ALL_COLLECTORS = (
    "cswap",
    "codexbar",
    "grok_billing",
    "caut",
    "caam",
    "openusage_ai",
    "openusage_sh",
    "opencode_go",
    "opencode_zen",
    "openrouter",
    "deepseek",
    "sipb",
    "tokscale",
    "clinepass",
    "hermes",
    "muse",
    "qwencloud",
    "bailian",
    "paseo",
    "acp",
)


def _account() -> AccountUsage:
    return AccountUsage(
        source="codexbar",
        provider="codex",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="Codex weekly", used_percent=10.0, remaining_percent=90.0, window_minutes=10080)],
    )


def _snap(version: str | None = None) -> Snapshot:
    return Snapshot(collected_at=utcnow(), accounts=[_account()], aiuse_version=version)


def test_schema_is_1_7_and_semantics_describe_the_field():
    assert SCHEMA_VERSION == "1.7"
    assert "COLLECTED" in SEMANTICS["aiuse_version"]


def test_runner_stamps_the_running_version_on_a_fresh_collection():
    snapshot = runner.run_collectors({"collectors": {name: {"enabled": False} for name in _ALL_COLLECTORS}})
    assert snapshot.aiuse_version == __version__
    assert snapshot.to_dict()["aiuse_version"] == __version__


def test_snapshot_to_dict_omits_an_unknown_version():
    assert "aiuse_version" not in _snap().to_dict()
    assert _snap("5.1.3").to_dict()["aiuse_version"] == "5.1.3"


def test_loader_restores_the_recorded_version_and_tolerates_old_rows():
    recorded = _snapshot_from_accounts_dict({"collected_at": utcnow().isoformat(), "aiuse_version": "5.1.3"})
    assert recorded.aiuse_version == "5.1.3"
    # A record written by 5.1.3 or earlier has no field; junk values are not trusted.
    assert _snapshot_from_accounts_dict({"collected_at": utcnow().isoformat()}).aiuse_version is None
    assert (
        _snapshot_from_accounts_dict({"collected_at": utcnow().isoformat(), "aiuse_version": 7}).aiuse_version is None
    )
    assert (
        _snapshot_from_accounts_dict({"collected_at": utcnow().isoformat(), "aiuse_version": ""}).aiuse_version is None
    )


def test_save_snapshot_persists_the_version_in_every_copy(tmp_path):
    path = history.save_snapshot(_snap("5.1.3"), [])
    data = json.loads(path.read_text())
    assert data["aiuse_version"] == "5.1.3"
    assert data["snapshot"]["aiuse_version"] == "5.1.3"
    assert data["schema_version"] == "1.7"
    latest = json.loads((history.snapshot_dir() / "latest.json").read_text())
    assert latest["aiuse_version"] == "5.1.3"
    burst = history.save_snapshot(_snap("5.1.3"), [], partial_providers=["codex"])
    assert json.loads(burst.read_text())["aiuse_version"] == "5.1.3"


def test_save_snapshot_records_null_for_a_snapshot_with_no_known_build():
    data = json.loads(history.save_snapshot(_snap(), []).read_text())
    assert "aiuse_version" in data and data["aiuse_version"] is None


def test_old_snapshot_file_without_the_field_loads_and_renders_unknown():
    from aiuse.watch import collect_watch_frame, render_watch_board

    path = history.save_snapshot(_snap("5.1.3"), [])
    data = json.loads(path.read_text())
    del data["aiuse_version"]
    del data["snapshot"]["aiuse_version"]
    path.write_text(json.dumps(data))
    (history.snapshot_dir() / "latest.json").write_text(json.dumps(data))
    snapshot, _alerts = collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600)
    assert snapshot.aiuse_version is None
    board = render_watch_board(snapshot, [], color=False, width=200)
    assert "Collected by aiuse ? at" in board


def test_replayed_snapshot_shows_the_recorded_build_not_the_running_one():
    from aiuse.watch import collect_watch_frame, render_watch_board

    history.save_snapshot(_snap("4.0.0"), [])
    snapshot, _alerts = collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600)
    assert snapshot.aiuse_version == "4.0.0"
    board = render_watch_board(snapshot, [], color=False, width=200)
    assert "Collected by aiuse 4.0.0 at" in board
    assert f"aiuse {__version__} at" not in board


def test_meta_line_shows_the_collecting_build_on_every_report_surface():
    snap = _snap("5.1.3")
    assert "Collected by aiuse 5.1.3 at" in render_stderr_meta(snap, [], color=False)
    assert "Collected by aiuse 5.1.3 at" in render_report(snap, [], config={}, color=False, full=True)
    header = next(s for s in build_report_sections(snap, [], config={}) if s.kind == "header")
    assert any("Collected by aiuse 5.1.3 at" in line for line in header.lines)
    assert "Collected by aiuse ? at" in render_stderr_meta(_snap(), [], color=False)
    assert "Collected at" not in render_stderr_meta(snap, [], color=False)


def test_chat_header_shows_the_build():
    from aiuse.chat_format import render_chat_report

    assert "aiuse 5.1.3" in render_chat_report(_snap("5.1.3"), []).splitlines()[0]
    assert "aiuse ?" in render_chat_report(_snap(), []).splitlines()[0]


def test_hint_says_aiuse_full_not_ai_full():
    meta = render_stderr_meta(_snap("5.1.3"), [], color=False)
    assert "Detail: aiuse --full" in meta
    assert "Detail: ai --full" not in meta


def test_json_envelope_carries_the_collecting_build(monkeypatch, capsys):
    snapshot = _snap("5.1.3")
    monkeypatch.setattr(cli, "check_dependencies", lambda _config: [])
    monkeypatch.setattr(cli, "run_collectors", lambda _config: snapshot)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_args: [])
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda _config: False)
    assert cli.main(["--json", "-q"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1.7"
    assert payload["aiuse_version"] == "5.1.3"
    assert payload["snapshot"]["aiuse_version"] == "5.1.3"


def test_json_alerts_only_and_flatten_carry_the_version(monkeypatch, capsys):
    snapshot = _snap("5.1.3")
    monkeypatch.setattr(cli, "check_dependencies", lambda _config: [])
    monkeypatch.setattr(cli, "run_collectors", lambda _config: snapshot)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_args: [])
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda _config: False)
    assert cli.main(["--json", "--alerts-only", "-q"]) == 0
    assert json.loads(capsys.readouterr().out)["aiuse_version"] == "5.1.3"
    assert cli.main(["--json", "--flatten", "-q"]) == 0
    assert json.loads(capsys.readouterr().out)["aiuse_version"] == "5.1.3"


def test_available_envelope_reports_the_cache_files_collector(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    cache.mkdir()
    row = json.loads(history.save_snapshot(_snap("5.1.3"), []).read_text())
    (cache / "latest.json").write_text(json.dumps(row))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)
    assert cli.main(["--available", "--json", "-q"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1.7"
    assert payload["aiuse_version"] == "5.1.3"  # the collector's build, not the running one's


def test_available_envelope_is_null_for_a_cache_that_predates_the_field(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    cache.mkdir()
    row = json.loads(history.save_snapshot(_snap("5.1.3"), []).read_text())
    row.pop("aiuse_version")
    row["snapshot"].pop("aiuse_version")
    (cache / "latest.json").write_text(json.dumps(row))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)
    assert cli.main(["--available", "--json", "-q"]) == 0
    assert json.loads(capsys.readouterr().out)["aiuse_version"] is None


def test_available_text_mode_names_the_collecting_build(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    cache.mkdir()
    row = json.loads(history.save_snapshot(_snap("5.1.3"), []).read_text())
    (cache / "latest.json").write_text(json.dumps(row))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)
    cli.main(["--available"])
    assert "collected by aiuse 5.1.3" in capsys.readouterr().err
    row.pop("aiuse_version")
    row["snapshot"].pop("aiuse_version")
    (cache / "latest.json").write_text(json.dumps(row))
    cli.main(["--available"])
    assert "collected by aiuse ?" in capsys.readouterr().err


def test_serve_snapshot_payload_from_disk_keeps_the_recorded_build():
    from aiuse.serve import _payload_from_disk_row

    row = json.loads(history.save_snapshot(_snap("4.0.0"), []).read_text())
    payload = _payload_from_disk_row(row, config={})
    assert payload["snapshot"]["aiuse_version"] == "4.0.0"
    row.pop("aiuse_version")
    assert "aiuse_version" not in _payload_from_disk_row(row, config={})["snapshot"]


def test_ledger_samples_record_the_build():
    from aiuse import ledger

    sample = ledger.capture_tokscale_ledger(previous=None, now=utcnow(), run=lambda *_a, **_k: {"entries": []})
    assert sample["aiuse_version"] == __version__
    # A reused recent sample is re-stamped by the build that wrote it.
    reused = ledger.capture_tokscale_ledger(previous=sample, now=utcnow(), run=lambda *_a, **_k: {"entries": []})
    assert reused["aiuse_version"] == __version__


def test_attribute_report_names_its_build_and_the_collecting_builds(tmp_path, monkeypatch):
    from aiuse import attribute, ledger

    monkeypatch.setattr(ledger, "ledger_dir", lambda: tmp_path / "ledger")
    now = utcnow()
    older = _snap("5.1.3")
    older.collected_at = now - timedelta(minutes=40)
    history.save_snapshot(older, [])
    unknown = _snap()
    unknown.collected_at = now - timedelta(minutes=20)
    history.save_snapshot(unknown, [])
    report = attribute.build_report({}, now - timedelta(hours=1), now, litellm_query=lambda *_a, **_k: [])
    assert report["aiuse_version"] == __version__
    assert report["coverage"]["collected_by"] == {"5.1.3": 1, "unknown": 1}
    text = attribute.render_report(report)
    assert text.splitlines()[-1] == f"aiuse {__version__} · samples collected by aiuse 5.1.3 x1, ? x1"


def test_usage_sources_report_and_text_name_the_build():
    from aiuse.usage_sources import render_usage_sources, usage_source_report

    report = usage_source_report({})
    assert report["aiuse_version"] == __version__
    assert render_usage_sources(report).splitlines()[0] == f"usage sources (aiuse {__version__})"


def test_gate_status_json_and_text_name_the_build(capsys):
    from aiuse.rate_gates import gate_status, run_gate_command

    assert gate_status()["aiuse_version"] == __version__
    assert run_gate_command(["status"]) == 0
    captured = capsys.readouterr()
    assert f"aiuse {__version__}" in captured.err
    assert "aiuse gate:" in captured.out
    assert run_gate_command(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["aiuse_version"] == __version__
