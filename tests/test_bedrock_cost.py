from datetime import UTC, datetime, timedelta

import pytest

from aiuse.collectors.base import CollectorError
from aiuse.collectors.bedrock_cost import BedrockCostClient
from aiuse.collectors.bedrock_gate import gated_bedrock_payload

NOW = datetime(2026, 10, 29, 20, tzinfo=UTC)
ACCOUNT = "828035973072"


def _page(*groups, token=None):
    return {
        "ResultsByTime": [
            {
                "Groups": [
                    {"Keys": [service, kind], "Metrics": {"UnblendedCost": {"Amount": str(amount), "Unit": "USD"}}}
                    for service, kind, amount in groups
                ],
                "Estimated": True,
            }
        ],
        **({"NextPageToken": token} if token else {}),
    }


def _client(monkeypatch, pages):
    monkeypatch.setattr("aiuse.collectors.bedrock_cost.shutil.which", lambda *a, **k: "/usr/bin/aws")
    c = BedrockCostClient(env={"AWS_PROFILE": "billing"})
    calls = []

    def run(args):
        calls.append(args)
        if args[0] == "sts":
            return {"Account": ACCOUNT}
        return pages.pop(0)

    monkeypatch.setattr(c, "_run", run)
    return c, calls


def test_credit_offset_keeps_gross_and_net_distinct(monkeypatch):
    c, calls = _client(
        monkeypatch,
        [
            _page(
                ("Amazon Bedrock", "Usage", 3.44721901),
                ("Amazon Bedrock", "Credit", -3.44721901),
                ("AWS Cost Explorer", "Usage", 13.44),
            )
        ],
    )
    row = c.fetch(now=NOW)[0]
    metadata = row["usage"]["bedrockBilling"]
    assert row["usage"]["providerCost"]["used"] == 3.44721901
    assert metadata == {
        "account_id": ACCOUNT,
        "billing_month": "2026-10",
        "gross_usd": 3.44721901,
        "credits_usd": -3.44721901,
        "net_usd": 0,
        "estimated": True,
    }
    assert "Type=DIMENSION,Key=RECORD_TYPE" in calls[1]
    assert "Start=2026-10-01,End=2026-10-30" in calls[1]
    c.account_id()
    assert len(calls) == 2  # identity reused


def test_native_zero_is_a_measured_zero(monkeypatch):
    c, _ = _client(monkeypatch, [_page()])
    assert c.fetch(now=NOW)[0]["usage"]["bedrockBilling"]["gross_usd"] == 0


def test_missing_period_is_unknown(monkeypatch):
    c, _ = _client(monkeypatch, [{"ResultsByTime": []}])
    with pytest.raises(CollectorError, match="unavailable"):
        c.fetch(now=NOW)


def test_pagination_and_repeated_token(monkeypatch):
    c, calls = _client(
        monkeypatch, [_page(("Amazon Bedrock", "Usage", 2), token="next"), _page(("Amazon Bedrock", "Credit", -1))]
    )
    assert c.fetch(now=NOW)[0]["usage"]["bedrockBilling"]["net_usd"] == 1
    assert calls[-1][-2:] == ["--next-page-token", "next"]
    c, _ = _client(monkeypatch, [_page(token="same"), _page(token="same")])
    with pytest.raises(CollectorError, match="pagination"):
        c.fetch(now=NOW)


def test_nonfinite_amount_is_not_a_reading(monkeypatch):
    c, _ = _client(monkeypatch, [_page(("Amazon Bedrock", "Usage", "nan"))])
    with pytest.raises(CollectorError, match="malformed"):
        c.fetch(now=NOW)


def _native(amount):
    return [
        {
            "provider": "bedrock",
            "usage": {
                "updatedAt": NOW.isoformat(),
                "providerCost": {"used": amount},
                "bedrockBilling": {
                    "account_id": ACCOUNT,
                    "billing_month": "2026-10",
                    "gross_usd": amount,
                    "credits_usd": -amount,
                    "net_usd": 0,
                    "estimated": True,
                },
            },
        }
    ]


def test_scoped_cache_preserves_native_zero_and_metadata(tmp_path):
    p = tmp_path / "spend.json"

    def read():
        return _native(0)

    first = gated_bedrock_payload(read, now=NOW, store=p, account_id=ACCOUNT, billing_month="2026-10")
    held = gated_bedrock_payload(
        lambda: pytest.fail("duplicate billed read"),
        now=NOW + timedelta(minutes=1),
        store=p,
        account_id=ACCOUNT,
        billing_month="2026-10",
    )
    assert held[0]["usage"]["bedrockBilling"] == first[0]["usage"]["bedrockBilling"]
    assert held[0]["usage"]["providerCost"]["used"] == 0


