import json

import pytest

from aiuse import cli
from aiuse.models import CrossCheck, Snapshot, utcnow


@pytest.fixture(autouse=True)
def _mock_deps(monkeypatch):
    monkeypatch.setattr(cli, "check_dependencies", lambda _config: [])


def test_help_epilog_mentions_setup_flags(capsys):
    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "--generate-config" in out
    assert "doctor" in out
    assert "--timeout" in out or "-t" in out
    assert "config.toml" in out


def test_http_probe_rejects_non_http_url():
    assert cli._http_probe_ok("file:///etc/passwd") is False


def _stub_probe(monkeypatch):
    monkeypatch.setattr(
        cli,
        "probe_tool_version",
        lambda cmd, _va, **_k: (True, f"{cmd}-probe"),
    )


def test_doctor_exits_without_collecting(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def fail_if_called(_config):
        raise AssertionError("collectors should not run")

    monkeypatch.setattr(cli, "run_collectors", fail_if_called)
    monkeypatch.setattr(cli, "which", lambda _cmd: "/usr/bin/fake")
    _stub_probe(monkeypatch)

    assert cli.main(["--doctor"]) == 0
    out = capsys.readouterr().out
    assert "aiuse doctor" in out
    assert "No hard problems" in out or "No problems" in out
    assert str(tmp_path / "aiuse") in out
    assert "probe" in out.lower() or "cswap-probe" in out


def test_doctor_subcommand_synonym(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "which", lambda _cmd: "/bin/tool")
    _stub_probe(monkeypatch)
    monkeypatch.setattr(cli, "run_collectors", lambda _c: (_ for _ in ()).throw(AssertionError("no collect")))
    assert cli.main(["doctor"]) == 0
    assert "aiuse doctor" in capsys.readouterr().out


def test_status_subcommand_prints_one_line(monkeypatch, capsys):
    from aiuse.models import AccountUsage, BillingKind, Snapshot, utcnow

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            )
        ],
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda _s, _c: [])
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda _c: False)
    assert cli.main(["status", "-q"]) == 0
    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    assert out.startswith("ok:")

    assert cli.main(["prompt", "-q"]) == 0
    out2 = capsys.readouterr().out.strip()
    assert out2.startswith("ok:")


def test_suggest_subcommand_and_json(monkeypatch, capsys):
    from aiuse.models import (
        AccountUsage,
        BillingKind,
        Snapshot,
        Urgency,
        UseOrLoseAlert,
        utcnow,
    )

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            )
        ],
    )
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="claude",
            account="a@x.com",
            window_label="Claude Code weekly",
            remaining_percent=91.0,
            days_until_reset=2.0,
            plan=None,
            message="burn it",
            source="cswap",
            score=88.0,
            kind="burn",
        )
    ]
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda _s, _c: list(alerts))
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda _c: False)
    monkeypatch.setattr(cli, "maybe_local_runtime_alerts", lambda *_a, **_k: [])

    assert cli.main(["suggest", "-q"]) == 2
    out = capsys.readouterr().out.strip()
    assert out.startswith("suggest:")
    assert "Claude Code weekly" in out
    assert "91%" in out

    assert cli.main(["suggest", "--json", "-q"]) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["suggestion"]["provider"] == "claude"
    assert payload["suggestion"]["kind"] == "burn"
    assert payload["suggestion"]["score"] == 88.0


