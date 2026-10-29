"""Several ClinePass accounts: CodexBar --all-accounts, labelled native keys,
and per-account selection / cross-checks in the runner."""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

from aiuse.collectors import clinepass
from aiuse.collectors.codexbar import _query_provider, collect_codexbar
from aiuse.collectors.runner import _match_peer_account, _provider_names, _select_and_cross_check, run_collectors
from aiuse.models import AccountUsage, BillingKind, QuotaWindow

_SECRET_VALUES = ("sk-env-te", "sk-plain-dc", "sk-sudo-dc", "sk-legacy")


def _usage(used: float) -> dict[str, Any]:
    return {
        "primary": {"usedPercent": used, "windowMinutes": 300},
        "secondary": {"usedPercent": used, "windowMinutes": 10080},
        "tertiary": {"usedPercent": used, "windowMinutes": 43200},
    }


def _codexbar_row(account: str, used: float = 10.0) -> dict[str, Any]:
    return {"provider": "clinepass", "source": "api", "account": account, "usage": _usage(used)}


_NO_TOKEN_ACCOUNTS = [
    {
        "provider": "cursor",
        "error": {"code": 1, "message": "No token accounts configured for cursor.", "kind": "provider"},
        "source": "auto",
    }
]


class _FakeCodexBar:
    """Stand-in for `run_json`: records argv and answers per provider/flag."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], *, timeout: float = 90.0, allow_empty: bool = False) -> Any:
        self.calls.append(list(argv))
        # A discovery collect also runs `codexbar config providers` (no
        # --provider). An empty catalog lists no Bedrock, so the Cost Explorer
        # gate stays idle and the usage assertions below stay about usage.
        if "--provider" not in argv:
            return []
        provider = argv[argv.index("--provider") + 1]
        every = "--all-accounts" in argv
        if provider == "clinepass":
            if every:
                return [_codexbar_row("technoeuphora"), _codexbar_row("dc", 40.0)]
            return [_codexbar_row("technoeuphora")]
        if provider == "cursor":
            if every:
                return _NO_TOKEN_ACCOUNTS
            return [{"provider": "cursor", "source": "web", "usage": {"primary": {"usedPercent": 5}}}]
        return [{"provider": provider, "source": "oauth", "usage": {"primary": {"usedPercent": 1}}}]


@pytest.fixture
def fake_codexbar(monkeypatch: pytest.MonkeyPatch) -> _FakeCodexBar:
    fake = _FakeCodexBar()
    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake)
    monkeypatch.setattr("aiuse.collectors.codexbar.which", lambda _name: "/usr/bin/codexbar")
    return fake


# --- CodexBar ---------------------------------------------------------------


def test_all_accounts_flag_only_for_configured_providers(fake_codexbar: _FakeCodexBar) -> None:
    accounts = collect_codexbar(
        providers="clinepass,codex",
        timeout_backoff=None,
        all_accounts_providers=frozenset({"clinepass"}),
    )

    by_provider = {call[call.index("--provider") + 1]: call for call in fake_codexbar.calls}
    assert "--all-accounts" in by_provider["clinepass"]
    assert "--all-accounts" not in by_provider["codex"]
    clinepass_rows = [a for a in accounts if a.provider == "clinepass"]
    assert [a.account for a in clinepass_rows] == ["technoeuphora", "dc"]
    assert clinepass_rows[1].windows[0].used_percent == 40.0


def test_providers_not_listed_never_get_the_flag(fake_codexbar: _FakeCodexBar) -> None:
    accounts = collect_codexbar(providers="clinepass,cursor", timeout_backoff=None)

    assert not any("--all-accounts" in call for call in fake_codexbar.calls)
    assert [a.account for a in accounts if a.provider == "clinepass"] == ["technoeuphora"]


def test_no_token_accounts_error_falls_back_to_plain_query(fake_codexbar: _FakeCodexBar) -> None:
    outcome = _query_provider("cursor", all_accounts=True)

    assert [("--all-accounts" in call) for call in fake_codexbar.calls] == [True, False]
    assert isinstance(outcome, list)
    assert outcome[0]["source"] == "web"
    assert "error" not in outcome[0]


def test_all_accounts_keeps_preferred_source(fake_codexbar: _FakeCodexBar) -> None:
    _query_provider("antigravity", all_accounts=True)

    first = fake_codexbar.calls[0]
    assert first[-3:] == ["--source", "oauth", "--all-accounts"]
    assert len(fake_codexbar.calls) == 1


def test_widget_cache_does_not_collapse_all_accounts_provider(fake_codexbar: _FakeCodexBar, tmp_path: Path) -> None:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "generatedAt": now_iso,
        "entries": [
            {"provider": "codex", "updatedAt": now_iso, "primary": {"usedPercent": 25.0, "windowMinutes": 300}},
            {
                "provider": "clinepass",
                "quotaOwnerKey": "technoeuphora",
                "updatedAt": now_iso,
                "primary": {"usedPercent": 10.0, "windowMinutes": 300},
            },
        ],
    }
    path = tmp_path / "widget-snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    accounts = collect_codexbar(
        providers="enabled",
        use_cache=True,
        cache_path=path,
        timeout_backoff=None,
        all_accounts_providers=frozenset({"clinepass"}),
    )

    # codex still comes from the snapshot; clinepass is queried live with every account.
    # The catalog read (`config providers`) has no --provider and must not add a usage query.
    assert [a.provider for a in accounts if a.provider == "codex"] == ["codex"]
    usage = [call for call in fake_codexbar.calls if "--provider" in call]
    assert [call[call.index("--provider") + 1] for call in usage] == ["clinepass"]
    assert any(call[1:3] == ["config", "providers"] for call in fake_codexbar.calls)
    clinepass_rows = [a for a in accounts if a.provider == "clinepass"]
    assert sorted(a.account or "" for a in clinepass_rows) == ["dc", "technoeuphora"]
    assert not any("widget snapshot" in note for a in clinepass_rows for note in a.notes)


def test_all_accounts_config_accepts_list_or_csv() -> None:
    assert _provider_names(["ClinePass", " cursor "]) == frozenset({"clinepass", "cursor"})
    assert _provider_names("clinepass, cursor") == frozenset({"clinepass", "cursor"})
    assert _provider_names(None) == frozenset()


def test_runner_threads_all_accounts_and_clinepass_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiuse.config import KNOWN_COLLECTOR_KEYS

    seen: dict[str, Any] = {}
    monkeypatch.setattr("aiuse.collectors.runner.collect_codexbar", lambda **kw: seen.update(codexbar=kw) or [])
    monkeypatch.setattr("aiuse.collectors.runner.collect_clinepass", lambda **kw: seen.update(clinepass=kw) or [])
    accounts = [{"label": "technoeuphora", "env": "X"}, {"label": "dc", "secret": "Y"}]
    run_collectors(
        {
            "collectors": {
                # Only the two collectors under test run (both stubbed).
                **{name: {"enabled": False} for name in KNOWN_COLLECTOR_KEYS},
                "codexbar": {"enabled": True, "all_accounts": "clinepass"},
                "clinepass": {"enabled": True, "accounts": accounts},
            }
        }
    )
    assert seen["codexbar"]["all_accounts_providers"] == frozenset({"clinepass"})
    assert seen["clinepass"]["accounts"] == accounts


# --- native ClinePass -------------------------------------------------------


class _Response:
    def __init__(self, used: float = 20.0, status: int = 200) -> None:
        self.used = used
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self) -> dict[str, Any]:
        return {"success": True, "data": {"limits": [{"type": "weekly", "percentUsed": self.used}]}}


class _Run:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


_ACCOUNTS = [
    {"label": "technoeuphora", "env": "CLINEPASS_API_KEY_TECHNOEUPHORA", "secret": "CLINE_API_KEY"},
    {"label": "dc", "env": "CLINEPASS_DC_API_KEY", "secret": "CLINE_API_KEY_DC"},
]


def _all_text(rows: list[AccountUsage]) -> str:
    return " ".join(f"{row.error} {row.notes} {row!r}" for row in rows)


def test_multi_account_resolves_env_then_secretspec_then_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: Any) -> _Run:
        runs.append(list(argv))
        tool = Path(argv[0]).name
        if tool == "secretspec":
            return _Run("", 1)  # not in the aiuse manifest: falls through to sudo
        return _Run("sk-sudo-dc")

    used_keys: list[str] = []

    def fake_get(_url: str, *, headers: dict[str, str], timeout: float) -> _Response:
        used_keys.append(headers["Authorization"].removeprefix("Bearer "))
        return _Response()

    monkeypatch.setattr(clinepass.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(clinepass.subprocess, "run", fake_run)
    monkeypatch.setattr(clinepass.requests, "get", fake_get)

    rows = clinepass.collect_clinepass(
        environ={"CLINEPASS_API_KEY_TECHNOEUPHORA": "sk-env-te"},
        accounts=_ACCOUNTS,
    )

    assert [row.account for row in rows] == ["technoeuphora", "dc"]
    assert all(row.error is None and row.windows for row in rows)
    # technoeuphora: env only, no subprocess. dc: plain secretspec, then sudo-secretspec.
    assert used_keys == ["sk-env-te", "sk-sudo-dc"]
    assert [Path(argv[0]).name for argv in runs] == ["secretspec", "sudo-secretspec"]
    assert runs[0][1:3] == ["get", "--file"] and runs[0][-1] == "CLINE_API_KEY_DC"
    assert runs[1][1:3] == ["get", "CLINE_API_KEY_DC"]
    assert not any(value in _all_text(rows) for value in _SECRET_VALUES)


def test_plain_secretspec_wins_before_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[str] = []

    def fake_run(argv: list[str], **_kw: Any) -> _Run:
        runs.append(Path(argv[0]).name)
        return _Run("sk-plain-dc")

    monkeypatch.setattr(clinepass.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(clinepass.subprocess, "run", fake_run)
    monkeypatch.setattr(clinepass.requests, "get", lambda *_a, **_k: _Response())

    rows = clinepass.collect_clinepass(environ={}, accounts=[_ACCOUNTS[1]])

    assert runs == ["secretspec"]
    assert rows[0].account == "dc" and rows[0].windows


def test_one_failing_account_is_an_error_row_not_an_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(_url: str, *, headers: dict[str, str], timeout: float) -> _Response:
        bad = headers["Authorization"].endswith("sk-sudo-dc")
        return _Response(status=401 if bad else 200)

    monkeypatch.setattr(clinepass.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        clinepass.subprocess,
        "run",
        lambda argv, **_k: _Run("", 1) if Path(argv[0]).name == "secretspec" else _Run("sk-sudo-dc"),
    )
    monkeypatch.setattr(clinepass.requests, "get", fake_get)

    rows = clinepass.collect_clinepass(environ={"CLINEPASS_API_KEY_TECHNOEUPHORA": "sk-env-te"}, accounts=_ACCOUNTS)

    good, bad = rows
    assert good.account == "technoeuphora" and good.windows and good.error is None
    assert bad.account == "dc" and not bad.windows
    assert bad.error == "ClinePass API returned HTTP 401"
    assert not any(value in _all_text(rows) for value in _SECRET_VALUES)


def test_malformed_limits_for_one_account_do_not_abort_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(_url: str, *, headers: dict[str, str], timeout: float) -> _Response:
        return _Response(used="N/A" if headers["Authorization"].endswith("sk-plain-dc") else 20.0)  # type: ignore[arg-type]

    monkeypatch.setattr(clinepass.shutil, "which", lambda _name: None)
    monkeypatch.setattr(clinepass.requests, "get", fake_get)

    rows = clinepass.collect_clinepass(
        environ={"CLINEPASS_API_KEY_TECHNOEUPHORA": "sk-env-te", "CLINEPASS_DC_API_KEY": "sk-plain-dc"},
        accounts=_ACCOUNTS,
    )

    good, bad = rows
    assert good.account == "technoeuphora" and good.windows and good.error is None
    assert bad.account == "dc" and not bad.windows
    assert bad.error is not None and "unparsable" in bad.error
    assert not any(value in _all_text(rows) for value in _SECRET_VALUES)


def test_missing_key_for_one_account_keeps_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clinepass.shutil, "which", lambda _name: None)
    monkeypatch.setattr(clinepass.requests, "get", lambda *_a, **_k: _Response())

    rows = clinepass.collect_clinepass(
        environ={"CLINEPASS_API_KEY_TECHNOEUPHORA": "sk-env-te"},
        accounts=[*_ACCOUNTS, {"label": "bare"}, {"env": "NO_LABEL"}],
    )

    assert [row.account for row in rows] == ["technoeuphora", "dc", "bare"]
    assert rows[0].windows
    assert rows[1].error is not None
    assert "CLINEPASS_DC_API_KEY unset" in rows[1].error
    assert "secretspec not on PATH" in rows[1].error
    assert rows[2].error == "ClinePass account bare has no env or secret configured"
    assert not any(value in _all_text(rows) for value in _SECRET_VALUES)


def test_legacy_no_accounts_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_which(name: str) -> None:
        calls.append(name)

    monkeypatch.setattr(clinepass.shutil, "which", fake_which)

    rows = clinepass.collect_clinepass(environ={}, accounts=[])

    assert calls == ["sudo-secretspec"]  # no plain secretspec on the legacy path
    assert len(rows) == 1 and rows[0].account is None
    assert rows[0].error == (
        "AIUSE_CLINE_API_KEY unset and sudo-secretspec not on PATH, so CLINE_API_KEY could not be read"
    )

    monkeypatch.setattr(clinepass.requests, "get", lambda *_a, **_k: _Response(status=500))
    with pytest.raises(clinepass.CollectorError, match="HTTP 500"):
        clinepass.collect_clinepass(environ={"AIUSE_CLINE_API_KEY": "sk-legacy"})


# --- runner selection and cross-checks ---------------------------------------


def _row(source: str, account: str | None, used: float = 10.0) -> AccountUsage:
    return AccountUsage(
        source=source,
        provider="clinepass",
        account=account,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="ClinePass weekly", used_percent=used, window_minutes=10080)],
    )


def _disagreements(checks: list[Any]) -> list[Any]:
    return [check for check in checks if check.status == "warning"]


def test_both_sources_report_both_accounts() -> None:
    rows = [
        _row("clinepass", "technoeuphora", 10),
        _row("clinepass", "dc", 50),
        _row("codexbar", "technoeuphora", 10),
        _row("codexbar", "DC", 50),
    ]

    selected, checks = _select_and_cross_check(rows, cswap_authoritative=True)

    assert sorted((a.account or "").casefold() for a in selected) == ["dc", "technoeuphora"]
    assert all(a.source == "codexbar" for a in selected)  # CodexBar outranks native
    assert not _disagreements(checks)
    consistent = [check for check in checks if check.status == "consistent"]
    assert sorted((check.account or "").casefold() for check in consistent) == ["dc", "technoeuphora"]


def test_account_only_native_reports_is_kept_without_false_disagreement() -> None:
    rows = [
        _row("clinepass", "technoeuphora", 10),
        _row("clinepass", "dc", 80),
        _row("codexbar", "technoeuphora", 10),
    ]

    selected, checks = _select_and_cross_check(rows, cswap_authoritative=True)

    assert {(a.account, a.source) for a in selected} == {("technoeuphora", "codexbar"), ("dc", "clinepass")}
    assert not _disagreements(checks)
    dc_checks = [check for check in checks if check.account == "dc"]
    assert dc_checks and all(check.status == "unavailable" for check in dc_checks)


def test_partial_overlap_marks_each_missing_account_unavailable() -> None:
    # Native {technoeuphora, dc} vs CodexBar {technoeuphora, other}: dc and
    # other are accounts the peer lacks, not an alias mapping problem.
    rows = [
        _row("clinepass", "technoeuphora", 10),
        _row("clinepass", "dc", 80),
        _row("codexbar", "technoeuphora", 10),
        _row("codexbar", "other", 30),
    ]

    selected, checks = _select_and_cross_check(rows, cswap_authoritative=True)

    assert {(a.account, a.source) for a in selected} == {
        ("technoeuphora", "codexbar"),
        ("dc", "clinepass"),
        ("other", "codexbar"),
    }
    assert not _disagreements(checks)
    assert not any("Cannot safely map" in check.message for check in checks)
    for account in ("dc", "other"):
        mine = [check for check in checks if check.account == account]
        assert mine and all(check.status == "unavailable" for check in mine)


def test_errored_native_account_is_not_renamed_onto_the_other() -> None:
    # Native dc live, native technoeuphora errored, CodexBar technoeuphora only:
    # automatic one-to-one naming must not relabel dc as technoeuphora.
    errored = _row("clinepass", "technoeuphora")
    errored.windows = []
    errored.error = "ClinePass API returned HTTP 401"
    rows = [errored, _row("clinepass", "dc", 80), _row("codexbar", "technoeuphora", 10)]

    selected, checks = _select_and_cross_check(rows, cswap_authoritative=True)

    assert {(a.account, a.source) for a in selected} == {("technoeuphora", "codexbar"), ("dc", "clinepass")}
    assert not any("percentage points" in check.message for check in checks)
    assert not any("identifiers differ" in check.message for check in checks)


def test_match_peer_account_never_pairs_different_labels() -> None:
    anchor = _row("clinepass", "dc")
    assert _match_peer_account(anchor, [_row("codexbar", "technoeuphora")], left_live_count=1) is None
    peer = _row("codexbar", "DC")
    assert _match_peer_account(anchor, [peer], left_live_count=1) is peer


def test_single_anonymous_account_still_pairs() -> None:
    anonymous = _row("clinepass", None, 10)
    labelled = _row("codexbar", "technoeuphora", 60)
    assert _match_peer_account(anonymous, [labelled], left_live_count=1) is labelled

    selected, checks = _select_and_cross_check(
        [_row("clinepass", None, 10), _row("codexbar", None, 60)], cswap_authoritative=True
    )
    assert [a.source for a in selected] == ["codexbar"]
    assert any(check.status == "warning" and "percentage points" in check.message for check in checks)


# --- config -----------------------------------------------------------------


def test_generated_config_parses_and_documents_multi_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from aiuse.config import generate_user_config, validate_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    text = Path(generate_user_config()["created"][0]).read_text(encoding="utf-8")
    tomllib.loads(text)
    assert '# all_accounts = ["clinepass"]' in text
    assert "# [[collectors.clinepass.accounts]]" in text

    example = (Path(__file__).resolve().parents[1] / "config" / "config.example.toml").read_text(encoding="utf-8")
    assert "# all_accounts = [" in example
    assert "# [[collectors.clinepass.accounts]]" in example

    good = {
        "collectors": {
            "codexbar": {"all_accounts": ["clinepass"]},
            "clinepass": {"accounts": [{"label": "dc", "secret": "CLINE_API_KEY_DC"}]},
        }
    }
    assert validate_config(good) == []
    bad = {"collectors": {"codexbar": {"all_accounts": 3}, "clinepass": {"accounts": [{"env": "X"}]}}}
    issues = "\n".join(validate_config(bad))
    assert "collectors.codexbar.all_accounts" in issues
    assert "collectors.clinepass.accounts[0] needs a non-empty label" in issues


def test_multi_account_settings_change_the_policy_fingerprint() -> None:
    from aiuse.config import collection_policy_fingerprint

    base = {"collectors": {"codexbar": {"enabled": True}, "clinepass": {"enabled": True}}}
    every = {
        "collectors": {"codexbar": {"enabled": True, "all_accounts": ["clinepass"]}, "clinepass": {"enabled": True}}
    }
    labelled = {"collectors": {"codexbar": {"enabled": True}, "clinepass": {"enabled": True, "accounts": _ACCOUNTS}}}

    prints = {collection_policy_fingerprint(cfg) for cfg in (base, every, labelled)}
    assert len(prints) == 3
