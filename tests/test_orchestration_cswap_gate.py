"""Tests for orchestration/cswap-gate.sh (aiuse-juk.3, US-003).

A stub ``cswap`` prints a canned ``cswap list --json`` document and logs its
arguments, so the tests can also prove the gate never switches accounts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "orchestration" / "cswap-gate.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _base_env() -> dict[str, str]:
    """The test environment minus BASH_ENV/ENV.

    A non-interactive bash sources $BASH_ENV, and a developer's rc file there
    can re-prepend directories to PATH, which would shadow the stubs below.
    """
    return {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV")}


FAKE_CSWAP = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CSWAP_LOG"
[ -z "${FAKE_CSWAP_STDERR:-}" ] || echo "$FAKE_CSWAP_STDERR" >&2
cat "$FAKE_CSWAP_JSON"
exit "${FAKE_CSWAP_RC:-0}"
"""


def _account(number: int, pct: float | None, *, active: bool, status: str = "ok", age: float = 30.0) -> dict:
    five_hour = None
    if pct is not None:
        five_hour = {
            "pct": pct,
            "resetsAt": "2026-10-09T09:39:59+00:00",
            "countdown": "4h 37m",
            "clock": "05:39",
        }
    return {
        "number": number,
        "email": f"user{number}@example.invalid",
        "active": active,
        "usageStatus": status,
        "usage": {"fiveHour": five_hour, "sevenDay": {"pct": 99.0}},
        "usageAgeSeconds": age,
    }


def _listing(*accounts: dict, active_number: int | None = None) -> dict:
    if active_number is None:
        active_number = next((a["number"] for a in accounts if a["active"]), 0)
    return {"schemaVersion": 1, "activeAccountNumber": active_number, "accounts": list(accounts)}


def _gate(
    tmp_path: Path, listing: object, *, rc: int = 0, **env_extra: str
) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "cswap"
    stub.write_text(FAKE_CSWAP, encoding="utf-8")
    stub.chmod(0o755)
    data = tmp_path / "list.json"
    data.write_text(listing if isinstance(listing, str) else json.dumps(listing), encoding="utf-8")
    log = tmp_path / "cswap.log"
    log.write_text("", encoding="utf-8")
    env = {k: v for k, v in _base_env().items() if not k.startswith("CSWAP_GATE_")}
    env |= {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_CSWAP_JSON": str(data),
        "FAKE_CSWAP_RC": str(rc),
        "CSWAP_LOG": str(log),
        **env_extra,
    }
    result = subprocess.run(
        ["bash", str(GATE)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return result, log.read_text(encoding="utf-8").splitlines()


def test_allows_below_threshold_and_only_lists(tmp_path):
    result, calls = _gate(tmp_path, _listing(_account(2, 14.0, active=True)))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("CSWAP GATE ALLOW: active account #2 user2@example.invalid 5h window is 14% used")
    assert calls == ["list --json"], "the gate must never switch, run or configure accounts"


@pytest.mark.parametrize("pct", [80.0, 80.4, 97.0, 100.0])
def test_refuses_at_or_above_threshold_with_percent_and_reset(tmp_path, pct):
    result, calls = _gate(tmp_path, _listing(_account(2, pct, active=True)))
    assert result.returncode == 1
    message = result.stderr.strip()
    assert message.startswith("CSWAP GATE REFUSE: ")
    shown = f"{pct:g}"
    assert f"5h window is {shown}% used (threshold 80%)" in message
    assert "resets 05:39 (in 4h 37m" in message
    assert "Not switching accounts" in message
    assert calls == ["list --json"]


def test_just_below_threshold_is_allowed(tmp_path):
    result, _ = _gate(tmp_path, _listing(_account(2, 79.9, active=True)))
    assert result.returncode == 0, result.stderr


def test_threshold_is_configurable(tmp_path):
    result, _ = _gate(tmp_path, _listing(_account(2, 50.0, active=True)), CSWAP_GATE_MAX_PCT="50")
    assert result.returncode == 1
    assert "threshold 50%" in result.stderr


def test_reads_the_active_account_not_others(tmp_path):
    listing = _listing(_account(1, 99.0, active=False), _account(2, 10.0, active=True))
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 0, result.stderr
    assert "#2 " in result.stdout


def test_falls_back_to_active_account_number(tmp_path):
    listing = _listing(_account(1, 10.0, active=False), _account(3, 90.0, active=False), active_number=3)
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 1
    assert "#3 " in result.stderr


# Review 2, finding 5g: the first row matching the flag OR the number was
# used, so a flagged account at 10% hid the numbered active account at 99%.
def test_refuses_when_active_flag_and_number_disagree(tmp_path):
    listing = _listing(_account(1, 10.0, active=True), _account(2, 99.0, active=False), active_number=2)
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 1, result.stdout
    assert result.stderr.startswith("CSWAP GATE REFUSE: cswap list marks 2 accounts as active (numbers [1,2]")


def test_refuses_when_two_rows_are_flagged_active(tmp_path):
    listing = _listing(_account(1, 10.0, active=True), _account(2, 99.0, active=True), active_number=1)
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 1, result.stdout
    assert "marks 2 accounts as active" in result.stderr


def test_missing_active_number_does_not_match_an_unnumbered_row(tmp_path):
    row = _account(1, 10.0, active=False)
    del row["number"]
    listing = {"schemaVersion": 1, "accounts": [row]}
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 1, result.stdout
    assert result.stderr.startswith("CSWAP GATE REFUSE: cswap list shows no active account")


def test_flag_and_number_agreeing_still_allows(tmp_path):
    listing = _listing(_account(1, 99.0, active=False), _account(2, 10.0, active=True), active_number=2)
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 0, result.stderr
    assert "#2 " in result.stdout


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        (_listing(_account(1, 10.0, active=False), active_number=9), "no active account"),
        (_listing(_account(2, None, active=True)), "has no 5h window reading"),
        (_listing(_account(2, 10.0, active=True, status="unavailable")), "usage status is 'unavailable'"),
        (_listing(_account(2, 10.0, active=True, age=5000.0)), "5h reading is 5000s old"),
        ("not json at all", "no active account"),
    ],
)
def test_fails_closed_on_missing_or_stale_data(tmp_path, listing, expected):
    result, _ = _gate(tmp_path, listing)
    assert result.returncode == 1
    assert result.stderr.startswith("CSWAP GATE REFUSE: ")
    assert expected in result.stderr


