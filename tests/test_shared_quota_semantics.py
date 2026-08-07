"""Dogfood docs/shared-quota-semantics fixtures against aiuse analysis."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from aiuse.analysis.pace import classify_pace, compute_pace, governing_partition
from aiuse.analysis.suggest import pick_suggestion
from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UsageCredits,
    parse_dt,
)
from aiuse.report import alert_priority_band

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "docs" / "shared-quota-semantics"
FIXTURES = PKG / "fixtures"
SCHEMAS = PKG / "schemas"

_BAND_NAMES = {
    0: "error",
    1: "empty",
    2: "n_a",
    3: "slow",
    4: "mid",
    5: "use",
}


def _load_fixtures() -> list[dict]:
    rows = []
    for path in sorted(FIXTURES.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def _account_from_fixture(data: dict) -> AccountUsage:
    acc = data["account"]
    windows = []
    for w in acc.get("windows") or []:
        windows.append(
            QuotaWindow(
                label=str(w.get("label") or ""),
                used_percent=w.get("used_percent"),
                remaining_percent=w.get("remaining_percent"),
                resets_at=parse_dt(w.get("resets_at")),
                window_minutes=w.get("window_minutes"),
                reset_description=w.get("reset_description"),
            )
        )
    try:
        billing = BillingKind(str(acc.get("billing_kind") or "unknown"))
    except ValueError:
        billing = BillingKind.UNKNOWN
    usage_credits = None
    uc = acc.get("usage_credits")
    if uc is not None:
        usage_credits = UsageCredits(
            used=uc.get("used"),
            limit=uc.get("limit"),
            remaining=uc.get("remaining"),
            currency=uc.get("currency", "USD"),
            used_percent=uc.get("used_percent"),
            resets_at=parse_dt(uc.get("resets_at")),
        )
    return AccountUsage(
        source=str(acc.get("source") or "fixture"),
        provider=str(acc.get("provider") or "unknown"),
        account=acc.get("account"),
        plan=acc.get("plan"),
        billing_kind=billing,
        windows=windows,
        balance_usd=acc.get("balance_usd"),
        credits_remaining=acc.get("credits_remaining"),
        usage_credits=usage_credits,
        error=acc.get("error"),
        notes=list(acc.get("notes") or []),
    )


def _now_from_fixture(data: dict) -> datetime:
    dt = parse_dt(data["now"])
    assert dt is not None
    return dt


@pytest.fixture(scope="module")
def jsonschema_mod():
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema


def test_package_layout_exists():
    assert (PKG / "README.md").is_file()
    assert (PKG / "formulas" / "pace.md").is_file()
    assert list(FIXTURES.glob("*.json"))
    assert list(SCHEMAS.glob("*.json"))


def test_enums_match_aiuse_models():
    billing = yaml.safe_load((PKG / "enums" / "billing_kind.yaml").read_text())
    assert set(billing["values"]) == {e.value for e in BillingKind}


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["id"])
def test_fixture_schema_valid(fixture, jsonschema_mod):
    schema = json.loads((SCHEMAS / "fixture.schema.json").read_text())
    jsonschema_mod.Draft202012Validator(schema).validate(fixture)


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["id"])
def test_fixture_expectations(fixture, monkeypatch):
    now = _now_from_fixture(fixture)
    monkeypatch.setattr("aiuse.analysis.use_or_lose.utcnow", lambda: now)
    monkeypatch.setattr("aiuse.models.utcnow", lambda: now)

    account = _account_from_fixture(fixture)
    expected = fixture["expected"]
    config = fixture.get("config") or {"analysis": {"scoring_mode": "pace", "learn_from_history": False}}

    # Governing label (shared allotment structure)
    if expected.get("governing_label") is not None:
        gov, _ = governing_partition(account.windows)
        assert gov is not None
        assert gov.label == expected["governing_label"]

    # Pace verdict on first window with remaining (when requested)
    if expected.get("pace_verdict") is not None and account.windows:
        window = account.windows[0]
        if expected.get("governing_label"):
            gov, _ = governing_partition(account.windows)
            window = gov or window
        pace = compute_pace(window, now=now)
        assert pace is not None
        pace_cfg = (config.get("analysis") or {}).get("pace") or {}
        verdict = classify_pace(
            pace,
            resets_at=window.resets_at,
            waste_alert_fraction=float(pace_cfg.get("waste_alert_fraction", 0.30)),
            min_elapsed_fraction=float(pace_cfg.get("min_elapsed_fraction", 0.15)),
            conserve_min_lead_hours=float(pace_cfg.get("conserve_min_lead_hours", 4.0)),
            has_learned_rate=False,
        )
        assert verdict == expected["pace_verdict"]

    # Full analyze path
    snap = Snapshot(collected_at=now, accounts=[account])
    alerts = analyze_use_or_lose(snap, config)

    if account.error and not account.windows:
        # Error rows do not produce burn alerts
        assert not any(a.kind == "burn" for a in alerts)
        if expected.get("band") == "error":
            return

    if account.billing_kind == BillingKind.PREPAID_BALANCE:
        assert expected.get("suggestion_eligible") is False
        assert pick_suggestion(alerts) is None

        prepaid_alerts = [a for a in alerts if a.kind == "prepaid"]
        if "band" in expected and prepaid_alerts:
            prepaid = prepaid_alerts[0]
            assert _BAND_NAMES[alert_priority_band(prepaid)] == expected["band"]
        return

    winner = pick_suggestion(alerts)
    eligible = winner is not None
    if "suggestion_eligible" in expected:
        assert eligible is bool(expected["suggestion_eligible"])

    if expected.get("alert_kind") in ("burn", "conserve"):
        kinds = {a.kind for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE)}
        assert expected["alert_kind"] in kinds
        sample = next(a for a in alerts if a.kind == expected["alert_kind"])
        if expected.get("band"):
            assert _BAND_NAMES[alert_priority_band(sample)] == expected["band"]
        if "has_overage" in expected:
            assert sample.pace is not None
            assert sample.pace.has_overage is bool(expected["has_overage"])

    if expected.get("governing_label") and expected.get("notes", "").lower().find("5h") >= 0:
        # Shared allotment: no 5h-only alert when weekly is the governor
        assert not any("5-hour" in (a.window_label or "") for a in alerts)

    # Windows that must independently alert (e.g. hard-separated pools like
    # Antigravity's Gemini vs Claude/GPT, each with its own governing weekly).
    if expected.get("alert_labels"):
        alerting = {a.window_label for a in alerts if a.kind in ("burn", "conserve")}
        for label in expected["alert_labels"]:
            assert label in alerting, f"expected an alert for window {label!r}, got {alerting}"

    # Windows that must never alert on their own (shared-allotment children:
    # nested 5h siblings, cswap model-scoped weekly sub-windows, …).
    if expected.get("suppressed_labels"):
        alerting = {a.window_label for a in alerts}
        for label in expected["suppressed_labels"]:
            assert label not in alerting, f"expected {label!r} to be a suppressed child, but it alerted"


def test_account_schema_accepts_aiuse_to_dict(jsonschema_mod):
    schema = json.loads((SCHEMAS / "account.schema.json").read_text())
    acc = AccountUsage(
        source="codexbar",
        provider="codex",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="weekly",
                remaining_percent=50.0,
                window_minutes=10080,
            )
        ],
    )
    jsonschema_mod.Draft202012Validator(schema).validate(acc.to_dict())
