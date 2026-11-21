"""Per-service headroom floors (schema 1.8): advisory, scaled by plan value."""

from __future__ import annotations

import json
import re
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest

from aiuse import cli
from aiuse.analysis import history
from aiuse.analysis.floors import FLOOR_BASES, resolve_floor
from aiuse.analysis.selfdescribe import SEMANTICS, available_pools, enrich_snapshot, pool_entries, unavailable_pools
from aiuse.config import DEFAULT_CONFIG, load_config, validate_config
from aiuse.models import utcnow

NOW = utcnow()
EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.toml"


# ── the formula ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("price", "floor"),
    [
        (199.99, 5.0),  # 10 / 199.99 * 100 = 5.0003
        (99.99, 10.0),
        (50.0, 20.0),  # exactly the cap
        (20.0, 20.0),  # 50 capped to max_percent
        (10.0, 20.0),
        (1000.0, 2.0),  # 1.0 raised to min_percent
        (400.0, 2.5),
    ],
)
def test_floor_is_the_clamped_reserve_over_plan_price(price, floor):
    assert resolve_floor(None, "codex", price) == (floor, "reserve_usd")
    assert resolve_floor(DEFAULT_CONFIG["floors"], "codex", price) == (floor, "reserve_usd")


@pytest.mark.parametrize("price", [None, 0, -5, "n/a", float("nan"), True])
def test_no_usable_plan_price_gives_the_default_percent(price):
    assert resolve_floor(None, "zai", price) == (10.0, "default")
    assert resolve_floor({"default_percent": 7.5}, "zai", price) == (7.5, "default")


def test_table_settings_move_the_formula_and_its_clamps():
    floors = {"reserve_usd": 5.0, "min_percent": 3.0, "max_percent": 12.0}
    assert resolve_floor(floors, "codex", 100.0) == (5.0, "reserve_usd")
    assert resolve_floor(floors, "codex", 20.0) == (12.0, "reserve_usd")  # 25 capped
    assert resolve_floor(floors, "codex", 1000.0) == (3.0, "reserve_usd")  # 0.5 raised


def test_per_provider_percent_override_is_config_basis_whatever_the_price():
    floors = {"providers": {"codex": {"percent": 4.0}}}
    assert resolve_floor(floors, "codex", 20.0) == (4.0, "config")
    assert resolve_floor(floors, "codex", None) == (4.0, "config")
    assert resolve_floor(floors, "claude", 20.0) == (20.0, "reserve_usd")  # others keep the formula


def test_per_provider_reserve_override_feeds_the_formula_and_still_clamps():
    floors = {"providers": {"codex": {"reserve_usd": 25.0}}}
    assert resolve_floor(floors, "codex", 200.0) == (12.5, "reserve_usd")
    assert resolve_floor(floors, "codex", 100.0) == (20.0, "reserve_usd")  # 25 capped
    assert resolve_floor(floors, "codex", None) == (10.0, "default")  # no price, no formula


def test_percent_beats_reserve_usd_when_both_are_set():
    floors = {"providers": {"codex": {"percent": 6.0, "reserve_usd": 50.0}}}
    assert resolve_floor(floors, "codex", 200.0) == (6.0, "config")


def test_override_matches_the_provider_through_its_aliases():
    floors = {"providers": {"agy": {"percent": 3.0}}}
    assert resolve_floor(floors, "antigravity", 199.99) == (3.0, "config")


def test_malformed_values_fall_back_to_the_defaults_instead_of_raising():
    floors = {"reserve_usd": "ten", "min_percent": -1, "max_percent": 500, "providers": {"codex": {"percent": 900}}}
    assert resolve_floor(floors, "codex", 199.99) == (5.0, "reserve_usd")


def test_floor_bases_are_the_closed_enum():
    assert FLOOR_BASES == ("reserve_usd", "config", "default")


# ── on the routing entries ───────────────────────────────────────────────────


def _window(label: str, remaining: float, days: float = 3, minutes: int = 10080) -> dict:
    return {
        "label": label,
        "used_percent": 100.0 - remaining,
        "remaining_percent": remaining,
        "resets_at": (NOW + timedelta(days=days)).isoformat(),
        "window_minutes": minutes,
    }


def _account(provider: str, remaining: float, name: str = "me") -> dict:
    return {
        "provider": provider,
        "account": name,
        "source": "codexbar",
        "billing_kind": "subscription_window",
        "collected_at": NOW.isoformat(),
        "windows": [_window(f"{provider} weekly", remaining)],
    }


