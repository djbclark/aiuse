"""omp balance from the Admin API cost report: canned pages only, no network."""

from __future__ import annotations

import http.server
import json
import threading
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
    entry = co.load_overrides(now=NOW)["omp"]
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
    entry = co.load_overrides(now=NOW)["omp"]  # override observed NOW-1d beats a reading from NOW-3d
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
    assert code == 0 and co.load_overrides(now=NOW)["omp"]["remaining_usd"] == 0.0


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


# --- pass 4: key hygiene, caps, redirects, age, merge order ---------------------------


def test_key_with_trailing_newline_is_trimmed_and_works():
    _seed_grant()
    fetch = Canned((200, _page(["10443"])))
    code, _ = ac.refresh(env={ac.KEY_ENV: FAKE_KEY + "\n"}, fetch=fetch, now=NOW)
    assert code == 0 and fetch.calls[0][1]["x-api-key"] == FAKE_KEY


@pytest.mark.parametrize("bad", ["SENT\nINEL-KEY", "SENT\rINEL", "has space", "semi;colon", "calf\u00e9"])
def test_key_with_bad_characters_is_refused_without_echoing_it(bad, capsys):
    _seed_grant()
    fetch = Canned()
    code, line = ac.refresh(env={ac.KEY_ENV: bad}, fetch=fetch, now=NOW)
    out = capsys.readouterr()
    assert code == 2 and fetch.calls == []
    for fragment in (bad, bad.strip(), "INEL", "SENT"):
        assert fragment not in line + out.out + out.err


def test_exception_text_with_the_key_never_reaches_output(capsys):
    _seed_grant()

    def leaky(url, headers, timeout):
        raise ValueError(f"Invalid header value {headers['x-api-key']!r}")

    code, line = ac.refresh(env=ENV, fetch=leaky, now=NOW)
    out = capsys.readouterr()
    assert code == 1 and "ValueError" in line
    assert FAKE_KEY not in line + out.out + out.err


def test_cli_with_newline_in_the_middle_prints_no_key(monkeypatch, capsys):
    _seed_grant()
    monkeypatch.setenv(ac.KEY_ENV, "SENTINEL\nKEY-3")
    monkeypatch.setattr(ac, "_http_get", lambda *a, **k: pytest.fail("network call"))
    assert ac.run_refresh_credits(["omp"]) == 2
    out = capsys.readouterr()
    assert "SENTINEL" not in out.out + out.err and "KEY-3" not in out.out + out.err


def test_empty_report_cannot_refill_the_grant_it_is_capped_at_the_override():
    _seed_grant(remaining=95.57)
    empty_bucket = {"data": [{"starting_at": "a", "ending_at": "b", "results": []}], "has_more": False}
    code, line = ac.refresh(env=ENV, fetch=Canned((200, empty_bucket)), now=NOW)
    assert code == 0 and "capped" in line and "$95.57 of $200.00" in line
    entry = co.load_overrides(now=NOW)["omp"]
    assert entry["remaining_usd"] == pytest.approx(95.57) and "capped" in entry["note"]
    assert "lower bound" in entry["note"]


def test_reading_is_capped_at_the_previous_reading():
    _seed_grant(remaining=95.57)
    ac.refresh(env=ENV, fetch=Canned((200, _page(["12000"]))), now=NOW - timedelta(hours=2))  # 80.00 left
    empty_bucket = {"data": [{"starting_at": "a", "ending_at": "b", "results": []}], "has_more": False}
    code, line = ac.refresh(env=ENV, fetch=Canned((200, empty_bucket)), now=NOW)
    assert code == 0 and "previous reading" in line
    assert json.loads(ac.readings_path().read_text())["omp"]["remaining_usd"] == pytest.approx(80.0)


def test_zero_buckets_since_the_grant_is_a_failure():
    _seed_grant()
    code, line = ac.refresh(env=ENV, fetch=Canned((200, {"data": [], "has_more": False})), now=NOW)
    assert code == 1 and "no buckets" in line
    assert not ac.readings_path().exists()


class _Redirector(http.server.BaseHTTPRequestHandler):
    seen_keys: list = []

    def do_GET(self):  # noqa: N802
        _Redirector.seen_keys.append(self.headers.get("x-api-key"))
        if self.path.startswith("/start"):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/final")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def test_redirects_are_refused_and_the_key_is_not_forwarded():
    _Redirector.seen_keys = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ac.CostReportError, match="redirect refused"):
            ac._http_get(f"http://127.0.0.1:{server.server_port}/start", {"x-api-key": FAKE_KEY}, 5.0)
    finally:
        server.shutdown()
        server.server_close()
    assert _Redirector.seen_keys == [FAKE_KEY]  # only the first hop ever saw it


def _write_reading(observed, remaining=50.0):
    entry = {
        "remaining_usd": remaining,
        "total_usd": 200.0,
        "granted_at": GRANTED.isoformat(),
        "expires_at": EXPIRES.isoformat(),
        "observed_at": observed.isoformat(),
        "source": "admin-api",
    }
    ac.readings_path().parent.mkdir(parents=True, exist_ok=True)
    ac.readings_path().write_text(json.dumps({"omp": entry}))


def test_admin_api_reading_has_the_24h_limit():
    _seed_grant()
    _write_reading(NOW - timedelta(hours=25))
    assert co.load_overrides(now=NOW)["omp"]["source"] == "operator-override"
    _write_reading(NOW - timedelta(hours=23))
    assert co.load_overrides(now=NOW)["omp"]["source"] == "admin-api"


def test_override_keeps_its_7_day_limit_while_a_reading_does_not_outlive_24h():
    co.save_override("omp", remaining=90.0, total=200.0, expires=EXPIRES, note=None, now=NOW - timedelta(days=6))
    assert co.load_overrides(now=NOW)["omp"]["source"] == "operator-override"
    _write_reading(NOW - timedelta(days=2))
    assert co.load_overrides(now=NOW)["omp"]["source"] == "operator-override"


def test_future_dated_reading_cannot_hide_a_valid_override():
    _seed_grant()
    _write_reading(datetime(2099, 1, 1, tzinfo=UTC))
    entry = co.load_overrides(now=NOW)["omp"]
    assert entry["source"] == "operator-override" and entry["remaining_usd"] == 95.57


def test_expired_reading_cannot_hide_a_valid_override():
    _seed_grant()
    _write_reading(NOW - timedelta(minutes=1))
    expired = json.loads(ac.readings_path().read_text())
    expired["omp"]["expires_at"] = (NOW - timedelta(days=1)).isoformat()
    ac.readings_path().write_text(json.dumps(expired))
    assert co.load_overrides(now=NOW)["omp"]["source"] == "operator-override"


def test_a_source_claimed_inside_the_override_file_is_ignored():
    co.overrides_path().parent.mkdir(parents=True, exist_ok=True)
    co.overrides_path().write_text(
        json.dumps(
            {"omp": {"remaining_usd": 1.0, "total_usd": 2.0, "observed_at": NOW.isoformat(), "source": "admin-api"}}
        )
    )
    assert co.load_overrides(now=NOW)["omp"]["source"] == "operator-override"
