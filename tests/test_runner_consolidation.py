import time

from aiuse.collectors.runner import _consolidate_accounts, _select_and_cross_check, run_collectors
from aiuse.models import AccountUsage, BillingKind, QuotaWindow


def _account(source: str, provider: str, *, error: str | None = None) -> AccountUsage:
    return AccountUsage(
        source=source,
        provider=provider,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[] if error else [QuotaWindow(label="quota", used_percent=10)],
        error=error,
    )


def test_cswap_is_only_claude_authority_when_enabled():
    accounts = _consolidate_accounts(
        [
            _account("cswap", "claude"),
            _account("codexbar", "claude"),
        ],
        cswap_authoritative=True,
    )
    assert [(account.source, account.provider) for account in accounts] == [("cswap", "claude")]


def test_tokscale_is_removed_when_codexbar_has_live_provider_data():
    accounts = _consolidate_accounts(
        [
            _account("codexbar", "codex"),
            _account("tokscale", "codex"),
            _account("codexbar", "grok"),
            _account("tokscale", "grok-build"),
        ],
        cswap_authoritative=True,
    )
    assert [account.source for account in accounts] == ["codexbar", "codexbar"]


def test_tokscale_remains_as_fallback_after_codexbar_error():
    accounts, checks = _select_and_cross_check(
        [
            _account("codexbar", "copilot", error="failed"),
            _account("tokscale", "copilot"),
        ],
        cswap_authoritative=True,
    )
    assert [account.source for account in accounts] == ["tokscale"]
    assert checks[0].status == "warning"


def test_copilot_prefers_tokscale_over_codexbar_when_both_live():
    accounts, _checks = _select_and_cross_check(
        [
            _account("codexbar", "copilot"),
            _account("tokscale", "copilot"),
        ],
        cswap_authoritative=True,
    )
    assert [account.source for account in accounts] == ["tokscale"]


def test_cross_check_reports_consistent_duplicate_measurements():
    codexbar = _account("codexbar", "codex")
    tokscale = _account("tokscale", "codex")
    codexbar.windows[0].label = "Codex weekly quota"
    tokscale.windows[0].label = "Codex weekly quota"
    accounts, checks = _select_and_cross_check([codexbar, tokscale], cswap_authoritative=True)
    assert [account.source for account in accounts] == ["codexbar"]
    assert any(c.status == "consistent" for c in checks)


def test_opencode_zen_native_source_cross_checks_codexbar_but_keeps_codexbar_selected():
    codexbar = AccountUsage(
        source="codexbar",
        provider="opencode-zen",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=4.25,
    )
    native = AccountUsage(
        source="opencode_zen",
        provider="opencode-zen",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=4.25,
    )

    accounts, checks = _select_and_cross_check([codexbar, native], cswap_authoritative=True)

    assert [(account.source, account.provider) for account in accounts] == [("codexbar", "opencode-zen")]
    check = next(check for check in checks if check.provider == "opencode-zen")
    assert check.status == "consistent"
    assert check.sources == ["CodexBar", "OpenCode Zen (native)"]


def test_cross_check_warns_when_percentages_disagree():
    codexbar = _account("codexbar", "codex")
    tokscale = _account("tokscale", "codex")
    codexbar.windows[0].label = "Codex weekly quota"
    tokscale.windows[0].label = "Codex weekly quota"
    tokscale.windows[0].used_percent = 30
    accounts, checks = _select_and_cross_check([codexbar, tokscale], cswap_authoritative=True)
    assert [account.source for account in accounts] == ["codexbar"]
    warn = next(c for c in checks if c.status == "warning")
    assert "percentage points" in warn.message