def _entries(config: dict | None, *accounts: dict) -> dict[str, dict]:
    snap = enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": list(accounts)}, now=NOW, config=config)
    return {e["provider"]: e for e in pool_entries(snap, now=NOW)}


PLANS = {"plans": {"antigravity": {"monthly_price": 199.99}, "codex": {"monthly_price": 20}}}
PLANS_200 = {"plans": {"codex": {"monthly_price": 200}}}


def test_entries_carry_floor_fields_from_the_configured_plan_price():
    rows = _entries(PLANS, _account("antigravity", 60.0), _account("codex", 45.0), _account("zai", 80.0))
    agy = rows["antigravity"]
    assert (agy["floor_percent"], agy["floor_basis"]) == (5.0, "reserve_usd")
    assert agy["headroom_above_floor_percent"] == pytest.approx(55.0)
    codex = rows["codex"]
    assert (codex["floor_percent"], codex["floor_basis"]) == (20.0, "reserve_usd")
    assert codex["headroom_above_floor_percent"] == pytest.approx(25.0)


def test_entry_without_a_plan_price_uses_the_default_floor():
    rows = _entries({"plans": {}}, _account("zai", 80.0))
    zai = rows["zai"]
    assert (zai["floor_percent"], zai["floor_basis"]) == (10.0, "default")
    assert zai["headroom_above_floor_percent"] == pytest.approx(70.0)


def test_config_overrides_reach_the_entries():
    config = {
        **PLANS,
        "floors": {"providers": {"antigravity": {"percent": 3.5}, "codex": {"reserve_usd": 4.0}}},
    }
    rows = _entries(config, _account("antigravity", 60.0), _account("codex", 45.0))
    assert (rows["antigravity"]["floor_percent"], rows["antigravity"]["floor_basis"]) == (3.5, "config")
    assert (rows["codex"]["floor_percent"], rows["codex"]["floor_basis"]) == (20.0, "reserve_usd")
    assert rows["codex"]["headroom_above_floor_percent"] == pytest.approx(25.0)


def test_headroom_above_floor_is_negative_below_the_floor_and_null_without_headroom():
    rows = _entries(PLANS, _account("codex", 12.0))
    assert rows["codex"]["headroom_above_floor_percent"] == pytest.approx(-8.0)
    unknown = _account("codex", 50.0)
    unknown["windows"] = [{"label": "codex weekly", "window_minutes": 10080}]
    row = _entries(PLANS, unknown)["codex"]
    assert row["headroom_percent"] is None
    assert row["headroom_above_floor_percent"] is None


def test_floors_change_neither_usable_now_nor_headroom_nor_the_near_empty_rule():
    accounts = [_account("codex", 12.0), _account("antigravity", 4.0), _account("zai", 80.0)]
    plain = _entries({"plans": {}}, *[json.loads(json.dumps(a)) for a in accounts])
    strict = _entries(
        {**PLANS, "floors": {"min_percent": 50.0, "max_percent": 90.0, "default_percent": 90.0}},
        *[json.loads(json.dumps(a)) for a in accounts],
    )
    for provider in plain:
        assert strict[provider]["usable_now"] == plain[provider]["usable_now"]
        assert strict[provider]["headroom_percent"] == plain[provider]["headroom_percent"]
    assert strict["codex"]["usable_now"] is True  # 12% left is under its 50%+ floor but still usable
    assert strict["codex"]["headroom_above_floor_percent"] < 0
    assert strict["antigravity"]["usable_now"] is False  # the 5% near-empty rule is untouched


def test_enrichment_without_a_config_still_gives_every_entry_a_floor():
    snap = enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": [_account("codex", 50.0)]}, now=NOW)
    (entry,) = pool_entries(snap, now=NOW)
    assert entry["floor_basis"] == "default"
    assert entry["floor_percent"] == 10.0


def test_floor_fields_are_documented_in_the_semantics_map():
    for key in ("floor_percent", "floor_basis", "headroom_above_floor_percent"):
        assert key in SEMANTICS and key in SEMANTICS["unavailable"]
    for basis in FLOOR_BASES:
        assert basis in SEMANTICS["floor_basis"]