def test_scoped_cache_account_and_month_changes_invalidate(tmp_path):
    p = tmp_path / "spend.json"
    gated_bedrock_payload(lambda: _native(10), now=NOW, store=p, account_id=ACCOUNT, billing_month="2026-10")
    calls = []

    def read():
        calls.append(1)
        return _native(0)

    row = gated_bedrock_payload(read, now=NOW, store=p, account_id="123456789012", billing_month="2026-10")
    assert row[0]["usage"]["providerCost"]["used"] == 0
    row = gated_bedrock_payload(
        read, now=NOW.replace(month=11, day=1), store=p, account_id="123456789012", billing_month="2026-11"
    )
    assert row[0]["usage"]["providerCost"]["used"] == 0
    assert len(calls) == 2


def test_legacy_ambiguous_zero_not_fabricated(tmp_path):
    p = tmp_path / "spend.json"
    p.write_text('{"schema_version":1,"spent_usd":0,"fetched_at":"2026-10-29T20:00:00+00:00"}')
    result = gated_bedrock_payload(
        lambda: pytest.fail("legacy hold must retain six-hour cap"),
        now=NOW,
        store=p,
        account_id=ACCOUNT,
        billing_month="2026-10",
    )
    assert isinstance(result, CollectorError)


def test_concurrent_collects_share_one_billed_read(tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor

    p = tmp_path / "spend.json"
    calls = []

    def read():
        calls.append(1)
        time.sleep(0.05)
        return _native(3)

    def collect():
        return gated_bedrock_payload(read, now=NOW, store=p, account_id=ACCOUNT, billing_month="2026-10")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(collect), pool.submit(collect)]
        results = [f.result() for f in futures]
    assert len(calls) == 1
    assert [r[0]["usage"]["providerCost"]["used"] for r in results] == [3, 3]


def test_subprocess_failure_does_not_expose_credentials(monkeypatch):
    import subprocess

    monkeypatch.setattr("aiuse.collectors.bedrock_cost.shutil.which", lambda *a, **k: "/usr/bin/aws")
    c = BedrockCostClient(env={"AWS_PROFILE": "billing"})
    monkeypatch.setattr(
        "aiuse.collectors.bedrock_cost.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "SECRET credential (AccessDeniedException)"),
    )
    with pytest.raises(CollectorError, match="AccessDeniedException") as err:
        c.account_id()
    assert "SECRET" not in str(err.value)


def _credential_client(monkeypatch, outcomes):
    import subprocess

    calls = []
    monkeypatch.setattr("aiuse.collectors.bedrock_cost.shutil.which", lambda name, **k: f"/tools/{name}")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        returncode, stdout, stderr = outcomes.pop(0)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr("aiuse.collectors.bedrock_cost.subprocess.run", run)
    return BedrockCostClient(env={}), calls


def test_default_aws_credentials_precede_optional_broker(monkeypatch):
    c, calls = _credential_client(monkeypatch, [(0, '{"Account":"828035973072"}', "")])
    assert c.account_id() == ACCOUNT
    assert len(calls) == 1
    assert calls[0][0][0] == "/tools/aws"


def test_missing_credentials_uses_broker_once_with_same_deadline(monkeypatch):
    c, calls = _credential_client(
        monkeypatch, [(253, "", "Unable to locate credentials"), (0, '{"Account":"828035973072"}', "")]
    )
    assert c.account_id() == ACCOUNT
    assert [call[0][0] for call in calls] == ["/tools/aws", "/tools/sudo-secretspec"]
    assert calls[1][1]["timeout"] <= calls[0][1]["timeout"]
    assert len(calls) == 2


def test_missing_credentials_broker_failure_does_not_recurse(monkeypatch):
    c, calls = _credential_client(
        monkeypatch, [(253, "", "Unable to locate credentials"), (253, "", "Unable to locate credentials")]
    )
    with pytest.raises(CollectorError):
        c.account_id()
    assert len(calls) == 2


def test_access_denied_never_switches_to_broker(monkeypatch):
    c, calls = _credential_client(monkeypatch, [(254, "", "An error occurred (AccessDeniedException)")])
    with pytest.raises(CollectorError, match="AccessDeniedException"):
        c.account_id()
    assert len(calls) == 1
    assert calls[0][0][0] == "/tools/aws"


def test_timeout_never_switches_to_broker(monkeypatch):
    import subprocess

    c, _ = _credential_client(monkeypatch, [])
    calls = []

    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr("aiuse.collectors.bedrock_cost.subprocess.run", timeout)
    with pytest.raises(CollectorError, match="TimeoutExpired"):
        c.account_id()
    assert len(calls) == 1
