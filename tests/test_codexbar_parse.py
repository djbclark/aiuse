"""Parse codexbar-shaped JSON without calling the binary."""

from __future__ import annotations

import threading

from aiuse.collectors.base import CollectorError
from aiuse.collectors.codexbar import _from_row, _normalize_providers, _parse_enabled_providers, _query_providers
from aiuse.models import BillingKind


def test_parse_copilot_style():
    row = {
        "provider": "copilot",
        "source": "api",
        "account": "example-user (Individual)",
        "usage": {
            "primary": {
                "usedPercent": 100,
                "resetsAt": "2026-08-01T00:00:00Z",
                "windowMinutes": 44640,
            },
            "secondary": {
                "usedPercent": 18.6,
                "resetsAt": "2026-08-01T00:00:00Z",
                "windowMinutes": 44640,
            },
            "loginMethod": "Individual",
            "accountEmail": "example-user (Individual)",
        },
    }
    acc = _from_row(row)
    assert acc.provider == "copilot"
    assert acc.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    # Completions/chat are dropped; CodexBar primary is premium requests.
    assert len(acc.windows) == 1
    assert acc.windows[0].remaining_percent == 0
    assert acc.windows[0].label == "GitHub Copilot premium requests"


def test_parse_error_row():
    row = {
        "provider": "claude",
        "source": "auto",
        "error": {"kind": "provider", "message": "No Claude session key"},
    }
    acc = _from_row(row)
    assert acc.error
    assert "Claude" in acc.error or "session" in acc.error


def test_parse_openrouter_prepaid():
    row = {
        "provider": "openrouter",
        "source": "api",
        "usage": {
            "loginMethod": "Balance: $18.90",
            "openRouterUsage": {
                "balance": 18.903002,
                "totalCredits": 20,
                "totalUsage": 1.096998,
                "usedPercent": 5.48,
            },
        },
    }
    acc = _from_row(row)
    assert acc.billing_kind == BillingKind.PREPAID_BALANCE
    assert acc.balance_usd is not None
    assert acc.balance_usd > 18


def test_parse_named_extra_rate_windows_without_generic_duplicates():
    row = {
        "provider": "antigravity",
        "source": "app",
        "usage": {
            "extraRateWindows": [
                {
                    "title": "Gemini weekly",
                    "window": {
                        "usedPercent": 0,
                        "resetsAt": "2026-08-01T00:00:00Z",
                        "windowMinutes": 10080,
                    },
                },
                {
                    "title": "Claude/GPT weekly",
                    "window": {
                        "usedPercent": 75,
                        "resetsAt": "2026-07-25T00:00:00Z",
                        "windowMinutes": 10080,
                    },
                },
            ],
            # These are duplicate projections of the named windows above.
            "primary": {
                "usedPercent": 0,
                "resetsAt": "2026-08-01T00:00:00Z",
                "windowMinutes": 10080,
            },
            "secondary": {
                "usedPercent": 75,
                "resetsAt": "2026-07-25T00:00:00Z",
                "windowMinutes": 10080,
            },
        },
    }
    acc = _from_row(row)
    assert [window.label for window in acc.windows] == [
        "Gemini weekly",
        "Claude/GPT weekly",
    ]


def test_unknown_duration_is_not_guessed_from_reset_time():
    acc = _from_row(
        {
            "provider": "grok",
            "usage": {
                "primary": {
                    "usedPercent": 25,
                    "resetsAt": "2099-01-01T00:00:00Z",
                }
            },
        }
    )
    assert acc.windows[0].label == "Grok usage limit"


def test_zero_of_zero_is_not_reported_as_fully_unused():
    acc = _from_row(
        {
            "provider": "warp",
            "usage": {
                "primary": {
                    "usedPercent": 0,
                    "resetDescription": "0/0 credits",
                    "resetsAt": "2099-01-01T00:00:00Z",
                }
            },
        }
    )
    assert acc.windows[0].label == "Warp credits"
    assert acc.windows[0].remaining() == 0


def test_provider_selection_defaults_to_enabled_and_splits_csv():
    assert _normalize_providers("enabled") == [None]
    assert _normalize_providers("codex,copilot") == ["codex", "copilot"]


def test_openai_api_usage_is_payg_not_prepaid():
    # "openai" is in PREPAID_HINTS, but an explicit openAIAPIUsage blob means this
    # account is billed pay-as-you-go, not a prepaid balance that rolls over.
    acc = _from_row(
        {
            "provider": "openai",
            "usage": {"openAIAPIUsage": {"usedPercent": 12}},
        }
    )
    assert acc.billing_kind == BillingKind.PAYG_API