def test_unavailable_items_carry_the_entry_floor():
    accounts = [_account("codex", 0.0), _account("antigravity", 60.0)]
    snap = enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": accounts}, now=NOW, config=PLANS)
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["provider"] == "codex"
    assert (item["floor_percent"], item["floor_basis"]) == (20.0, "reserve_usd")
    assert item["headroom_above_floor_percent"] == pytest.approx(-20.0)
    (pool,) = available_pools(snap)
    assert (pool["floor_percent"], pool["floor_basis"]) == (5.0, "reserve_usd")


def test_envelope_json_shape(tmp_path, capsys):
    snap = {"collected_at": utcnow().isoformat(), "accounts": [_account("codex", 0.0), _account("zai", 70.0)]}
    cache = history.snapshot_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "latest.json").write_text(json.dumps(snap))
    config = tmp_path / "config.toml"
    config.write_text(
        "[floors]\nreserve_usd = 4.0\n[floors.providers.zai]\npercent = 6.0\n[plans.codex]\nmonthly_price = 20\n"
    )
    assert cli.main(["--available", "--json", "-q", "--config", str(config)]) == 0
    payload = json.loads(capsys.readouterr().out)
    (avail,) = payload["available"]
    (out,) = payload["unavailable"]
    assert (avail["provider"], avail["floor_percent"], avail["floor_basis"]) == ("zai", 6.0, "config")
    assert avail["headroom_above_floor_percent"] == pytest.approx(64.0)
    assert (out["provider"], out["floor_percent"], out["floor_basis"]) == ("codex", 20.0, "reserve_usd")


# ── config ───────────────────────────────────────────────────────────────────


def test_default_config_floors_are_valid_and_match_the_documented_defaults():
    assert DEFAULT_CONFIG["floors"] == {
        "reserve_usd": 10.0,
        "min_percent": 2.0,
        "max_percent": 20.0,
        "default_percent": 10.0,
        "providers": {},
        "pool_families": {},
    }
    assert validate_config(DEFAULT_CONFIG) == []
    assert load_config()["floors"]["providers"] == {}
    assert load_config()["floors"]["pool_families"] == {}


@pytest.mark.parametrize(
    ("floors", "needle"),
    [
        ("nope", "floors must be a mapping"),
        ({"reserve_usd": 0}, "floors.reserve_usd must be greater than 0"),
        ({"reserve_usd": "ten"}, "floors.reserve_usd must be a number"),
        ({"min_percent": -1}, "floors.min_percent must be between 0 and 100"),
        ({"max_percent": 101}, "floors.max_percent must be between 0 and 100"),
        ({"default_percent": True}, "floors.default_percent must be a number"),
        ({"min_percent": 30, "max_percent": 10}, "must not exceed"),
        ({"providers": []}, "floors.providers must be"),
        ({"providers": {"codex": 5}}, "floors.providers.codex must be a table"),
        ({"providers": {"codex": {"percent": 150}}}, "floors.providers.codex.percent must be between"),
        ({"providers": {"codex": {"reserve_usd": -2}}}, "floors.providers.codex.reserve_usd must be greater"),
        ({"bogus": 1}, "unknown floors key 'bogus'"),
        ({"providers": {"codex": {"pct": 1}}}, "unknown floors.providers.codex key 'pct'"),
        ({"providers": {"codex": {"percent": 5, "reserve_usd": 5}}}, "percent wins"),
        ({"providers": {"codex": {}}}, "neither percent nor reserve_usd"),
    ],
)
def test_validate_config_reports_bad_floors(floors, needle):
    issues = validate_config({"floors": floors})
    assert any(needle in issue for issue in issues), issues


def test_validate_config_accepts_a_full_floors_table():
    floors = {
        "reserve_usd": 8,
        "min_percent": 1,
        "max_percent": 25.5,
        "default_percent": 12,
        "providers": {"antigravity": {"percent": 4}, "codex": {"reserve_usd": 30}},
    }
    assert validate_config({"floors": floors}) == []


# ── config.example.toml documents every floors key ───────────────────────────


def _example_floors_section() -> str:
    text = EXAMPLE.read_text(encoding="utf-8")
    header = text.find("# ── Per-service headroom floors")
    assert header != -1 and re.search(r"^# \[floors\]$", text, re.MULTILINE), "config.example.toml lacks [floors]"
    return text[header:]


def _all_keys(table: dict) -> set[str]:
    keys: set[str] = set()
    for key, value in table.items():
        keys.add(key)
        if isinstance(value, dict):
            keys |= _all_keys(value)
    return keys