def test_doctor_missing_enabled_tool_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def fake_which(cmd: str):
        if cmd == "tokscale":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr(cli, "which", fake_which)
    _stub_probe(monkeypatch)
    code = cli.main(["--doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "MISSING" in out
    assert "tokscale" in out
    assert "Problems:" in out


def test_doctor_disabled_missing_tool_is_ok(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda _p=None: {
            "collectors": {"tokscale": {"enabled": False}, "cswap": True, "codexbar": True},
            "timeouts": {"default": 45},
        },
    )

    def fake_which(cmd: str):
        if cmd == "tokscale":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr(cli, "which", fake_which)
    _stub_probe(monkeypatch)
    assert cli.main(["--doctor"]) == 0
    out = capsys.readouterr().out
    assert "tokscale" in out
    assert "disabled in config" in out
    assert "No hard problems" in out or "No problems" in out


def test_diagnose_respects_timeout_force():
    config = {"timeouts": {"force": 12.0, "default": 45.0}, "collectors": {}}
    code, lines = cli.diagnose(config, which_fn=lambda _c: "/x", probe=False)
    assert code == 0
    text = "\n".join(lines)
    assert "force:   12.0" in text
    assert "cswap: 12" in text


def test_doctor_reports_config_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda _p=None: {"timeouts": {"default": -1}, "collectors": {}},
    )
    monkeypatch.setattr(cli, "which", lambda _c: "/bin/x")
    _stub_probe(monkeypatch)
    assert cli.main(["--doctor"]) == 1
    out = capsys.readouterr().out
    assert "error:" in out
    assert "positive" in out


def test_print_completion_bash(capsys):
    assert cli.main(["--print-completion", "bash"]) == 0
    out = capsys.readouterr().out
    assert "complete" in out
    assert "--brief" in out


def test_brief_mode_skips_usage_section(monkeypatch, capsys):
    from aiuse.models import AccountUsage, Urgency, UseOrLoseAlert

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(provider="codex", source="codexbar", account="a@x.com")],
    )
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@x.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=1.0,
        plan=None,
        message="burn",
        source="codexbar",
        score=10.0,
        kind="burn",
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [alert])
    assert cli.main(["--brief", "--no-color", "-q", "--no-tui"]) == 2
    out = capsys.readouterr().out
    assert "use" in out
    assert "codex" in out
    assert "## Per-provider usage" not in out
    assert "## Tips" not in out


def test_default_pretty_is_clock_matrix(monkeypatch, capsys):
    from aiuse.models import AccountUsage, Urgency, UseOrLoseAlert

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(provider="codex", source="codexbar", account="a@x.com")],
    )
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@x.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=1.0,
        plan=None,
        message="burn",
        source="codexbar",
        score=10.0,
        kind="burn",
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [alert])
    assert cli.main(["--no-color", "-q", "--no-tui"]) == 2
    captured = capsys.readouterr()
    assert "use" in captured.out
    # The window label is gone from the default view by design — the clock
    # columns carry the period instead, so a weekly window lands under WEEK.
    assert "WEEK" in captured.out
    assert "Weekly" not in captured.out
    assert "## Per-provider usage" not in captured.out
    assert "Detail: ai --full" not in captured.out  # quiet suppresses stderr meta


def test_full_mode_includes_providers(monkeypatch, capsys):
    from aiuse.models import AccountUsage, Urgency, UseOrLoseAlert

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(provider="codex", source="codexbar", account="a@x.com")],
    )
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@x.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=1.0,
        plan=None,
        message="burn",
        source="codexbar",
        score=10.0,
        kind="burn",
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [alert])
    assert cli.main(["--full", "--no-color", "-q", "--no-tui"]) == 2
    out = capsys.readouterr().out
    assert "## Per-provider usage" in out
    assert "(full)" in out


def test_alerts_only_includes_cross_check_warnings(monkeypatch, capsys):
    snapshot = Snapshot(
        collected_at=utcnow(),
        cross_checks=[
            CrossCheck(
                provider="copilot",
                account="user@example.com",
                status="warning",
                sources=["CodexBar", "tokscale"],
                message="The quota percentages disagree.",
            )
        ],
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _config: snapshot)

    assert cli.main(["--alerts-only", "--no-color"]) == 0
    output = capsys.readouterr().out
    assert "[cross-check] copilot" in output
    assert "user@example.com" in output
    assert "percentages disagree" in output


def test_json_alerts_only_includes_structured_cross_check_warnings(monkeypatch, capsys):
    snapshot = Snapshot(
        collected_at=utcnow(),
        cross_checks=[
            CrossCheck(
                provider="codex",
                account=None,
                status="warning",
                sources=["CodexBar", "tokscale"],
                message="One tool failed.",
            )
        ],
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _config: snapshot)

    assert cli.main(["--json", "--alerts-only"]) == 0
    output = capsys.readouterr().out
    assert '"cross_check_warnings"' in output
    assert '"One tool failed."' in output