def test_prepaid_hint_provider_with_real_window_is_subscription_window():
    # A PREPAID_HINTS provider (e.g. "together") that reports a genuine rate-limit
    # window with a real reset time must still be flagged as use-or-lose eligible,
    # not silently treated as a non-urgent prepaid balance.
    acc = _from_row(
        {
            "provider": "together",
            "usage": {
                "primary": {
                    "usedPercent": 5,
                    "resetsAt": "2026-08-01T00:00:00Z",
                    "windowMinutes": 10080,
                }
            },
        }
    )
    assert acc.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert acc.windows[0].remaining() == 95


def test_dollar_rate_in_description_is_not_mistaken_for_a_balance():
    acc = _from_row(
        {
            "provider": "glama",
            "usage": {
                "primary": {
                    "usedPercent": 10,
                    "resetDescription": "Overage billed at $0.002/1K tokens, no fixed reset",
                }
            },
        }
    )
    assert acc.balance_usd is None


def test_unmapped_provider_windowminutes_bucketing_boundaries():
    weekly = _from_row({"provider": "codex", "usage": {"primary": {"usedPercent": 10, "windowMinutes": 500}}})
    assert weekly.windows[0].label == "Codex weekly quota (1)"

    monthly = _from_row({"provider": "codex", "usage": {"primary": {"usedPercent": 10, "windowMinutes": 44640}}})
    assert monthly.windows[0].label == "Codex monthly quota (1)"


def test_parse_enabled_providers_filters_to_enabled_true():
    # Shape matches `codexbar config providers --format json`.
    payload = [
        {"provider": "codex", "enabled": True, "defaultEnabled": True, "displayName": "Codex"},
        {"provider": "openai", "enabled": False, "defaultEnabled": False, "displayName": "OpenAI"},
        {"provider": "claude", "enabled": True, "defaultEnabled": False, "displayName": "Claude"},
    ]
    assert _parse_enabled_providers(payload) == ["codex", "claude"]


def test_parse_enabled_providers_returns_none_for_unexpected_shapes():
    assert _parse_enabled_providers({"not": "a list"}) is None
    assert _parse_enabled_providers([]) is None
    assert _parse_enabled_providers([{"provider": "codex", "enabled": False}]) is None