def test_example_config_mentions_every_default_floors_key_with_a_comment():
    section = _example_floors_section()
    for key in _all_keys(DEFAULT_CONFIG["floors"]) | {"percent", "reserve_usd"}:
        assert re.search(rf"^# {re.escape(key)} = ", section, re.MULTILINE) or key in ("providers", "pool_families"), (
            key
        )
        assert re.search(
            rf"\b{re.escape(key)}\b", re.sub(rf"^# {re.escape(key)} = .*$", "", section, flags=re.MULTILINE)
        ), key
    for basis in FLOOR_BASES:
        assert f'"{basis}"' in section, basis
    for needle in (
        "clamp(reserve_usd / plan_monthly_price * 100, min_percent, max_percent)",
        "5.0",
        "20.0",
        "advisory",
    ):
        assert needle in section, needle


def test_example_floors_lines_parse_and_validate_when_uncommented():
    lines = []
    for line in _example_floors_section().splitlines():
        match = re.match(r"^# (\[.*\]|[A-Za-z_][\w.\"-]* = .*)$", line)
        if match:
            lines.append(match.group(1))
    parsed = tomllib.loads("\n".join(lines))
    floors = parsed["floors"]
    assert {"reserve_usd", "min_percent", "max_percent", "default_percent", "providers", "pool_families"} <= set(floors)
    assert floors["pool_families"]["openai_api"] == {"percent": 10.0}
    assert validate_config({"floors": floors}) == []
    assert floors["reserve_usd"] == DEFAULT_CONFIG["floors"]["reserve_usd"]
    assert floors["min_percent"] == DEFAULT_CONFIG["floors"]["min_percent"]
    assert floors["max_percent"] == DEFAULT_CONFIG["floors"]["max_percent"]
    assert floors["default_percent"] == DEFAULT_CONFIG["floors"]["default_percent"]


# ── bounds crossing the other's default (schema 1.8 review, finding 6) ──────


def test_min_percent_alone_above_the_default_max_is_flagged_with_the_swap_direction():
    issues = validate_config({"floors": {"min_percent": 50}})
    (issue,) = [i for i in issues if "min_percent" in i]
    assert "floors.min_percent (50)" in issue
    assert "max_percent (20" in issue
    assert "swapped" in issue
    assert "20..50" in issue


def test_max_percent_alone_below_the_default_min_is_flagged_with_the_swap_direction():
    issues = validate_config({"floors": {"max_percent": 1}})
    (issue,) = [i for i in issues if "max_percent" in i]
    assert "floors.max_percent (1)" in issue
    assert "min_percent (2" in issue
    assert "swapped" in issue
    assert "1..2" in issue


def test_both_bounds_given_crossed_stays_an_error():
    issues = validate_config({"floors": {"min_percent": 30, "max_percent": 10}})
    assert any(i.startswith("error:") and "min_percent" in i for i in issues)


def test_bounds_that_agree_with_the_defaults_are_clean():
    assert validate_config({"floors": {"min_percent": 5}}) == []
    assert validate_config({"floors": {"max_percent": 40}}) == []
    assert validate_config({"floors": {"min_percent": 2, "max_percent": 2}}) == []


def test_resolve_floor_swaps_defensively_when_the_bounds_cross():
    # A $199.99 plan: reserve 10 -> 5.0; min_percent=50 alone swaps the clamp to 20..50.
    assert resolve_floor({"min_percent": 50}, "claude", 199.99) == (20.0, "reserve_usd")
    assert resolve_floor({"max_percent": 1}, "claude", 20.0) == (2.0, "reserve_usd")


# ── per-pool-family floors (schema 1.8, operator-chosen) ─────────────────────


def _codex_with_api_credits(remaining_api: float = 60.0) -> dict:
    account = _account("codex", 70.0)
    credits = _window("OpenAI API credits", remaining_api)
    credits["window_id"] = "codex-api-credits"
    account["windows"].append(credits)
    return account


def _by_family(config: dict | None, account: dict) -> dict[str, dict]:
    snap = enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": [account]}, now=NOW, config=config)
    return {e["pool_family"]: e for e in pool_entries(snap, now=NOW)}


def test_prepaid_openai_api_pool_ignores_the_plan_price_and_takes_the_default_percent():
    assert resolve_floor(None, "codex", 200.0, "openai_api") == (10.0, "default")
    assert resolve_floor({"default_percent": 7}, "codex", 200.0, "openai_api") == (7.0, "default")
    # the subscription pools of the same provider still scale by plan value
    assert resolve_floor(None, "codex", 200.0, "default") == (5.0, "reserve_usd")
    assert resolve_floor(None, "codex", 200.0) == (5.0, "reserve_usd")