def test_opencode_go_native_expired_beats_codexbar_local_estimate():
    native = AccountUsage(
        source="opencode_go",
        provider="opencode-go",
        plan="expired",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="OpenCode Go", remaining_percent=0.0, reset_description="subscription expired")],
        notes=["OpenCode Go has no active subscription (expired or not renewed)."],
    )
    local = _account("codexbar", "opencode-go")
    local.notes = [
        "Live data fetched by CodexBar via local.",
        "OpenCode Go local estimate (SQLite costs vs fixed caps) — may diverge "
        "from the official Go limit; prefer CodexBar web when available.",
    ]
    local.windows[0].label = "OpenCode Go monthly quota (3)"
    local.windows[0].used_percent = 1.9
    local.windows[0].remaining_percent = 98.1

    accounts, checks = _select_and_cross_check([local, native], cswap_authoritative=True)
    assert [(account.source, account.plan) for account in accounts] == [("opencode_go", "expired")]
    warn = next(check for check in checks if check.status == "warning")
    assert "local cost estimate" in warn.message.casefold()


def test_opencode_cross_check_names_local_estimate_when_openusage_disagrees():
    """CodexBar web monthly 0% vs OpenUsage estimated ~19% — prefer web billing."""
    codexbar = _account("codexbar", "opencode-go")
    codexbar.notes = ["Live data fetched by CodexBar via web."]
    codexbar.windows[0].label = "OpenCode Go monthly quota (3)"
    codexbar.windows[0].used_percent = 100.0
    codexbar.windows[0].remaining_percent = 0.0
    codexbar.windows[0].window_minutes = 43200

    openusage = _account("openusage_ai", "opencode-go")
    openusage.notes = [
        "Live data fetched by OpenUsage via cli.",
        "OpenUsage marked estimated (local cost vs fixed $ caps): session, weekly, monthly. "
        "May understate used quota versus official OpenCode web billing.",
    ]
    openusage.windows[0].label = "OpenCode monthly"
    openusage.windows[0].used_percent = 80.6
    openusage.windows[0].remaining_percent = 19.4
    openusage.windows[0].window_minutes = 43200
    openusage.windows[0].raw = {"estimated": True, "unit": "usd", "limit": 60}

    accounts, checks = _select_and_cross_check([codexbar, openusage], cswap_authoritative=True)
    assert [account.source for account in accounts] == ["codexbar"]
    warn = next(c for c in checks if c.status == "warning" and c.provider == "opencode-go")
    assert "local cost estimate" in warn.message.casefold()
    assert "OpenUsage.ai" in warn.message
    assert "CodexBar" in warn.message
    assert "percentage points" in warn.message


def test_7d_window_label_matches_weekly_window():
    codexbar = _account("codexbar", "codex")
    codexbar.windows[0].label = "Codex weekly quota"
    openusage = _account("openusage_sh", "codex")
    openusage.windows[0].label = "weekly"

    _accounts, checks = _select_and_cross_check([codexbar, openusage], cswap_authoritative=True)

    assert any(check.status == "consistent" for check in checks)


def test_multi_source_prefers_codexbar_and_cross_checks_all_peers():
    """caut + openusage + tokscale peers cross-check; only codexbar is selected."""
    codexbar = _account("codexbar", "codex")
    caut = _account("caut", "codex")
    openusage = _account("openusage_ai", "codex")
    tokscale = _account("tokscale", "codex")
    for row in (codexbar, caut, openusage, tokscale):
        row.windows[0].label = "weekly"
        row.windows[0].used_percent = 10
    accounts, checks = _select_and_cross_check(
        [codexbar, caut, openusage, tokscale],
        cswap_authoritative=True,
    )
    assert [a.source for a in accounts] == ["codexbar"]
    # All pairs among 4 sources → C(4,2) = 6 pair comparisons
    assert len([c for c in checks if c.status == "consistent"]) >= 3
    sources_seen = {tuple(c.sources) for c in checks if c.status == "consistent"}
    assert any("CodexBar" in s and "caut" in s for s in sources_seen)
    assert any("OpenUsage.ai" in s for s in sources_seen)


