import json
from types import SimpleNamespace

import pytest
import requests

from aiuse.collectors.base import CollectorError
from aiuse.collectors.muse import collect_muse
from aiuse.models import BillingKind


def test_collect_muse_is_quiet_until_key_is_explicitly_supplied():
    assert collect_muse(environ={}) == []


def test_collect_muse_returns_balance_via_override_url(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"total_credits": 50.0, "total_usage": 10.5}}

        def raise_for_status(self):
            pass

    def fake_get(url, timeout, headers):
        calls.append((url, headers))
        assert timeout == 12
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    accounts = collect_muse(
        timeout=12,
        environ={
            "AIUSE_MUSE_API_KEY": "sk-test",
            "AIUSE_MUSE_API_URL": "https://api.meta.ai/v1/usage",
        },
    )

    assert len(calls) == 1
    assert calls[0][0] == "https://api.meta.ai/v1/usage"
    assert calls[0][1]["Authorization"] == "Bearer sk-test"
    assert len(accounts) == 1
    assert accounts[0].provider == "muse"
    assert accounts[0].source == "muse"
    assert accounts[0].billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API)
    assert accounts[0].balance_usd == pytest.approx(39.5)


def test_collect_muse_handles_generic_balance_shape(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"balance": 18.25}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    accounts = collect_muse(
        environ={
            "META_API_KEY": "sk-meta",
            "AIUSE_MUSE_API_URL": "https://api.meta.ai/v1/credits",
        }
    )
    assert accounts[0].balance_usd == pytest.approx(18.25)


def test_collect_muse_handles_windows_shape(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "limits": [
                    {"type": "five_hour", "percentUsed": 12.0, "resetsAt": "2026-08-21T20:00:00Z"},
                    {"type": "weekly", "percentUsed": 45.0, "resetsAt": "2026-08-28T00:00:00Z"},
                ]
            }

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    accounts = collect_muse(
        environ={"AIUSE_MUSE_API_KEY": "sk-test", "AIUSE_MUSE_API_URL": "https://api.meta.ai/v1/usage"}
    )
    assert len(accounts) == 1
    assert accounts[0].billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(accounts[0].windows) == 2
    labels = [w.label for w in accounts[0].windows]
    assert "Muse 5-hour" in labels
    assert "Muse weekly" in labels


def test_collect_muse_handles_401_403(monkeypatch):
    class FakeResponse401:
        status_code = 401

        def json(self):
            return {}

        def raise_for_status(self):
            raise requests.HTTPError("401")

    def fake_get(*args, **kwargs):
        return FakeResponse401()

    monkeypatch.setattr(requests, "get", fake_get)

    accounts = collect_muse(environ={"AIUSE_MUSE_API_KEY": "bad", "AIUSE_MUSE_API_URL": "https://api.meta.ai/v1/usage"})
    assert len(accounts) == 1
    assert accounts[0].error is not None
    assert "rejected the key" in accounts[0].error.lower() or "401" in accounts[0].error


def test_collect_muse_probes_candidates(monkeypatch):
    calls: list[str] = []

    class Fake404:
        status_code = 404

        def json(self):
            return {}

        def raise_for_status(self):
            raise requests.HTTPError("404")

    class FakeOK:
        status_code = 200

        def json(self):
            return {"data": {"total_credits": 20, "total_usage": 5}}

        def raise_for_status(self):
            pass

    def fake_get(url, timeout, headers):
        calls.append(url)
        if url.endswith("/billing/usage"):
            return FakeOK()
        if url.endswith("/usage"):
            return Fake404()
        return Fake404()

    monkeypatch.setattr(requests, "get", fake_get)
    accounts = collect_muse(environ={"META_API_KEY": "sk-test"})
    assert any(u.endswith("/billing/usage") for u in calls)
    assert accounts[0].balance_usd == pytest.approx(15.0)