def test_pool_family_override_takes_percent_or_reserve_usd():
    floors = {"pool_families": {"openai_api": {"percent": 3}}}
    assert resolve_floor(floors, "codex", 200.0, "openai_api") == (3.0, "config")
    floors = {"pool_families": {"openai_api": {"reserve_usd": 25}}}
    assert resolve_floor(floors, "codex", 200.0, "openai_api") == (12.5, "reserve_usd")
    assert resolve_floor(floors, "codex", None, "openai_api") == (10.0, "default")
    # percent wins over reserve_usd, as for providers
    both = {"pool_families": {"openai_api": {"percent": 3, "reserve_usd": 25}}}
    assert resolve_floor(both, "codex", 200.0, "openai_api") == (3.0, "config")


def test_pool_family_override_beats_the_provider_override_and_leaves_other_pools_alone():
    floors = {
        "providers": {"codex": {"percent": 4}},
        "pool_families": {"openai_api": {"percent": 8}},
    }
    assert resolve_floor(floors, "codex", 200.0, "openai_api") == (8.0, "config")
    assert resolve_floor(floors, "codex", 200.0, "default") == (4.0, "config")
    assert resolve_floor(floors, "codex", 200.0) == (4.0, "config")


def test_pool_family_override_matches_the_family_name_case_insensitively_and_by_family_only():
    floors = {"pool_families": {"Gemini_Argon": {"percent": 6}}}
    assert resolve_floor(floors, "antigravity", 199.99, "gemini_argon") == (6.0, "config")
    assert resolve_floor(floors, "antigravity", 199.99, "gemini") == (5.0, "reserve_usd")


def test_malformed_pool_family_values_fall_back_instead_of_raising():
    for bad in ("nope", {"openai_api": "nope"}, {"openai_api": {"percent": "x"}}, {"openai_api": {"percent": 500}}):
        assert resolve_floor({"pool_families": bad}, "codex", 200.0, "openai_api") == (10.0, "default")
        assert resolve_floor({"pool_families": bad}, "codex", 200.0, "default") == (5.0, "reserve_usd")


def test_codex_api_credits_entry_gets_the_prepaid_default_and_the_subscription_entry_keeps_the_plan_floor():
    rows = _by_family(PLANS_200, _codex_with_api_credits())
    assert (rows["openai_api"]["floor_percent"], rows["openai_api"]["floor_basis"]) == (10.0, "default")
    assert rows["openai_api"]["headroom_above_floor_percent"] == pytest.approx(50.0)
    default_row = rows["default"]
    assert (default_row["floor_percent"], default_row["floor_basis"]) == (5.0, "reserve_usd")


def test_pool_family_config_reaches_the_codex_api_credits_entry():
    config = {**PLANS_200, "floors": {"pool_families": {"openai_api": {"percent": 3.5}}}}
    rows = _by_family(config, _codex_with_api_credits())
    assert (rows["openai_api"]["floor_percent"], rows["openai_api"]["floor_basis"]) == (3.5, "config")
    assert rows["default"]["floor_basis"] == "reserve_usd"


def test_unenriched_snapshots_still_get_the_prepaid_default_on_the_entry():
    account = _codex_with_api_credits()
    account["monthly_price"] = 200.0
    account["windows"][1]["pool_family"] = "openai_api"
    rows = {
        e["pool_family"]: e for e in pool_entries({"collected_at": NOW.isoformat(), "accounts": [account]}, now=NOW)
    }
    assert (rows["openai_api"]["floor_percent"], rows["openai_api"]["floor_basis"]) == (10.0, "default")


@pytest.mark.parametrize(
    ("floors", "needle"),
    [
        ({"pool_families": []}, "floors.pool_families must be"),
        ({"pool_families": {"openai_api": 5}}, "floors.pool_families.openai_api must be a table"),
        (
            {"pool_families": {"openai_api": {"percent": 150}}},
            "floors.pool_families.openai_api.percent must be between",
        ),
        (
            {"pool_families": {"openai_api": {"reserve_usd": -2}}},
            "floors.pool_families.openai_api.reserve_usd must be greater",
        ),
        ({"pool_families": {"openai_api": {"pct": 1}}}, "unknown floors.pool_families.openai_api key 'pct'"),
        ({"pool_families": {"openai_api": {"percent": 5, "reserve_usd": 5}}}, "percent wins"),
        ({"pool_families": {"openai_api": {}}}, "neither percent nor reserve_usd"),
        ({"pool_families": {"opnai_api": {"percent": 5}}}, "no pool reports the family 'opnai_api'"),
        ({"pool_families": {"default": {"percent": 5}}}, "every provider's default pool"),
    ],
)
def test_validate_config_reports_bad_pool_family_floors(floors, needle):
    issues = validate_config({"floors": floors})
    assert any(needle in issue for issue in issues), issues