def test_caut_selected_when_codexbar_missing():
    caut = _account("caut", "codex")
    tokscale = _account("tokscale", "codex")
    accounts, _ = _select_and_cross_check([caut, tokscale], cswap_authoritative=True)
    assert [a.source for a in accounts] == ["caut"]


def test_claude_cross_check_matches_accounts_case_insensitively():
    cswap_row = _account("cswap", "claude")
    cswap_row.account = "User@Example.com"
    cswap_row.windows[0].label = "Claude Code weekly"
    codexbar_row = _account("codexbar", "claude")
    codexbar_row.account = "user@example.com"
    codexbar_row.windows[0].label = "Claude Code weekly"
    codexbar_row.windows[0].used_percent = 50

    accounts, checks = _select_and_cross_check([cswap_row, codexbar_row], cswap_authoritative=True)

    assert [account.source for account in accounts] == ["cswap"]
    assert checks[0].status == "warning"
    assert "percentage points" in checks[0].message


def test_single_account_per_source_is_normalized_automatically():
    codexbar = _account("codexbar", "codex")
    codexbar.account = "me@example.com"
    openusage = _account("openusage_sh", "codex")
    openusage.account = "codex-cli"
    anonymous = _account("openusage_ai", "codex")

    selected, checks = _select_and_cross_check([codexbar, openusage, anonymous], cswap_authoritative=True)

    assert selected[0].account == "me@example.com"
    assert openusage.account == "me@example.com"
    assert not any("account identifiers differ" in check.message for check in checks)


def test_multi_account_alias_is_not_guessed_and_gives_toml_hint():
    codexbar_one = _account("codexbar", "codex")
    codexbar_one.account = "one@example.com"
    codexbar_two = _account("codexbar", "codex")
    codexbar_two.account = "two@example.com"
    openusage = _account("openusage_sh", "codex")
    openusage.account = "codex-cli"

    _selected, checks = _select_and_cross_check([codexbar_one, codexbar_two, openusage], cswap_authoritative=True)

    assert openusage.account == "codex-cli"
    assert any("[account_aliases.codex.openusage_sh]" in check.message for check in checks)


def test_explicit_multi_account_alias_is_applied():
    codexbar_one = _account("codexbar", "codex")
    codexbar_one.account = "one@example.com"
    codexbar_two = _account("codexbar", "codex")
    codexbar_two.account = "two@example.com"
    openusage = _account("openusage_sh", "codex")
    openusage.account = "codex-cli"

    _selected, _checks = _select_and_cross_check(
        [codexbar_one, codexbar_two, openusage],
        cswap_authoritative=True,
        account_aliases={"codex": {"openusage_sh": {"codex-cli": "one@example.com"}}},
    )

    assert openusage.account == "one@example.com"


def test_claude_gets_cross_checked_when_cswap_disabled():
    codexbar_row = _account("codexbar", "claude")
    tokscale_row = _account("tokscale", "claude")
    codexbar_row.windows[0].used_percent = 5
    tokscale_row.windows[0].used_percent = 90

    accounts, checks = _select_and_cross_check([codexbar_row, tokscale_row], cswap_authoritative=False)

    assert [account.source for account in accounts] == ["codexbar"]
    assert checks
    assert checks[0].status == "warning"


def test_run_collectors_runs_sources_concurrently_not_sequentially(monkeypatch):
    def slow_cswap(**_kwargs):
        time.sleep(1)
        return [_account("cswap", "claude")]

    def slow_codexbar(**_kwargs):
        time.sleep(1)
        return [_account("codexbar", "codex")]

    def slow_tokscale(**_kwargs):
        time.sleep(1)
        return [_account("tokscale", "grok")]

    monkeypatch.setattr("aiuse.collectors.runner.collect_cswap", slow_cswap)
    monkeypatch.setattr("aiuse.collectors.runner.collect_codexbar", slow_codexbar)
    monkeypatch.setattr("aiuse.collectors.runner.collect_tokscale", slow_tokscale)
    monkeypatch.setattr("aiuse.collectors.runner.collect_caut", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_openusage_ai", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_openusage_sh", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_opencode_zen", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_opencode_go", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_clinepass", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_openrouter", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_hermes", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_muse", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_qwencloud", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_bailian", lambda **_k: [])

    start = time.monotonic()
    snapshot = run_collectors({})
    elapsed = time.monotonic() - start

    # Concurrent execution takes about one second; sequential execution takes
    # at least three. Keep a full second of scheduler headroom.
    assert elapsed < 2
    assert {account.provider for account in snapshot.accounts} == {"claude", "codex", "grok"}
    assert snapshot.collector_errors == []


