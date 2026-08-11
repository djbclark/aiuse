import json
from types import SimpleNamespace

import pytest
import requests

from aiuse.collectors.base import CollectorError
from aiuse.collectors.openrouter import collect_openrouter
from aiuse.models import BillingKind


def test_collect_openrouter_is_quiet_until_key_is_explicitly_supplied():
    assert collect_openrouter(environ={}) == []


def test_collect_openrouter_returns_prepaid_account(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        status_code = 200
        
        def json(self):
            return {"data": {"total_usage": 10.5, "total_credits": 50.0}}
            
        def raise_for_status(self):
            pass

    def fake_get(url, timeout, headers):
        calls.append((url, headers))
        assert timeout == 12
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    accounts = collect_openrouter(timeout=12, environ={"AIUSE_OPENROUTER_MANAGEMENT_KEY": "sk-or-example"})

    assert len(calls) == 1
    assert calls[0][0] == "https://openrouter.ai/api/v1/credits"
    assert calls[0][1]["Authorization"] == "Bearer sk-or-example"
    
    assert len(accounts) == 1
    assert accounts[0].provider == "openrouter"
    assert accounts[0].source == "openrouter_api"
    assert accounts[0].billing_kind == BillingKind.PREPAID_BALANCE
    assert accounts[0].balance_usd == 39.5
    assert "sk-or-example" not in str(accounts[0])


def test_collect_openrouter_handles_negative_or_malformed_values(monkeypatch):
    class FakeResponse:
        status_code = 200
        
        def json(self):
            return {"data": {"total_usage": 60.0, "total_credits": 50.0}}
            
        def raise_for_status(self):
            pass

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    accounts = collect_openrouter(environ={"AIUSE_OPENROUTER_MANAGEMENT_KEY": "sk-or-example"})
    assert accounts[0].balance_usd == 0.0

    class FakeMissingData:
        status_code = 200
        def json(self): return {"data": {}}
        def raise_for_status(self): pass

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FakeMissingData())
    with pytest.raises(CollectorError, match="missing numeric total_credits"):
        collect_openrouter(environ={"AIUSE_OPENROUTER_MANAGEMENT_KEY": "sk-or-example"})


def test_collect_openrouter_handles_401_403(monkeypatch):
    class FakeResponse401:
        status_code = 401
        
        def json(self):
            return {}
            
        def raise_for_status(self):
            raise requests.HTTPError("401 Client Error")

    def fake_get(*args, **kwargs):
        return FakeResponse401()

    monkeypatch.setattr(requests, "get", fake_get)

    accounts = collect_openrouter(environ={"AIUSE_OPENROUTER_MANAGEMENT_KEY": "sk-or-example"})
    assert len(accounts) == 1
    assert accounts[0].error is not None
    assert "rejected the key" in accounts[0].error
    assert "sk-or-example" not in str(accounts[0])


def test_collect_openrouter_uses_secretspec_key_when_no_override(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout="sk-or-secret\n")

    class FakeResponse:
        status_code = 200
        def json(self): return {"data": {"total_credits": 100, "total_usage": 0}}
        def raise_for_status(self): pass

    def fake_get(url, timeout, headers):
        assert headers["Authorization"] == "Bearer sk-or-secret"
        return FakeResponse()

    monkeypatch.setattr("aiuse.collectors.openrouter.shutil.which", lambda _name: "/usr/bin/secretspec")
    monkeypatch.setattr("aiuse.collectors.openrouter.subprocess.run", fake_run)
    monkeypatch.setattr(requests, "get", fake_get)

    monkeypatch.setenv("SECRETSPEC_FILE", "/tmp/aiuse-secretspec.toml")
    accounts = collect_openrouter()

    assert seen == [
        [
            "/usr/bin/secretspec",
            "get",
            "--file",
            "/tmp/aiuse-secretspec.toml",
            "--reason",
            "aiuse OpenRouter balance collection",
            "OPENROUTER_MANAGEMENT_KEY",
        ]
    ]
    assert accounts[0].balance_usd == 100.0
    assert "sk-or-secret" not in str(accounts[0])