def test_collect_muse_negative_balance_clamped(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"total_credits": 10, "total_usage": 20}}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    accounts = collect_muse(
        environ={"AIUSE_MUSE_API_KEY": "sk-test", "AIUSE_MUSE_API_URL": "https://api.meta.ai/v1/usage"}
    )
    assert accounts[0].balance_usd == pytest.approx(0.0)


def test_collect_muse_uses_secretspec_key_when_no_override(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout="sk-secretspec\n")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"total_credits": 100, "total_usage": 0}}

        def raise_for_status(self):
            pass

    def fake_get(url, timeout, headers):
        assert headers["Authorization"] == "Bearer sk-secretspec"
        return FakeResponse()

    monkeypatch.setattr("aiuse.collectors.muse.shutil.which", lambda _name: "/usr/bin/secretspec")
    monkeypatch.setattr("aiuse.collectors.muse.subprocess.run", fake_run)
    monkeypatch.setattr(requests, "get", fake_get)

    monkeypatch.setenv("SECRETSPEC_FILE", "/tmp/aiuse-secretspec.toml")
    accounts = collect_muse()

    assert any("MUSE_API_KEY" in cmd or "META_API_KEY" in cmd for cmd in seen[0])
    assert accounts[0].balance_usd == pytest.approx(100.0)


def test_collect_muse_raises_on_unrecognizable_payload(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"unknown_field": 123}}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    with pytest.raises(CollectorError):
        collect_muse(environ={"AIUSE_MUSE_API_KEY": "sk-test", "AIUSE_MUSE_API_URL": "https://api.meta.ai/v1/usage"})


def test_collect_muse_reads_cli_auth_json(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": {
                    "meta": {
                        "api_key": "LLM|from-cli",
                        "user_email": "muse-user@example.com",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    class Fake404:
        status_code = 404

        def raise_for_status(self):
            raise requests.HTTPError("404")

        def json(self):
            return {}

    class FakeModels:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"object": "list", "data": []}

    def fake_get(url, timeout, headers):
        calls.append(url)
        assert headers["Authorization"] == "Bearer LLM|from-cli"
        if url.endswith("/models"):
            return FakeModels()
        return Fake404()

    monkeypatch.setattr(requests, "get", fake_get)
    # Live-mode (environ=None) so auth.json is consulted; point it at the fixture.
    monkeypatch.setenv("MUSE_AUTH_PATH", str(auth_path))
    monkeypatch.delenv("AIUSE_MUSE_API_KEY", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.setattr("aiuse.collectors.muse.shutil.which", lambda _name: None)

    accounts = collect_muse(timeout=5)
    assert len(accounts) == 1
    assert accounts[0].provider == "muse"
    assert accounts[0].account == "muse-user@example.com"
    assert accounts[0].balance_usd is None
    assert accounts[0].billing_kind == BillingKind.PAYG_API
    assert any("billing endpoint" in n for n in accounts[0].notes)
    assert "LLM|from-cli" not in str(accounts[0])
    assert any(u.endswith("/models") for u in calls)


def test_collect_muse_soft_inventory_when_billing_paths_404(monkeypatch):
    class Fake404:
        status_code = 404

        def raise_for_status(self):
            raise requests.HTTPError("404")

        def json(self):
            return {}

    class FakeModels:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"object": "list", "data": []}

    def fake_get(url, timeout, headers):
        if url.endswith("/models"):
            return FakeModels()
        return Fake404()

    monkeypatch.setattr(requests, "get", fake_get)
    accounts = collect_muse(environ={"META_API_KEY": "sk-test"})
    assert len(accounts) == 1
    assert accounts[0].balance_usd is None
    assert "prepaid" in accounts[0].billing_kind.value or accounts[0].billing_kind == BillingKind.PAYG_API
    assert any("credential refresh muse" in n for n in accounts[0].notes)


def test_collect_muse_ignores_auth_json_when_environ_override(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"providers": {"meta": {"api_key": "LLM|should-not-use"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MUSE_AUTH_PATH", str(auth_path))
    assert collect_muse(environ={}) == []