# Review 2, finding 5h: stderr was merged into the JSON, so any warning made
# jq fail and the gate refused a healthy reading.
def test_stderr_warning_does_not_break_the_reading(tmp_path):
    listing = _listing(_account(2, 14.0, active=True))
    result, _ = _gate(tmp_path, listing, FAKE_CSWAP_STDERR="warning: keychain slow")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("CSWAP GATE ALLOW: ")


def test_cswap_failure_shows_its_stderr(tmp_path):
    result, _ = _gate(tmp_path, "", rc=3, FAKE_CSWAP_STDERR="error: no accounts configured")
    assert result.returncode == 1
    assert result.stderr.startswith("CSWAP GATE REFUSE: cswap list --json failed: error: no accounts configured")


def test_refuses_when_cswap_fails(tmp_path):
    result, _ = _gate(tmp_path, "boom", rc=2)
    assert result.returncode == 1
    assert "cswap list --json failed" in result.stderr


def test_never_reads_aiuse(tmp_path):
    source = GATE.read_text(encoding="utf-8")
    code = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    assert not any("aiuse" in line for line in code)
    assert "cswap switch" not in source and "cswap auto" not in "\n".join(code)


def _raw_listing(*, pct: str = "14.0", age: str | None = "30") -> str:
    """A one-account listing with the 5h pct and usageAgeSeconds as raw JSON text.

    Raw text lets a test send values json.dumps cannot (1e400) and drop the age
    field entirely (age=None), the way real cswap does when the age is unknown.
    """
    age_field = "" if age is None else f', "usageAgeSeconds": {age}'
    return (
        '{"schemaVersion": 1, "activeAccountNumber": 2, "accounts": [{"number": 2,'
        ' "email": "user2@example.invalid", "active": true, "usageStatus": "ok",'
        f' "usage": {{"fiveHour": {{"pct": {pct}, "resetsAt": "2026-10-09T09:39:59+00:00",'
        f' "countdown": "4h 37m", "clock": "05:39"}}}}{age_field}}}]}}'
    )


# Review 2, finding 5a: a missing, null, unparsable or negative usageAgeSeconds
# used to skip the freshness check and ALLOW.
@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (None, "no reading age"),
        ("null", "no reading age"),
        ('"99999s"', "unparsable reading age '99999s'"),
        ('"abc"', "unparsable reading age 'abc'"),
        ("-50000", "unparsable reading age '-50000'"),
        ("1e400", "unparsable reading age"),
    ],
)
def test_refuses_missing_or_unparsable_age(tmp_path, age, expected):
    result, _ = _gate(tmp_path, _raw_listing(age=age))
    assert result.returncode == 1, result.stdout
    assert result.stderr.startswith("CSWAP GATE REFUSE: ")
    assert expected in result.stderr
    assert "ALLOW" not in result.stdout


@pytest.mark.parametrize("age", ["0", "30", "900", "899.5"])
def test_fresh_ages_still_allow(tmp_path, age):
    result, _ = _gate(tmp_path, _raw_listing(age=age))
    assert result.returncode == 0, result.stderr


def test_age_just_over_the_limit_refuses(tmp_path):
    result, _ = _gate(tmp_path, _raw_listing(age="900.5"))
    assert result.returncode == 1
    assert "5h reading is 900s old (> 900s)" in result.stderr


# Review 2, finding 5b: a pct that is not a plain number made `[ -ge ]` error
# out, the `if` read that as false, and the gate fell through to ALLOW.
@pytest.mark.parametrize(
    "pct",
    ['"NaN"', "1e400", "-5", "-0.5", '"12abc"', '"0x10"', "1000", "true", "[]", '""'],
)
def test_refuses_non_numeric_or_out_of_range_percent(tmp_path, pct):
    result, _ = _gate(tmp_path, _raw_listing(pct=pct))
    assert result.returncode == 1, result.stdout
    assert result.stderr.startswith("CSWAP GATE REFUSE: ")
    assert "ALLOW" not in result.stdout


@pytest.mark.parametrize(
    ("pct", "shown"), [("0", "0"), ("0.0", "0"), ("14", "14"), ('"14.5"', "14.5"), ("79.99", "79.99")]
)
def test_plain_percent_values_still_allow(tmp_path, pct, shown):
    result, _ = _gate(tmp_path, _raw_listing(pct=pct))
    assert result.returncode == 0, result.stderr
    assert f"5h window is {shown}% used" in result.stdout


def test_gate_allows_only_on_a_positive_check():
    """The ALLOW line must be reachable only through an explicit numeric pass."""
    source = GATE.read_text(encoding="utf-8")
    assert '[[ $used =~ ^[0-9]+$ ]] && [ "$used" -lt "$max_pct" ]' in source