def test_validate_config_accepts_known_pool_families():
    floors = {
        "pool_families": {
            "openai_api": {"percent": 4},
            "gemini_argon": {"reserve_usd": 5},
            "anthropic_api": {"percent": 10},
            "fable": {"percent": 6},
        }
    }
    assert validate_config({"floors": floors}) == []


def test_pool_family_floor_fields_are_documented():
    assert "pool_families" in SEMANTICS["floor_basis"]
    assert "openai_api" in SEMANTICS["floor_percent"]
    assert "floor_by_pool_family" in SEMANTICS


# ── review nits 8 and 9: provider keys, free and non-numeric prices ──────────


def test_floors_provider_aliases_with_two_values_are_flagged():
    floors = {"providers": {"agy": {"percent": 3}, "antigravity": {"percent": 9}}}
    issues = validate_config({"floors": floors})
    (issue,) = [i for i in issues if "same provider" in i]
    assert "floors.providers.antigravity" in issue and "floors.providers.agy" in issue
    assert "only the first" in issue
    assert resolve_floor(floors, "antigravity", 199.99) == (3.0, "config")  # the first one wins, as warned


def test_floors_unknown_provider_key_is_flagged_but_real_ones_are_not():
    issues = validate_config({"floors": {"providers": {"claudee": {"percent": 3}}}})
    assert any("unknown provider 'claudee'" in i for i in issues), issues
    real = ["antigravity", "agy", "gemini", "claude", "codex", "omp", "pi", "cursor", "zai", "grok", "copilot"]
    real += ["opencode-go", "opencode", "clinepass", "deepseek", "bedrock", "muse", "qwencloud", "alibaba"]
    floors = {"providers": {name: {"percent": 5} for name in real if name not in ("agy", "gemini", "opencode")}}
    assert validate_config({"floors": floors}) == []
    for alias in ("agy", "gemini", "opencode"):
        assert validate_config({"floors": {"providers": {alias: {"percent": 5}}}}) == [], alias


@pytest.mark.parametrize("price", [0, 0.0, -5, "20", True, None, float("nan")])
def test_a_free_or_non_numeric_plan_price_gives_the_default_floor_and_no_headroom_usd(price):
    account = _account("codex", 50.0)
    account["monthly_price"] = price
    snap = {"collected_at": NOW.isoformat(), "accounts": [account]}
    (entry,) = pool_entries(snap, now=NOW)
    assert (entry["floor_percent"], entry["floor_basis"]) == (10.0, "default")
    if isinstance(price, (int, float)) and not isinstance(price, bool) and price == price:
        return  # a numeric price keeps its headroom_usd
    assert "headroom_usd" not in entry


def test_the_floor_basis_wording_says_a_positive_numeric_price():
    assert "positive numeric plan price" in SEMANTICS["floor_basis"]
    assert "positive numeric plan price" in SEMANTICS["floor_percent"]


def test_pool_family_duplicate_spellings_with_two_values_are_flagged():
    """Review round 2, nit 6: openai_api and OPENAI_API both configured, the first silently won."""
    floors = {"pool_families": {"openai_api": {"percent": 3}, "OPENAI_API": {"percent": 9}}}
    issues = validate_config({"floors": floors})
    (issue,) = [i for i in issues if "same pool family" in i]
    assert issue.startswith("warning")
    assert "floors.pool_families.OPENAI_API" in issue and "floors.pool_families.openai_api" in issue
    assert "only the first" in issue
    assert resolve_floor(floors, "codex", 200.0, "openai_api") == (3.0, "config")  # the first one wins, as warned
    swapped = {"pool_families": {"OPENAI_API": {"percent": 9}, "openai_api": {"percent": 3}}}
    assert resolve_floor(swapped, "codex", 200.0, "openai_api") == (9.0, "config")
    assert not [
        i for i in validate_config({"floors": {"pool_families": {"openai_api": {"percent": 3}}}}) if "same" in i
    ]