def test_run_collectors_keeps_other_sources_when_one_raises(monkeypatch):
    def failing_cswap(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("aiuse.collectors.runner.collect_cswap", failing_cswap)
    monkeypatch.setattr(
        "aiuse.collectors.runner.collect_codexbar",
        lambda **_kwargs: [_account("codexbar", "codex")],
    )
    monkeypatch.setattr(
        "aiuse.collectors.runner.collect_tokscale",
        lambda **_kwargs: [_account("tokscale", "grok")],
    )
    monkeypatch.setattr("aiuse.collectors.runner.collect_caut", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_openusage_ai", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_openusage_sh", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_opencode_zen", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_opencode_go", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_clinepass", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_openrouter", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_hermes", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_muse", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_qwencloud", lambda **_k: [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_bailian", lambda **_k: [])
    snapshot = run_collectors({})

    assert {account.provider for account in snapshot.accounts} == {"codex", "grok"}
    assert snapshot.collector_errors == ["cswap: boom"]


def test_more_than_two_cswap_claude_accounts_all_survive_selection():
    accounts = [_account("cswap", "claude") for _ in range(4)]
    for i, account in enumerate(accounts):
        account.account = f"user{i}@example.com"

    selected, checks = _select_and_cross_check(accounts, cswap_authoritative=True)

    assert {account.account for account in selected} == {f"user{i}@example.com" for i in range(4)}
    # Single-source provider gets one multi-tool unavailability note (not per account).
    assert any(check.status == "unavailable" for check in checks)
    assert any("only by cswap" in check.message for check in checks)


def test_claude_falls_back_to_codexbar_when_cswap_has_no_live_data():
    cswap_err = _account("cswap", "claude", error="cswap empty")
    cswap_err.account = "a@example.com"
    codexbar = _account("codexbar", "claude")
    codexbar.account = "a@example.com"
    codexbar.windows[0].label = "Claude Code weekly"

    selected, checks = _select_and_cross_check([cswap_err, codexbar], cswap_authoritative=True)

    assert [account.source for account in selected] == ["codexbar"]
    assert any(check.status == "warning" and "falling back" in check.message for check in checks)


def test_claude_falls_back_to_tokscale_when_cswap_and_codexbar_empty():
    cswap_err = _account("cswap", "claude", error="no data")
    tokscale = _account("tokscale", "claude")
    tokscale.windows[0].label = "Session"

    selected, checks = _select_and_cross_check([cswap_err, tokscale], cswap_authoritative=True)

    assert [account.source for account in selected] == ["tokscale"]
    assert any("falling back" in check.message for check in checks)


def test_claude_keeps_cswap_error_when_no_alternate_live_source():
    cswap_err = _account("cswap", "claude", error="no data")
    selected, checks = _select_and_cross_check([cswap_err], cswap_authoritative=True)
    assert [account.source for account in selected] == ["cswap"]
    assert any("falling back" in check.message for check in checks)


def test_claude_cross_check_includes_tokscale_when_cswap_live():
    cswap_row = _account("cswap", "claude")
    cswap_row.account = "user@example.com"
    cswap_row.windows[0].label = "Claude Code weekly"
    cswap_row.windows[0].used_percent = 10
    tokscale_row = _account("tokscale", "claude")
    tokscale_row.account = "user@example.com"
    tokscale_row.windows[0].label = "Claude Code weekly"
    tokscale_row.windows[0].used_percent = 40

    _selected, checks = _select_and_cross_check([cswap_row, tokscale_row], cswap_authoritative=True)

    assert any(
        check.status == "warning" and "percentage points" in check.message and "tokscale" in check.sources
        for check in checks
    )


def test_errored_cswap_with_matching_codexbar_email_gets_specific_warning():
    # Keep one live cswap account so selection stays on cswap rows (not global fallback).
    cswap_ok = _account("cswap", "claude")
    cswap_ok.account = "other@example.com"
    cswap_err = _account("cswap", "claude", error="token expired")
    cswap_err.account = "user@example.com"
    codexbar_row = _account("codexbar", "claude")
    codexbar_row.account = "user@example.com"
    codexbar_row.windows[0].label = "Claude weekly"
    codexbar_row.windows[0].used_percent = 50

    _selected, checks = _select_and_cross_check([cswap_ok, cswap_err, codexbar_row], cswap_authoritative=True)
    messages = [c.message for c in checks if c.account == "user@example.com"]
    assert messages, checks
    assert any("could not read canonical usage" in m for m in messages)
    assert any("do not replace" in m.lower() or "do not substitute" in m.lower() for m in messages)
    assert not any("reporting inconsistency" in m.lower() for m in messages)


def _stale_claude_slot(account: str) -> AccountUsage:
    """A not-renewed slot the way cswap serves it: stale lastGood windows."""
    return AccountUsage(
        source="cswap",
        provider="claude",
        account=account,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(label="Claude Code 5-hour", used_percent=5.0, resets_at=None),
            QuotaWindow(label="Claude Code weekly", used_percent=89.0, remaining_percent=11.0),
        ],
        notes=["cswap slot 1"],
    )


