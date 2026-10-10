import requests

from aiuse.collectors.sipb import collect_sipb
from aiuse.models import BillingKind


def test_sipb_up_fast(monkeypatch):
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code

        def json(self):
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP Error {self.status_code}")

    def mock_get(url, *args, **kwargs):
        if url.endswith("/config"):
            return MockResponse({"status": True, "name": "LLMS-DEV-1", "version": "0.6.30"})
        return MockResponse({})

    monkeypatch.setattr(requests, "get", mock_get)

    accounts = collect_sipb(env={})
    assert len(accounts) == 1
    assert accounts[0].provider == "sipb"
    assert accounts[0].billing_kind == BillingKind.UNKNOWN
    assert accounts[0].error is None
    assert any("Server is UP" in n for n in accounts[0].notes)
    assert any("use sparingly / as a last resort" in n.lower() for n in accounts[0].notes)


def test_sipb_up_slow(monkeypatch):
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code

        def json(self):
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP Error {self.status_code}")

    import time

    def mock_get(url, *args, **kwargs):
        time.sleep(0.1)  # Simulate slow
        if url.endswith("/config"):
            return MockResponse({"status": True, "name": "LLMS-DEV-1"})
        return MockResponse({})

    monkeypatch.setattr(requests, "get", mock_get)

    accounts = collect_sipb(env={})
    assert len(accounts) == 1
    assert accounts[0].error is None
    assert any("Server is UP" in n for n in accounts[0].notes)


def test_sipb_down_connect_error(monkeypatch):
    def mock_get(url, *args, **kwargs):
        raise requests.ConnectionError("Connection refused")

    monkeypatch.setattr(requests, "get", mock_get)

    accounts = collect_sipb(env={})
    assert len(accounts) == 1
    assert accounts[0].error is not None
    assert "Connection refused" in accounts[0].error
    assert any("use sparingly / as a last resort" in n.lower() for n in accounts[0].notes)


def test_sipb_down_503(monkeypatch):
    class MockResponse:
        def raise_for_status(self):
            raise requests.HTTPError("503 Server Error")

    def mock_get(url, *args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(requests, "get", mock_get)

    accounts = collect_sipb(env={})
    assert len(accounts) == 1
    assert accounts[0].error is not None
    assert "503 Server Error" in accounts[0].error


def test_sipb_authd_success(monkeypatch):
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code

        def json(self):
            return self._json_data

        def raise_for_status(self):
            pass

    def mock_get(url, *args, **kwargs):
        if url.endswith("/config"):
            return MockResponse({"status": True})
        if url.endswith("/models"):
            assert kwargs["headers"]["Authorization"] == "Bearer fake-test-key"
            return MockResponse({"data": [{"id": "model1"}, {"id": "model2"}]})
        return MockResponse({})

    monkeypatch.setattr(requests, "get", mock_get)

    accounts = collect_sipb(env={"MIT_SIPB_API_KEY": "fake-test-key"})
    assert len(accounts) == 1
    assert accounts[0].error is None
    assert any("2 models available" in n for n in accounts[0].notes)
