"""omp balance from the Admin API cost report: canned pages only, no network."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest

from aiuse import anthropic_cost as ac
from aiuse import credits_override as co
from aiuse.analysis.selfdescribe import enrich_snapshot
from aiuse.models import Snapshot

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
GRANTED = datetime(2026, 10, 13, tzinfo=UTC)
EXPIRES = datetime(2026, 11, 8, tzinfo=UTC)
FAKE_KEY = "sk-ant-admin-TESTONLY"
ENV = {ac.KEY_ENV: FAKE_KEY}


def _page(amounts, *, next_page=None):
    return {
        "data": [
            {"starting_at": "x", "ending_at": "y", "results": [{"amount": a, "currency": "USD"} for a in amounts]}
        ],
        "has_more": next_page is not None,
        "next_page": next_page,
    }


class Canned:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        status, doc = self.responses.pop(0)
        return status, doc if isinstance(doc, bytes) else json.dumps(doc).encode()


def _seed_grant(remaining=95.57):
    co.save_override(
        "omp",
        remaining=remaining,
        total=200.0,
        expires=EXPIRES,
        note="console",
        now=NOW - timedelta(days=1),
        granted=GRANTED,
    )


def test_two_pages_are_summed_in_dollars_and_paged_by_next_page():
    fetch = Canned((200, _page(["5000.5", "1000"], next_page="page_2")), (200, _page(["2500"])))
    spent = ac.fetch_spend_usd(FAKE_KEY, GRANTED, fetch=fetch)
    assert spent == pytest.approx(85.005)
    first, second = (urllib.parse.parse_qs(urllib.parse.urlparse(c[0]).query) for c in fetch.calls)
    assert first["starting_at"] == ["2026-10-13T00:00:00Z"] and first["bucket_width"] == ["1d"]
    assert "page" not in first and second["page"] == ["page_2"]
    for url, headers, timeout in fetch.calls:
        assert url.startswith(ac.COST_REPORT_URL + "?")
        assert headers["x-api-key"] == FAKE_KEY and headers["anthropic-version"] == "2023-06-01"
        assert timeout == 20.0


def test_clamping():
    assert ac.compute_remaining(200.0, 104.43) == pytest.approx(95.57)
    assert ac.compute_remaining(200.0, 250.0) == 0.0
    assert ac.compute_remaining(200.0, 0.0) == 200.0


def test_refresh_writes_an_admin_api_reading_and_it_beats_an_older_override(capsys):
    _seed_grant()
    fetch = Canned((200, _page(["6000", "4443"], next_page="p2")), (200, _page(["0"])))
    code, line = ac.refresh(env=ENV, fetch=fetch, now=NOW)
    assert code == 0 and "$95.57 of $200.00" in line
    entry = co.load_overrides()["omp"]
    assert entry["source"] == "admin-api" and entry["observed_at"] == NOW.isoformat()
    assert entry["remaining_usd"] == pytest.approx(95.57)
    assert entry["granted_at"] == GRANTED.isoformat()
    out = capsys.readouterr()
    assert FAKE_KEY not in out.out + out.err + line
    assert FAKE_KEY not in ac.readings_path().read_text()
    assert FAKE_KEY not in co.overrides_path().read_text()


def test_override_stays_the_fallback_when_it_is_fresher_than_the_reading():
    _seed_grant(remaining=95.57)
    ac.refresh(env=ENV, fetch=Canned((200, _page(["1000"]))), now=NOW - timedelta(days=3))
    entry = co.load_overrides()["omp"]  # override observed NOW-1d beats a reading from NOW-3d
    assert entry["source"] == "operator-override"


def test_failure_keeps_the_previous_reading(capsys):
    _seed_grant()
    ac.refresh(env=ENV, fetch=Canned((200, _page(["10000"]))), now=NOW - timedelta(hours=1))
    before = ac.readings_path().read_text()
    for response in [(500, b""), (429, b""), (200, b"not json"), (200, {"data": "nope"})]:
        code, line = ac.refresh(env=ENV, fetch=Canned(response), now=NOW)
        assert code == 1 and line.startswith("aiuse: credits refresh failed")
        assert FAKE_KEY not in line
    assert ac.readings_path().read_text() == before


def test_network_error_is_reported_without_headers():
    _seed_grant()

    def boom(url, headers, timeout):
        raise ac.CostReportError("request failed (TimeoutError)")

    code, line = ac.refresh(env=ENV, fetch=boom, now=NOW)
    assert code == 1 and "TimeoutError" in line and FAKE_KEY not in line


def test_never_runs_without_the_env_var():
    _seed_grant()
    fetch = Canned()
    code, line = ac.refresh(env={}, fetch=fetch, now=NOW)
    assert code == 2 and ac.KEY_ENV in line
    assert fetch.calls == [] and not ac.readings_path().exists()
    code, _ = ac.refresh(env={ac.KEY_ENV: ""}, fetch=fetch, now=NOW)
    assert code == 2 and fetch.calls == []


def test_cli_without_key_exits_2_and_never_calls_the_network(monkeypatch, capsys):
    monkeypatch.delenv(ac.KEY_ENV, raising=False)
    monkeypatch.setattr(ac, "_http_get", lambda *a, **k: pytest.fail("network call without a key"))
    assert ac.run_refresh_credits(["omp"]) == 2
    assert ac.KEY_ENV in capsys.readouterr().err


def test_no_grant_parameters_means_not_run():
    fetch = Canned()
    code, line = ac.refresh(env=ENV, fetch=fetch, now=NOW)
    assert code == 2 and "set-credits" in line and fetch.calls == []
    co.save_override("omp", remaining=1.0, total=200.0, expires=EXPIRES, note=None)  # no --granted
    assert ac.refresh(env=ENV, fetch=fetch, now=NOW)[0] == 2


def test_spend_over_total_clamps_remaining_to_zero():
    _seed_grant()
    code, _ = ac.refresh(env=ENV, fetch=Canned((200, _page(["99999"]))), now=NOW)
    assert code == 0 and co.load_overrides()["omp"]["remaining_usd"] == 0.0


def test_admin_api_reading_decorates_the_row_and_may_make_it():
    _seed_grant()
    ac.refresh(env=ENV, fetch=Canned((200, _page(["10443"]))), now=datetime.now(UTC))
    snap = Snapshot(collected_at=datetime.now(UTC), accounts=[]).to_dict()
    enrich_snapshot(snap, credit_overrides=co.load_overrides())
    (omp,) = snap["accounts"]
    assert omp["source"] == "admin-api" and omp["balance_usd"] == pytest.approx(95.57)


def test_set_credits_granted_roundtrip_and_bad_date():
    assert co.run_set_credits(["omp", "--remaining", "1", "--total", "2", "--granted", "2026-10-13"]) == 0
    assert co.load_grant_params() == (2.0, GRANTED, None)
    assert co.run_set_credits(["omp", "--remaining", "1", "--total", "2", "--granted", "never"]) == 2