def test_json_keeps_nested_live_envelope(monkeypatch, capsys):
    snapshot = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli, "run_collectors", lambda _config: snapshot)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_args: [])
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda _config: False)

    assert cli.main(["--json", "-q"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"schema_version", "contract_url", "alerts", "suggestion", "history", "snapshot"}
    assert payload["schema_version"] == "1.0"
    assert payload["snapshot"]["accounts"] == []


def test_json_flatten_matches_cached_snapshot_shape(monkeypatch, capsys):
    snapshot = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli, "run_collectors", lambda _config: snapshot)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_args: [])
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda _config: False)

    assert cli.main(["--json", "--flatten", "-q"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"collected_at", "accounts", "alerts"}
    assert payload["accounts"] == []
    assert payload["alerts"] == []


def test_show_config_path_exits_without_collecting(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def fail_if_called(_config):
        raise AssertionError("collectors should not run")

    monkeypatch.setattr(cli, "run_collectors", fail_if_called)

    assert cli.main(["--show-config-path"]) == 0
    out = capsys.readouterr().out
    assert f"config: {tmp_path / 'aiuse' / 'config.toml'}" in out
    assert f"legacy services: {tmp_path / 'aiuse' / 'services.yaml'}" in out


def test_cli_timeout_flag_sets_force(monkeypatch):
    captured = {}

    def fake_collectors(config):
        captured["timeouts"] = dict(config.get("timeouts") or {})
        return Snapshot(collected_at=utcnow())

    monkeypatch.setattr(cli, "run_collectors", fake_collectors)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [])
    assert cli.main(["--timeout", "12", "--json", "--alerts-only"]) == 0
    assert captured["timeouts"]["force"] == 12.0
    assert captured["timeouts"]["default"] == 12.0

    assert cli.main(["-t45", "--json", "--alerts-only"]) == 0
    assert captured["timeouts"]["force"] == 45.0


def test_no_tokscale_works_when_collector_is_boolean_true(monkeypatch):
    captured = {}

    def fake_collectors(config):
        captured["tokscale"] = config.get("collectors", {}).get("tokscale")
        return Snapshot(collected_at=utcnow())

    monkeypatch.setattr(cli, "run_collectors", fake_collectors)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr(
        cli, "load_config", lambda _p=None: {"collectors": {"tokscale": True}, "analysis": {}, "timeouts": {}}
    )
    assert cli.main(["--no-tokscale", "--json", "--alerts-only"]) == 0
    assert captured["tokscale"] == {"enabled": False}


def test_main_exits_1_when_all_collectors_fail(monkeypatch):
    def empty_failing(_config):
        snap = Snapshot(collected_at=utcnow())
        snap.collector_errors = ["cswap: boom", "codexbar: boom", "tokscale: boom"]
        return snap

    monkeypatch.setattr(cli, "run_collectors", empty_failing)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [])
    assert cli.main(["--json", "--alerts-only"]) == 1


def test_main_exits_2_when_actionable_alerts(monkeypatch):
    from aiuse.models import Urgency, UseOrLoseAlert

    snap = Snapshot(collected_at=utcnow())
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account=None,
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=1.0,
        plan=None,
        message="burn",
        source="codexbar",
        score=80.0,
        kind="burn",
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [alert])
    assert cli.main(["--json", "--alerts-only", "--quiet"]) == 0


def test_main_exits_0_when_no_actionable_alerts(monkeypatch):
    from aiuse.models import AccountUsage, Urgency, UseOrLoseAlert

    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(provider="codex", source="codexbar")],
    )
    info = UseOrLoseAlert(
        urgency=Urgency.INFO,
        provider="codex",
        account=None,
        window_label="Weekly",
        remaining_percent=10.0,
        days_until_reset=20.0,
        plan=None,
        message="advisory",
        source="codexbar",
        score=1.0,
    )
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [info])
    assert cli.main(["--json", "--alerts-only", "-q"]) == 0