def test_query_providers_merges_results_in_deterministic_order(monkeypatch):
    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        provider = argv[argv.index("--provider") + 1]
        if provider == "cursor":
            raise CollectorError("session expired")
        return [{"provider": provider, "usage": {"primary": {"usedPercent": 1}}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    results = _query_providers(["codex", "cursor", "claude"])

    assert [provider for provider, _ in results] == ["codex", "cursor", "claude"]
    codex_outcome = results[0][1]
    assert isinstance(codex_outcome, list)
    assert codex_outcome[0]["provider"] == "codex"
    cursor_outcome = results[1][1]
    assert isinstance(cursor_outcome, CollectorError)
    assert "session expired" in str(cursor_outcome)


def test_collect_codexbar_discovers_and_queries_enabled_providers_individually(monkeypatch):
    calls = []

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        calls.append(list(argv))
        if "config" in argv:
            return [
                {"provider": "codex", "enabled": True},
                {"provider": "claude", "enabled": True},
                {"provider": "openai", "enabled": False},
            ]
        provider = argv[argv.index("--provider") + 1]
        return [{"provider": provider, "usage": {"primary": {"usedPercent": 1}}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import collect_codexbar

    accounts = collect_codexbar()

    assert {account.provider for account in accounts} == {"codex", "claude"}
    # The bundled no-`--provider` call is never made once discovery succeeds.
    assert all("--provider" in call for call in calls if call[:2] == ["codexbar", "usage"])


def test_one_slow_or_hanging_provider_does_not_delay_the_others(monkeypatch):
    all_queries_started = threading.Barrier(4)

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        provider = argv[argv.index("--provider") + 1]
        # A sequential implementation cannot get all four calls to this point;
        # the barrier tests actual overlap without relying on scheduler timing.
        all_queries_started.wait(timeout=1.0)
        return [{"provider": provider, "usage": {}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    results = _query_providers(["codex", "claude", "grok", "cursor"])

    assert all_queries_started.n_waiting == 0
    assert all_queries_started.broken is False
    outcomes = dict(results)
    assert outcomes["codex"][0]["provider"] == "codex"
    assert outcomes["claude"][0]["provider"] == "claude"
    assert outcomes["grok"][0]["provider"] == "grok"
    assert outcomes["cursor"][0]["provider"] == "cursor"


def test_duplicate_providers_are_queried_once_not_raced(monkeypatch):
    call_count = {"codex": 0}

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        provider = argv[argv.index("--provider") + 1]
        if provider == "codex":
            call_count["codex"] += 1
        return [{"provider": provider, "usage": {}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    results = _query_providers(["codex", "codex", "claude"])

    assert [provider for provider, _ in results] == ["codex", "claude"]
    assert call_count["codex"] == 1


def test_non_collector_error_from_one_provider_does_not_abort_the_batch(monkeypatch):
    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        provider = argv[argv.index("--provider") + 1]
        if provider == "claude":
            raise ValueError("unexpected non-CollectorError failure")
        return [{"provider": provider, "usage": {}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    results = _query_providers(["codex", "claude", "grok"])

    outcomes = dict(results)
    assert outcomes["codex"][0]["provider"] == "codex"
    assert outcomes["grok"][0]["provider"] == "grok"
    assert isinstance(outcomes["claude"], CollectorError)
    assert "unexpected non-CollectorError failure" in str(outcomes["claude"])


def test_discovery_failure_via_non_collector_error_falls_back_gracefully(monkeypatch):
    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        if "config" in argv:
            raise ValueError("codexbar config providers exploded")
        return [{"provider": "codex", "usage": {}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import collect_codexbar

    accounts = collect_codexbar()

    # Falls back to the bundled no-`--provider` call instead of raising.
    assert {account.provider for account in accounts} == {"codex"}


def test_single_discovered_provider_uses_configured_timeout(monkeypatch):
    captured_timeouts = []

    def fake_run_json(argv, *, timeout=45.0, allow_empty=False):
        if "config" in argv:
            return [{"provider": "openrouter", "enabled": True}]
        captured_timeouts.append(timeout)
        return [{"provider": "openrouter", "usage": {}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import collect_codexbar

    collect_codexbar(timeout=45.0, discovery_timeout=45.0)

    assert captured_timeouts == [45.0]


def test_unnamed_same_duration_slots_keep_distinct_labels():
    from aiuse.collectors.codexbar import _from_row

    row = {
        "provider": "mystery",
        "enabled": True,
        "usage": {
            "primary": {
                "usedPercent": 10,
                "windowMinutes": 300,
                "resetsAt": "2099-01-01T00:00:00Z",
            },
            "secondary": {
                "usedPercent": 40,
                "windowMinutes": 300,
                "resetsAt": "2099-01-01T01:00:00Z",
            },
        },
    }
    account = _from_row(row)
    assert len(account.windows) == 2
    labels = [w.label for w in account.windows]
    assert labels[0] != labels[1]
    assert "(1)" in labels[0]
    assert "(2)" in labels[1]


def test_opencodego_prefers_web_source(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        calls.append(list(argv))
        assert "--source" in argv and argv[argv.index("--source") + 1] == "web"
        return [
            {
                "provider": "opencodego",
                "source": "web",
                "usage": {
                    "primary": {"usedPercent": 0, "windowMinutes": 300, "resetsAt": "2099-01-01T00:00:00Z"},
                    "secondary": {"usedPercent": 16, "windowMinutes": 10080, "resetsAt": "2099-01-08T00:00:00Z"},
                    "tertiary": {"usedPercent": 100, "windowMinutes": 43200, "resetsAt": "2099-02-01T00:00:00Z"},
                    "providerCost": {
                        "used": 1.25,
                        "limit": 0,
                        "period": "Zen balance",
                        "currencyCode": "USD",
                    },
                },
            }
        ]

    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import _from_row, _opencode_zen_from_row, _query_provider

    outcome = _query_provider("opencodego")
    assert isinstance(outcome, list)
    assert outcome[0]["source"] == "web"
    assert len(calls) == 1
    assert calls[0][-2:] == ["--source", "web"]

    account = _from_row(outcome[0])
    monthly = next(w for w in account.windows if "monthly" in w.label.lower())
    assert monthly.remaining_percent == 0.0
    assert account.balance_usd is None
    zen = _opencode_zen_from_row(outcome[0])
    assert zen is not None
    assert zen.provider == "opencode-zen"
    assert zen.billing_kind == BillingKind.PREPAID_BALANCE
    assert zen.balance_usd == 1.25


def test_opencodego_falls_back_to_auto_when_web_errors(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):
        calls.append(list(argv))
        if "--source" in argv:
            return [
                {
                    "provider": "opencodego",
                    "source": "web",
                    "error": {"kind": "provider", "message": "missing cookie"},
                }
            ]
        return [
            {
                "provider": "opencodego",
                "source": "local",
                "usage": {
                    "tertiary": {"usedPercent": 80.6, "windowMinutes": 43200, "resetsAt": "2099-02-01T00:00:00Z"},
                },
            }
        ]

    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import _from_row, _query_provider

    outcome = _query_provider("opencodego")
    assert isinstance(outcome, list)
    assert outcome[0]["source"] == "local"
    assert any(call[-2:] == ["--source", "web"] for call in calls)
    assert any("--source" not in call for call in calls)

    account = _from_row(outcome[0])
    assert any("local estimate" in note for note in account.notes)


def test_opencodego_local_plus_web_source_is_still_a_local_estimate():
    from aiuse.collectors.codexbar import _from_row

    account = _from_row(
        {
            "provider": "opencodego",
            "source": "local+web",
            "usage": {
                "tertiary": {"usedPercent": 1.9, "windowMinutes": 43200, "resetsAt": "2099-02-01T00:00:00Z"},
            },
        }
    )
    assert any("local estimate" in note for note in account.notes)


def test_usable_usage_payload_rejects_error_only_rows():
    from aiuse.collectors.base import CollectorError
    from aiuse.collectors.codexbar import _usable_usage_payload

    assert _usable_usage_payload(CollectorError("boom")) is False
    assert _usable_usage_payload([{"provider": "opencodego", "error": {"message": "x"}}]) is False
    assert _usable_usage_payload([{"provider": "opencodego", "usage": {"primary": {"usedPercent": 1}}}]) is True


def test_dollar_in_reset_description_does_not_flip_subscription_billing():
    from aiuse.collectors.codexbar import _from_row
    from aiuse.models import BillingKind

    row = {
        "provider": "codex",
        "enabled": True,
        "usage": {
            "primary": {
                "usedPercent": 10,
                "windowMinutes": 10080,
                "resetsAt": "2099-01-01T00:00:00Z",
                "resetDescription": "Resets soon — plan was $20/mo",
            }
        },
    }
    account = _from_row(row)
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(account.windows) == 1


def test_parse_cursor_included_auto_other_models_and_ondemand():
    row = {
        "provider": "cursor",
        "source": "web",
        "usage": {
            "primary": {
                "usedPercent": 61.35942028985507,
                "resetsAt": "2026-08-02T23:22:27Z",
                "windowMinutes": 44640,
                "resetDescription": "Resets Aug 2 at 7:22PM",
            },
            "secondary": {
                "usedPercent": 54.89333333333334,
                "resetsAt": "2026-08-02T23:22:27Z",
                "windowMinutes": 44640,
            },
            "tertiary": {
                "usedPercent": 100,
                "resetsAt": "2026-08-02T23:22:27Z",
                "windowMinutes": 44640,
            },
            "providerCost": {
                "period": "Monthly",
                "resetsAt": "2026-08-02T23:22:27Z",
                "limit": 2,
                "currencyCode": "USD",
                "used": 1.47,
            },
            "loginMethod": "Cursor Pro",
            "accountEmail": "djbclark@gmail.com",
        },
    }
    acc = _from_row(row)
    assert acc.provider == "cursor"
    assert acc.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert [w.label for w in acc.windows] == [
        "Cursor included",
        "Cursor Auto",
        "Cursor other models",
    ]
    assert all(w.resets_at is not None for w in acc.windows)
    assert abs((acc.windows[0].remaining_percent or 0) - (100 - 61.35942028985507)) < 0.01
    assert acc.windows[2].remaining_percent == 0
    assert acc.usage_credits is not None
    assert acc.usage_credits.used == 1.47
    assert acc.usage_credits.limit == 2
    assert abs((acc.usage_credits.remaining or 0) - 0.53) < 0.001
    assert any("On-demand" in n for n in acc.notes)

    # Parse → use-or-lose: Other Models is a monthly window, not prepaid inventory.
    # Fixture resetsAt is a fixed historical timestamp; move windows into the future
    # so analyze_use_or_lose (which uses utcnow()) still sees an open window.
    from datetime import UTC, datetime, timedelta

    from aiuse.analysis.use_or_lose import analyze_use_or_lose
    from aiuse.models import Snapshot

    future = datetime.now(UTC) + timedelta(days=9)
    for window in acc.windows:
        window.resets_at = future
    snap = Snapshot(collected_at=datetime.now(UTC), accounts=[acc])
    cfg = {
        "analysis": {
            "scoring_mode": "pace",
            "learn_from_history": False,
            "provider_overrides": {"cursor": {"shared_allotment": True}},
            "pace": {"waste_alert_fraction": 0.30, "min_elapsed_fraction": 0.15},
        },
        "plans": {},
    }
    alerts = analyze_use_or_lose(snap, cfg)
    other = [a for a in alerts if a.window_label == "Cursor other models"]
    assert len(other) == 1
    assert other[0].kind == "conserve"
    assert all(a.kind != "prepaid" for a in alerts)


def test_antigravity_unnamed_slots_get_the_same_pool_names_as_titled_windows():
    """CodexBar reports antigravity two ways; both must land on one identity.

    With `extraRateWindows` the windows arrive titled ("Gemini 5-hour"). With
    only primary/secondary they used to arrive as "quota 1/2 (name not supplied
    by CodexBar)", which forked the same subscription: pool splitting saw no
    Gemini/Claude-GPT pools and history keyed a second, unmatchable series.
    """
    from datetime import timedelta

    from aiuse.models import utcnow

    now = utcnow()
    row = {
        "provider": "antigravity",
        "source": "cli",
        "account": "user@example.com",
        "usage": {
            # No windowMinutes and no titles — the shape that used to break.
            "primary": {"usedPercent": 1.0, "resetsAt": (now + timedelta(hours=4)).isoformat()},
            "secondary": {"usedPercent": 0.0, "resetsAt": (now + timedelta(hours=5)).isoformat()},
        },
    }
    acc = _from_row(row)
    assert acc is not None
    labels = [w.label for w in acc.windows]
    assert labels == ["Gemini 5-hour", "Claude/GPT 5-hour"]


def test_antigravity_named_and_unnamed_shapes_split_into_the_same_pools():
    from datetime import timedelta

    from aiuse.analysis.pace import independent_pool_key, partition_independent_pools
    from aiuse.models import utcnow

    now = utcnow()
    unnamed = _from_row(
        {
            "provider": "antigravity",
            "source": "cli",
            "account": "user@example.com",
            "usage": {
                "primary": {"usedPercent": 1.0, "resetsAt": (now + timedelta(hours=4)).isoformat()},
                "secondary": {"usedPercent": 0.0, "resetsAt": (now + timedelta(hours=5)).isoformat()},
            },
        }
    )
    assert unnamed is not None
    # Two hard-separated pools, exactly as the titled shape produces.
    assert [independent_pool_key(w.label) for w in unnamed.windows] == ["gemini", "claude_gpt"]
    assert len(partition_independent_pools(unnamed.windows)) == 2


def test_antigravity_slot_label_falls_back_when_reset_is_absent():
    """No duration and no reset: keep the pool name, drop the invented suffix."""
    from aiuse.collectors.codexbar import _slot_label

    assert _slot_label("antigravity", 1, {}) == "Gemini"
    # A slot beyond the known pools keeps the generic fallback. Antigravity is a
    # subscription, so an empty slot is unnamed data — never a "prepaid balance".
    assert _slot_label("antigravity", 3, {}) == "Google AI / Antigravity quota 3"


def test_prepaid_providers_report_a_balance_not_an_unnamed_quota():
    """deepseek/openrouter send credit, not a window — say so, and stop blaming CodexBar.

    These arrive every collection with no windowMinutes, no resetsAt, a balance
    in `reset_description` and a meaningless used=0/remaining=100, and used to
    render as `Deepseek quota 1 (name not supplied by CodexBar)` in `ai --json`.
    """
    from aiuse.collectors.codexbar import _slot_label

    assert _slot_label("deepseek", 1, {}) == "DeepSeek prepaid balance"
    assert _slot_label("openrouter", 1, {}) == "OpenRouter prepaid balance"
    # The old label is gone entirely, for every provider.
    assert "name not supplied" not in _slot_label("deepseek", 1, {})
    assert "name not supplied" not in _slot_label("somevendor", 2, {})

    # A prepaid provider that *does* report a real recurring window is still
    # named by its period — the balance wording is only for the no-window case.
    assert _slot_label("deepseek", 1, {"windowMinutes": 300}) == "DeepSeek 5-hour quota (1)"


def test_slot_label_names_a_window_by_its_reset_when_duration_is_missing():
    """CodexBar routinely omits windowMinutes; the reset distance still names the period."""
    from datetime import timedelta

    from aiuse.collectors.codexbar import _slot_label
    from aiuse.models import utcnow

    in_three_hours = (utcnow() + timedelta(hours=3)).isoformat()
    in_five_days = (utcnow() + timedelta(days=5)).isoformat()
    assert _slot_label("somevendor", 1, {"resetsAt": in_three_hours}) == "Somevendor 5-hour quota (1)"
    assert _slot_label("somevendor", 1, {"resetsAt": in_five_days}) == "Somevendor weekly quota (1)"
