"""--include / --exclude: per-run provider selection overriding config.toml."""

import pytest

from aiuse import cli
from aiuse.collectors.runner import run_collectors
from aiuse.config import canonical_disabled_services, collection_policy_fingerprint
from aiuse.models import AccountUsage, BillingKind, QuotaWindow
from aiuse.provider_select import (
    blocked_reason,
    matches,
    set_selection,
    split_known,
    split_patterns,
)


def _account(provider: str) -> AccountUsage:
    return AccountUsage(
        source="tokscale",
        provider=provider,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="quota", used_percent=10)],
    )


def _selected(include=(), exclude=(), **config):
    config = dict(config)
    set_selection(config, split_patterns(include), split_patterns(exclude))
    return config


def test_split_patterns_flattens_repeats_and_commas():
    assert split_patterns(["Claude, codex", "oc-*,claude", ""]) == ["claude", "codex", "oc-*"]


def test_literals_match_ids_aliases_and_display_names():
    assert matches("antigravity", "agy")
    assert matches("opencode-go", "oc-go")
    assert matches("opencode-go", "opencode-go")
    assert not matches("opencode-go", "oc-zen")


def test_globs_match_id_or_display_name_but_not_aliases():
    assert matches("opencode-zen", "oc-*")
    assert matches("opencode-zen", "opencode-*")
    assert matches("claude", "CLAUDE*")
    # "openai" is an alias of codex and pi: a glob must not reach them through it.
    assert not matches("codex", "open*")
    assert not matches("pi", "open*")


def test_include_overrides_config_disable_and_exclude_wins():
    config = _selected(
        include=["grok", "claude"],
        exclude=["claude"],
        disabled_services={"grok": "vendor shelved", "copilot": "unused"},
    )
    assert canonical_disabled_services(config) == {"copilot": "unused"}
    assert blocked_reason(config, "grok") is None
    assert blocked_reason(config, "claude") is not None
    assert blocked_reason(config, "codex") is not None  # not in the allow-list


def test_exclude_adds_to_config_disables_without_replacing_them():
    config = _selected(exclude=["cursor"], disabled_services={"grok": "vendor shelved"})
    included, excluded = split_known(config)
    assert {"grok", "cursor"} <= set(excluded)
    assert "claude" in included


def test_fingerprint_changes_only_when_a_flag_is_given():
    plain = {"disabled_services": {"grok": "x"}}
    assert collection_policy_fingerprint(plain) == collection_policy_fingerprint(_selected(**plain))
    assert collection_policy_fingerprint(plain) != collection_policy_fingerprint(_selected(exclude=["claude"], **plain))


def _run_only(monkeypatch, *names, **config):
    """run_collectors with exactly these collectors on (a real collector must never run in a test)."""
    import aiuse.collectors.runner as runner

    monkeypatch.setattr(runner, "_enabled", lambda _cfg, name: name in names)
    return run_collectors(config)


def test_run_collectors_drops_flagged_rows_even_for_unregistered_providers(monkeypatch):
    import aiuse.collectors.runner as runner

    monkeypatch.setattr(
        runner,
        "collect_tokscale",
        lambda **_kw: [_account("claude"), _account("codex"), _account("newvendor")],
    )
    only = _run_only(monkeypatch, "tokscale", **_selected(include=["claude"]))
    assert [a.provider for a in only.accounts] == ["claude"]
    without = _run_only(monkeypatch, "tokscale", **_selected(exclude=["new*"]))
    assert [a.provider for a in without.accounts] == ["claude", "codex"]
    # The flags are not operator disables: they are not reported as such.
    assert only.disabled_services == {}


def test_include_runs_collector_the_config_disabled(monkeypatch):
    import aiuse.collectors.runner as runner

    monkeypatch.setattr(runner, "collect_grok", lambda **_kw: [_account("grok")])
    shelved = {"disabled_services": {"grok": "shelved"}}
    assert _run_only(monkeypatch, "grok_billing", **shelved).accounts == []
    revived = _run_only(monkeypatch, "grok_billing", **_selected(include=["grok"], **shelved))
    assert [a.provider for a in revived.accounts] == ["grok"]
    assert revived.disabled_services == {}


def _write_config(tmp_path, monkeypatch, text):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "aiuse" / "config.toml"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")


def _no_collect(monkeypatch):
    def fail(_config):
        raise AssertionError("collectors should not run")

    monkeypatch.setattr(cli, "run_collectors", fail)


def test_show_flags_print_comma_lists_without_collecting(monkeypatch, tmp_path, capsys):
    _write_config(tmp_path, monkeypatch, '[disabled_services]\ngrok = "shelved"\n')
    _no_collect(monkeypatch)
    assert cli.main(["--show-excluded"]) == 0
    assert "grok" in capsys.readouterr().out.strip().split(",")
    assert cli.main(["--exclude", "claude", "--show-included"]) == 0
    included = capsys.readouterr().out.strip().split(",")
    assert "codex" in included
    assert "claude" not in included and "grok" not in included


def test_show_included_list_previews_it_as_an_include(monkeypatch, tmp_path, capsys):
    _write_config(tmp_path, monkeypatch, '[disabled_services]\ngrok = "shelved"\n')
    _no_collect(monkeypatch)
    assert cli.main(["--exclude", "codex", "--show-included", "grok,cod*,claude"]) == 0
    assert capsys.readouterr().out.strip() == "claude,grok"


def test_both_show_flags_are_labelled(monkeypatch, tmp_path, capsys):
    _write_config(tmp_path, monkeypatch, "")
    _no_collect(monkeypatch)
    assert cli.main(["--include", "claude", "--show-included", "--show-excluded"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "included: claude"
    assert "claude" not in lines[1].removeprefix("excluded: ").split(",")


def test_unmatched_pattern_warns_and_empty_list_is_an_error(monkeypatch, tmp_path, capsys):
    _write_config(tmp_path, monkeypatch, "")
    _no_collect(monkeypatch)
    assert cli.main(["--include", "claud", "--show-included"]) == 0
    assert "matches no known provider" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--include", " , ", "--show-included"])


def test_serve_and_sample_reject_selection(monkeypatch, tmp_path, capsys):
    _write_config(tmp_path, monkeypatch, "")
    assert cli.main(["--include", "claude", "--sample"]) == 2
    assert "do not apply" in capsys.readouterr().err


def test_watch_frame_with_a_selection_neither_reads_nor_writes_disk(monkeypatch):
    from aiuse import watch
    from aiuse.models import Snapshot, utcnow

    def touched(*_a, **_kw):
        raise AssertionError("a narrowed watch frame must stay screen-only")

    monkeypatch.setattr(watch, "_frame_from_disk", touched)
    monkeypatch.setattr(watch, "save_snapshot", touched)
    monkeypatch.setattr(watch.sampler, "collect_with_ledger", touched)
    monkeypatch.setattr(
        watch, "run_collectors", lambda _cfg: Snapshot(collected_at=utcnow(), accounts=[_account("claude")])
    )
    config = _selected(include=["claude"], analysis={"persist_snapshots": True})
    snapshot, _alerts = watch.collect_watch_frame(config, max_age=600)
    assert [a.provider for a in snapshot.accounts] == ["claude"]


def test_watch_all_providers_sweep_ignores_the_selection():
    from aiuse.watch import all_providers_config

    config = _selected(include=["claude"], exclude=["codex"], disabled_services={"grok": "shelved"})
    sweep = all_providers_config(config)
    assert "provider_selection" not in sweep and "disabled_services" not in sweep
    assert config["provider_selection"]["include"] == ["claude"]  # the live config is untouched
