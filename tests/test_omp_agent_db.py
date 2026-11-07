"""omp's Anthropic API-key pool read from a (fixture) agent.db."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aiuse.analysis.selfdescribe import (
    apply_shared_rate_gate_to_snapshot,
    available_pools,
    enrich_snapshot,
    pool_entries,
)
from aiuse.collectors.base import CollectorNotInstalled
from aiuse.collectors.omp_agent_db import collect_omp_agent_db
from aiuse.collectors.runner import _select_and_cross_check
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _db(
    tmp_path: Path, creds: list[tuple[int, str, str, str | None]], blocks: list[tuple[int, int]] | None = None
) -> Path:
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, credential_type TEXT,"
            " data TEXT, disabled_cause TEXT)"
        )
        conn.execute(
            "CREATE TABLE auth_credential_blocks (credential_id INTEGER, provider_key TEXT, block_scope TEXT,"
            " blocked_until_ms INTEGER, updated_at INTEGER)"
        )
        for cid, provider, kind, cause in creds:
            conn.execute(
                "INSERT INTO auth_credentials VALUES (?, ?, ?, 'REDACTED-FIXTURE', ?)", (cid, provider, kind, cause)
            )
        for cid, until_ms in blocks or []:
            conn.execute(
                "INSERT INTO auth_credential_blocks VALUES (?, 'anthropic:api_key', '', ?, 0)", (cid, until_ms)
            )
    return path


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _pool(row: AccountUsage) -> dict:
    snap = Snapshot(collected_at=NOW, accounts=[row]).to_dict()
    enriched = enrich_snapshot(snap, now=NOW)
    (pool,) = pool_entries(enriched, now=NOW)
    return pool


def _collect(path: Path, binary: str | None = "/usr/bin/omp") -> AccountUsage:
    (row,) = collect_omp_agent_db(
        db_path=path, models_yml_path=path.parent / "missing-models.yml", now=NOW, omp_binary=binary
    )
    return row


def _models(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "models.yml"
    path.write_text(text)
    return path


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(text)
    return path


def _collect_all(
    tmp_path: Path,
    *,
    db_path: Path | None = None,
    models_yml_path: Path | None = None,
    binary: str | None = "/usr/bin/omp",
    config_yml_path: Path | None = None,
) -> list[AccountUsage]:
    return collect_omp_agent_db(
        db_path=db_path or tmp_path / "missing-agent.db",
        models_yml_path=models_yml_path or tmp_path / "missing-models.yml",
        config_yml_path=config_yml_path,
        now=NOW,
        omp_binary=binary,
    )


def _openai_row(rows: list[AccountUsage]) -> AccountUsage:
    return next(row for row in rows if row.source == "omp_models_yml")


def test_enabled_api_key_is_usable_with_unknown_headroom(tmp_path):
    row = _collect(_db(tmp_path, [(10, "anthropic", "oauth", "disabled by user"), (11, "anthropic", "api_key", None)]))
    assert row.provider == "omp"
    assert row.billing_kind is BillingKind.PAYG_API
    pool = _pool(row)
    assert pool["usable_now"] is True
    assert pool["headroom_percent"] is None
    assert pool["pool_family"] == "anthropic_api"
    assert pool["cli_binary"] == "omp"
    assert pool["available_at"] is None
    snap = enrich_snapshot(Snapshot(collected_at=NOW, accounts=[row]).to_dict(), now=NOW)
    assert [p["provider"] for p in available_pools(snap)] == ["omp"]


def test_only_oauth_is_not_usable(tmp_path):
    row = _collect(_db(tmp_path, [(10, "anthropic", "oauth", None)]))
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert "api_key" in pool["api_key_pool"]["reason"]
    snap = enrich_snapshot(Snapshot(collected_at=NOW, accounts=[row]).to_dict(), now=NOW)
    assert available_pools(snap) == []


def test_disabled_and_foreign_credentials_do_not_count(tmp_path):
    row = _collect(_db(tmp_path, [(1, "anthropic", "api_key", "disabled by user"), (2, "openai", "api_key", None)]))
    assert _pool(row)["usable_now"] is False


def test_active_block_reports_available_at(tmp_path):
    until = NOW + timedelta(minutes=17)
    path = _db(tmp_path, [(11, "anthropic", "api_key", None)], [(11, _ms(until))])
    pool = _pool(_collect(path))
    assert pool["usable_now"] is False
    assert datetime.fromisoformat(pool["available_at"]) == until


def test_expired_block_is_ignored(tmp_path):
    path = _db(tmp_path, [(11, "anthropic", "api_key", None)], [(11, _ms(NOW - timedelta(hours=1)))])
    assert _pool(_collect(path))["usable_now"] is True


def test_one_unblocked_key_keeps_the_pool_usable(tmp_path):
    path = _db(
        tmp_path,
        [(11, "anthropic", "api_key", None), (12, "anthropic", "api_key", None)],
        [(11, _ms(NOW + timedelta(hours=1)))],
    )
    assert _pool(_collect(path))["usable_now"] is True


def test_missing_binary_is_not_usable(tmp_path):
    row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]), binary="")
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert "binary" in pool["api_key_pool"]["reason"]


def test_missing_database_is_an_uninstalled_tool(tmp_path):
    with pytest.raises(CollectorNotInstalled):
        collect_omp_agent_db(db_path=tmp_path / "absent.db", now=NOW)


def test_unreadable_database_gives_an_unusable_row(tmp_path):
    bad = tmp_path / "agent.db"
    bad.write_text("not sqlite")
    pool = _pool(_collect(bad))
    assert pool["usable_now"] is False
    assert "unreadable" in pool["api_key_pool"]["reason"]


def test_openai_models_yml_route_is_usable_without_agent_db(tmp_path):
    models = _models(
        tmp_path,
        'providers:\n  openai:\n    apiKey: "!sudo-secretspec get OPENAI_API_KEY --reason test"\n',
    )
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert row.source == "omp_models_yml"
    assert row.account == "openai api key"
    assert row.raw == {"models_yml": str(models), "openai_api_key_configured": True}
    pool = _pool(row)
    assert pool["pool_family"] == "openai_api"
    assert pool["models_hint"] == "omp TUI - OpenAI API key (platform credits): openai/gpt-* models"
    assert pool["usable_now"] is True
    assert pool["headroom_percent"] is None
    assert pool["api_key_pool"]["provider"] == "openai"


def test_openai_route_disabled_by_omp_config_is_not_available(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders:\n  - openai\n")
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert pool["api_key_pool"]["reason"] == "openai disabled in omp disabledProviders"
    assert available_pools(enrich_snapshot(Snapshot(collected_at=NOW, accounts=[row]).to_dict(), now=NOW)) == []


def test_openai_route_kept_when_disabled_providers_omits_openai(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders:\n  - anthropic\n  - google\n")
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
    assert _pool(row)["usable_now"] is True


def test_openai_route_kept_when_disabled_providers_has_wrong_type(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders: openai\n")
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
    assert _pool(row)["usable_now"] is True


def test_openai_route_fails_closed_on_malformed_omp_config(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders: [openai\n")
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert "malformed" in pool["api_key_pool"]["reason"]


def test_openai_route_fails_closed_on_oversized_omp_config(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders: []\n" + ("# pad\n" * 50000))
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert "256 KiB" in pool["api_key_pool"]["reason"]


def test_openai_route_uses_config_path_override(tmp_path, monkeypatch):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders:\n  - openai\n")
    monkeypatch.setenv("AIUSE_OMP_CONFIG_YML", str(config))
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is False


def test_openai_route_honors_path_scoped_disabled_provider(tmp_path, monkeypatch):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    project = tmp_path / "repo"
    project.mkdir()
    config = _config(
        tmp_path,
        f'disabledProviders:\n  - path: "{project}"\n    providers:\n      - openai\n',
    )
    monkeypatch.chdir(project)
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
    assert _pool(row)["usable_now"] is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]\n",
        "providers: []\n",
        "providers:\n  openai: []\n",
        "providers:\n  openai:\n    apiKey: ''\n",
        "providers:\n  openai:\n    apiKey: '   '\n",
        "providers:\n  openai:\n    apiKey: null\n",
        "providers:\n  openai:\n    apiKey: [not, a, string]\n",
        "providers:\n  openai:\n    apiKey: 123\n",
        "providers:\n  openai:\n    apiKey: true\n",
        "providers: [not, a, mapping\n",
    ],
)
def test_missing_empty_or_malformed_models_yml_is_no_openai_route(tmp_path, text):
    models = _models(tmp_path, text)
    with pytest.raises(CollectorNotInstalled):
        _collect_all(tmp_path, models_yml_path=models)


def test_oversized_models_yml_is_no_openai_route(tmp_path):
    models = _models(tmp_path, "providers:\n  openai:\n    apiKey: configured\n" + ("# pad\n" * 50000))
    with pytest.raises(CollectorNotInstalled):
        _collect_all(tmp_path, models_yml_path=models)


def test_symlinked_models_yml_is_supported(tmp_path):
    target = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    link = tmp_path / "linked-models.yml"
    link.symlink_to(target)
    (row,) = _collect_all(tmp_path, models_yml_path=link)
    assert _pool(row)["usable_now"] is True


def test_unreadable_models_yml_is_no_openai_route(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    models.chmod(0)
    try:
        try:
            models.read_bytes()
        except OSError:
            pass
        else:
            pytest.skip("chmod did not make models.yml unreadable on this platform")
        with pytest.raises(CollectorNotInstalled):
            _collect_all(tmp_path, models_yml_path=models)
    finally:
        models.chmod(0o600)


def test_unreadable_omp_config_fails_closed(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    config = _config(tmp_path, "disabledProviders: []\n")
    config.chmod(0)
    try:
        try:
            config.read_bytes()
        except OSError:
            pass
        else:
            pytest.skip("chmod did not make config.yml unreadable on this platform")
        (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=config)
        pool = _pool(row)
        assert pool["usable_now"] is False
        assert "unreadable" in pool["api_key_pool"]["reason"]
    finally:
        config.chmod(0o600)


@pytest.mark.parametrize("env_value", [None, "", "sk-test-fixture"])
def test_openai_api_key_env_var_reference_counts_as_configured_without_resolving(tmp_path, monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", env_value)
    models = _models(tmp_path, "providers:\n  openai:\n    apiKey: OPENAI_API_KEY\n")
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is True
    assert "OPENAI_API_KEY" not in json.dumps(row.to_dict(), default=str)
    assert "sk-test-fixture" not in json.dumps(row.to_dict(), default=str)


def test_openai_base_url_override_stays_openai_api_route(tmp_path):
    models = _models(
        tmp_path,
        'providers:\n  openai:\n    baseUrl: "https://gateway.example/v1"\n    apiKey: "configured"\n',
    )
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["pool_family"] == "openai_api"


def test_project_config_replaces_global_disabled_providers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    agent = home / ".omp" / "agent"
    agent.mkdir(parents=True)
    (agent / "config.yml").write_text("disabledProviders:\n  - openai\n")
    project = tmp_path / "repo"
    project_config_dir = project / ".omp"
    project_config_dir.mkdir(parents=True)
    (project_config_dir / "config.yml").write_text("disabledProviders:\n  - groq\n")
    monkeypatch.delenv("AIUSE_OMP_CONFIG_YML", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is True


@pytest.mark.parametrize("filename", ["config.yml", "config.yaml", "settings.json"])
def test_openai_global_config_locations(tmp_path, monkeypatch, filename):
    agent = tmp_path / "home" / ".omp" / "agent"
    agent.mkdir(parents=True)
    (agent / filename).write_text('{"disabledProviders": ["openai"]}')
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AIUSE_OMP_CONFIG_YML")
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    monkeypatch.delenv("OMP_PROFILE", raising=False)
    monkeypatch.delenv("PI_PROFILE", raising=False)
    models = _models(tmp_path, "providers: {openai: {apiKey: configured}}")
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is False


@pytest.mark.parametrize("filename", ["settings.json", "config.yml"])
def test_openai_project_config_locations(tmp_path, monkeypatch, filename):
    project = tmp_path / ".omp"
    project.mkdir()
    (project / filename).write_text('{"disabledProviders": ["openai"]}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIUSE_OMP_CONFIG_YML")
    monkeypatch.setenv("HOME", str(tmp_path))
    models = _models(tmp_path, "providers: {openai: {apiKey: configured}}")
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is False


@pytest.mark.parametrize("value", ["disabledProviders: [openai]", "disabledProviders: []", "missing"])
def test_openai_config_overlays_override_project(tmp_path, monkeypatch, value):
    project = tmp_path / ".omp"
    project.mkdir()
    (project / "config.yml").write_text("disabledProviders: [openai]")
    overlay = tmp_path / "overlay.yml"
    if value != "missing":
        overlay.write_text(value)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIUSE_OMP_CONFIG_YML")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PI_CONFIG_FILES", str(overlay))
    models = _models(tmp_path, "providers: {openai: {apiKey: configured}}")
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is (value == "disabledProviders: []")


@pytest.mark.parametrize("profile_env", ["OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR"])
def test_openai_config_relocated_agent_dir(tmp_path, monkeypatch, profile_env):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AIUSE_OMP_CONFIG_YML")
    monkeypatch.delenv("OMP_PROFILE", raising=False)
    monkeypatch.delenv("PI_PROFILE", raising=False)
    agent = tmp_path / ".omp" / "profiles" / "work" / "agent"
    agent.mkdir(parents=True)
    (agent / "config.yml").write_text("disabledProviders: [openai]")
    monkeypatch.setenv(profile_env, str(agent) if profile_env == "PI_CODING_AGENT_DIR" else "work")
    models = _models(tmp_path, "providers: {openai: {apiKey: configured}}")
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert _pool(row)["usable_now"] is False


def test_openai_missing_config_is_usable(tmp_path):
    models = _models(tmp_path, "providers: {openai: {apiKey: configured}}")
    (row,) = _collect_all(tmp_path, models_yml_path=models, config_yml_path=tmp_path / "absent.yml")
    assert _pool(row)["usable_now"] is True


def test_openai_alias_bomb_is_bounded_before_parsing(tmp_path, monkeypatch):
    import aiuse.collectors.omp_agent_db as collector

    # A large alias document must be rejected before invoking the YAML parser.
    models = _models(tmp_path, "a: &a [one]\nb: [" + ",".join(["*a"] * 100000) + "]")

    def reject_parse(_text):
        pytest.fail("oversized YAML reached parser")

    monkeypatch.setattr(collector.yaml, "safe_load", reject_parse)
    with pytest.raises(CollectorNotInstalled):
        _collect_all(tmp_path, models_yml_path=models)


def test_openai_read_is_capped(tmp_path, monkeypatch):
    import io

    from aiuse.collectors.omp_agent_db import _read_mapping

    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            assert size == 256 * 1024 + 1
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: BoundedStream(b"x" * (256 * 1024 + 2)))
    mapping, error = _read_mapping(tmp_path / "models.yml", label="omp models.yml")
    assert mapping is None
    assert "256 KiB" in error


def test_openai_route_requires_omp_binary(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    with pytest.raises(CollectorNotInstalled):
        _collect_all(tmp_path, models_yml_path=models, binary="")


def test_openai_api_key_command_is_never_executed(tmp_path, capsys):
    sentinel = tmp_path / "executed"
    models = _models(
        tmp_path,
        f'providers:\n  openai:\n    apiKey: "!/bin/sh -c \\"touch {sentinel}\\""\n',
    )
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    assert not sentinel.exists()
    enriched = enrich_snapshot(Snapshot(collected_at=NOW, accounts=[row]).to_dict(), now=NOW)
    captured = capsys.readouterr()
    output = json.dumps(enriched, default=str) + captured.out + captured.err
    assert "!/bin/sh" not in output
    assert str(sentinel) not in output
    assert models.read_text() not in output


def test_openai_models_yml_text_never_reaches_json(tmp_path):
    secret_marker = "TEST_OPENAI_ROUTE_SECRET_MARKER"
    models = _models(tmp_path, f'providers:\n  openai:\n    apiKey: "{secret_marker}"\n')
    (row,) = _collect_all(tmp_path, models_yml_path=models)
    snap = Snapshot(collected_at=NOW, accounts=[row]).to_dict()
    enriched = enrich_snapshot(snap, now=NOW)
    assert secret_marker not in json.dumps(row.to_dict(), default=str)
    assert secret_marker not in json.dumps(enriched, default=str)
    assert "providers:" not in json.dumps(enriched, default=str)


def test_anthropic_and_openai_routes_are_separate_pools(tmp_path):
    db = _db(tmp_path, [(11, "anthropic", "api_key", None)])
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    rows = _collect_all(tmp_path, db_path=db, models_yml_path=models)
    snap = enrich_snapshot(Snapshot(collected_at=NOW, accounts=rows).to_dict(), now=NOW)
    assert [account["account"] for account in snap["accounts"]] == ["anthropic api key", "openai api key"]
    pools = {(p["source"], p["pool_family"]): p for p in pool_entries(snap, now=NOW)}
    assert ("omp_agent_db", "anthropic_api") in pools
    assert ("omp_models_yml", "openai_api") in pools
    assert [p["pool_family"] for p in available_pools(snap)] == ["anthropic_api", "openai_api"]


def test_codexbar_grant_precedence_keeps_openai_route_separate(tmp_path):
    db = _db(tmp_path, [(11, "anthropic", "api_key", None)])
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    key_row, openai = _collect_all(tmp_path, db_path=db, models_yml_path=models)
    grant = AccountUsage(
        source="codexbar",
        provider="omp",
        account="Org",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="credits", used_percent=10.0, remaining_percent=90.0)],
        collected_at=NOW,
    )
    selected, checks = _select_and_cross_check([key_row, openai, grant], cswap_authoritative=False)
    assert [a.source for a in selected] == ["codexbar", "omp_models_yml"]
    assert [c for c in checks if c.provider == "omp" and c.status == "warning"] == []


def test_pi_openai_spend_meter_does_not_cross_check_against_omp_openai_route(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    (openai,) = _collect_all(tmp_path, models_yml_path=models)
    pi = AccountUsage(
        source="codexbar",
        provider="pi",
        account="OpenAI org",
        billing_kind=BillingKind.PAYG_API,
        spent_usd=12.0,
        windows=[QuotaWindow(label="monthly-spend", used_percent=12.0, remaining_percent=88.0)],
        collected_at=NOW,
    )
    selected, checks = _select_and_cross_check([openai, pi], cswap_authoritative=False)
    assert [(a.provider, a.source) for a in selected] == [("omp", "omp_models_yml"), ("pi", "codexbar")]
    assert [c for c in checks if c.provider == "omp"] == []
    assert [c.provider for c in checks] == ["pi"]


def test_operator_override_replaces_anthropic_route_but_not_openai_route(tmp_path):
    db = _db(tmp_path, [(11, "anthropic", "api_key", None)])
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    rows = _collect_all(tmp_path, db_path=db, models_yml_path=models)
    override = {
        "omp": {
            "remaining_usd": 0.0,
            "total_usd": 200.0,
            "observed_at": (NOW - timedelta(hours=1)).isoformat(),
            "expires_at": (NOW + timedelta(days=10)).isoformat(),
        }
    }
    enriched = enrich_snapshot(Snapshot(collected_at=NOW, accounts=rows).to_dict(), now=NOW, credit_overrides=override)
    sources = [account["source"] for account in enriched["accounts"]]
    assert sources == ["operator-override", "omp_models_yml"]
    pools = {(pool["source"], pool["pool_family"]): pool for pool in pool_entries(enriched, now=NOW)}
    assert ("operator-override", "claude") in pools
    assert ("omp_models_yml", "openai_api") in pools


def test_claude_rate_gate_does_not_gate_openai_route(tmp_path):
    models = _models(tmp_path, 'providers:\n  openai:\n    apiKey: "configured"\n')
    (openai,) = _collect_all(tmp_path, models_yml_path=models)
    claude = AccountUsage(
        source="codexbar",
        provider="claude",
        account="Claude",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Claude Code weekly",
                used_percent=100.0,
                remaining_percent=0.0,
                resets_at=NOW + timedelta(days=3),
                window_minutes=10080,
            )
        ],
        collected_at=NOW,
    )
    enriched = enrich_snapshot(Snapshot(collected_at=NOW, accounts=[claude, openai]).to_dict(), now=NOW)
    (pool,) = [p for p in pool_entries(enriched, now=NOW) if p["provider"] == "omp"]
    assert pool["pool_family"] == "openai_api"
    assert pool["usable_now"] is True
    assert "rate_gated_by" not in pool

    model_snap = Snapshot(collected_at=NOW, accounts=[claude, openai])
    assert apply_shared_rate_gate_to_snapshot(model_snap, now=NOW) is False
    assert model_snap.accounts[1].rate_gated_by is None


def test_grant_row_from_codexbar_outranks_key_pool_row(tmp_path):
    key_row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]))
    grant = AccountUsage(
        source="codexbar",
        provider="omp",
        account="Org",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="credits", used_percent=10.0, remaining_percent=90.0)],
        collected_at=NOW,
    )
    selected, _ = _select_and_cross_check([key_row, grant], cswap_authoritative=False)
    assert [a.source for a in selected] == ["codexbar"]
    selected, _ = _select_and_cross_check([key_row], cswap_authoritative=False)
    assert [a.source for a in selected] == ["omp_agent_db"]


def test_stale_codexbar_grant_still_outranks_key_pool_row(tmp_path):
    now = utcnow()
    key_row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]))
    key_row.collected_at = now
    grant = AccountUsage(
        source="codexbar",
        provider="omp",
        account="Org",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        balance_usd=120.0,
        windows=[QuotaWindow(label="credits", used_percent=10.0, remaining_percent=90.0)],
        collected_at=now - timedelta(hours=1),
    )
    selected, checks = _select_and_cross_check([key_row, grant], cswap_authoritative=False)
    assert [a.source for a in selected] == ["codexbar"]
    assert [c for c in checks if c.provider == "omp" and c.status == "warning"] == []


def test_override_replacing_the_key_pool_row_decides_usability(tmp_path):
    row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]))
    override = {
        "omp": {
            "remaining_usd": 0.0,
            "total_usd": 200.0,
            "observed_at": (NOW - timedelta(hours=1)).isoformat(),
            "expires_at": (NOW + timedelta(days=10)).isoformat(),
        }
    }
    snap = Snapshot(collected_at=NOW, accounts=[row]).to_dict()
    enriched = enrich_snapshot(snap, now=NOW, credit_overrides=override)
    (account,) = enriched["accounts"]
    assert account["source"] == "operator-override"
    assert account["usable_now"] is False
    assert "api_key_pool" not in account
    assert available_pools(enriched) == []


def test_key_material_never_reaches_the_json(tmp_path):
    row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]))
    snap = Snapshot(collected_at=NOW, accounts=[row]).to_dict()
    enriched = enrich_snapshot(snap, now=NOW)
    assert "REDACTED-FIXTURE" not in json.dumps(snap)
    assert "REDACTED-FIXTURE" not in json.dumps(enriched, default=str)