def test_lapsed_accounts_rule_empties_only_the_declared_account():
    from aiuse.collectors.runner import _apply_lapsed_accounts

    mit = _stale_claude_slot("djbclark@mit.edu")
    gmail = _stale_claude_slot("djbclark@gmail.com")
    config = {"analysis": {"lapsed_accounts": {"claude/djbclark@mit.edu": "not renewed 2026-08"}}}

    _apply_lapsed_accounts([mit, gmail], config)

    assert mit.plan == "expired"
    assert mit.error is None
    assert len(mit.windows) == 1
    assert mit.windows[0].remaining_percent == 0.0
    assert mit.windows[0].resets_at is None
    assert "not renewed 2026-08" in (mit.windows[0].reset_description or "")
    assert any("lapsed_accounts" in note for note in mit.notes)

    assert gmail.plan is None
    assert len(gmail.windows) == 2
    assert gmail.windows[1].remaining_percent == 11.0


def test_lapsed_accounts_true_value_uses_default_description():
    from aiuse.collectors.runner import _apply_lapsed_accounts

    mit = _stale_claude_slot("djbclark@mit.edu")
    _apply_lapsed_accounts([mit], {"analysis": {"lapsed_accounts": {"claude/djbclark@mit.edu": True}}})

    assert mit.windows[0].reset_description == "subscription not renewed"


def test_lapsed_accounts_matches_provider_aliases_and_case_insensitive_account():
    from aiuse.collectors.runner import _apply_lapsed_accounts

    row = _stale_claude_slot("DJBclark@MIT.edu")
    _apply_lapsed_accounts([row], {"analysis": {"lapsed_accounts": {"claude/djbclark@mit.edu": "gone"}}})

    assert row.plan == "expired"


def test_lapsed_accounts_ignores_other_providers():
    from aiuse.collectors.runner import _apply_lapsed_accounts

    codex = _account("codexbar", "codex")
    _apply_lapsed_accounts([codex], {"analysis": {"lapsed_accounts": {"claude/someone@mit.edu": "gone"}}})

    assert codex.plan is None
    assert len(codex.windows) == 1