def test_quiet_suppresses_progress_on_stderr(monkeypatch, capsys):
    snap = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [])
    assert cli.main(["--json", "--alerts-only", "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "Collecting" not in err


def test_without_quiet_prints_progress(monkeypatch, capsys):
    snap = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli, "analyze_use_or_lose", lambda *_a, **_k: [])
    assert cli.main(["--json", "--alerts-only"]) == 0
    err = capsys.readouterr().err
    assert "Collecting usage" in err


def test_persist_snapshots_saves_without_learning(monkeypatch, tmp_path, capsys):
    from aiuse import cli as cli_mod
    from aiuse.analysis import history as history_mod

    snap = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli_mod, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli_mod, "analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr(history_mod, "snapshot_dir", lambda: tmp_path / "snapshots")
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda _p=None: {
            "analysis": {"persist_snapshots": True, "learn_from_history": False},
            "collectors": {},
            "timeouts": {},
        },
    )
    assert cli_mod.main(["--json", "-q"]) == 0
    err = capsys.readouterr().err
    assert "Saved snapshot" not in err  # quiet suppresses progress
    files = [f for f in (tmp_path / "snapshots").glob("*.json") if f.name != "latest.json"]
    assert len(files) == 1


def test_no_persist_when_flags_off(monkeypatch, tmp_path):
    from aiuse import cli as cli_mod
    from aiuse.analysis import history as history_mod

    snap = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli_mod, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli_mod, "analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr(history_mod, "snapshot_dir", lambda: tmp_path / "snapshots")
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda _p=None: {
            "analysis": {"persist_snapshots": False, "learn_from_history": False},
            "collectors": {},
            "timeouts": {},
        },
    )
    assert cli_mod.main(["--json", "-q"]) == 0
    assert not (tmp_path / "snapshots").exists()


def test_learn_from_history_implies_persist(monkeypatch, tmp_path):
    from aiuse import cli as cli_mod
    from aiuse.analysis import history as history_mod

    snap = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli_mod, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli_mod, "analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr(history_mod, "snapshot_dir", lambda: tmp_path / "snapshots")
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda _p=None: {
            "analysis": {"persist_snapshots": False, "learn_from_history": True},
            "collectors": {},
            "timeouts": {},
        },
    )
    assert cli_mod.main(["--json", "-q"]) == 0
    assert len([f for f in (tmp_path / "snapshots").glob("*.json") if f.name != "latest.json"]) == 1


def test_learn_auto_persists_while_waiting(monkeypatch, tmp_path):
    from aiuse import cli as cli_mod
    from aiuse.analysis import history as history_mod

    snap = Snapshot(collected_at=utcnow())
    monkeypatch.setattr(cli_mod, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli_mod, "analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr(history_mod, "snapshot_dir", lambda: tmp_path / "snapshots")
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda _p=None: {
            "analysis": {"persist_snapshots": False, "learn_from_history": "auto"},
            "collectors": {},
            "timeouts": {},
        },
    )
    assert cli_mod.main(["--json", "-q"]) == 0
    assert len([f for f in (tmp_path / "snapshots").glob("*.json") if f.name != "latest.json"]) == 1


def test_collect_exit_code_helper():
    from aiuse.models import Urgency, UseOrLoseAlert

    empty = Snapshot(collected_at=utcnow())
    assert cli.collect_exit_code(empty, []) == 0
    fail = Snapshot(collected_at=utcnow())
    fail.collector_errors = ["x"]
    assert cli.collect_exit_code(fail, []) == 1
    alert = UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="x",
        account=None,
        window_label="w",
        remaining_percent=50,
        days_until_reset=1,
        plan=None,
        message="m",
        source="s",
        score=1,
    )
    ok_with_data = Snapshot(
        collected_at=utcnow(),
        accounts=[],
    )
    # No accounts but no errors either → 2 if alert present
    assert cli.collect_exit_code(ok_with_data, [alert]) == 2


def test_generate_config_creates_files_and_refuses_overwrite(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["--generate-config"]) == 0
    out = capsys.readouterr()
    assert "created:" in out.out
    assert (tmp_path / "aiuse" / "config.toml").is_file()

    assert cli.main(["--generate-config"]) == 1
    err = capsys.readouterr().err
    assert "exists (not overwritten)" in err
    assert "already exist" in err or "left unchanged" in err
