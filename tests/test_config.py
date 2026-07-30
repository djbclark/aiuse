from pathlib import Path

import pytest

from aiuse.config import (
    DEFAULT_CONFIG,
    DEFAULT_SUBPROCESS_TIMEOUT,
    collector_health_url,
    default_config_dir,
    default_config_path,
    default_toml_config_path,
    ensure_config_dir,
    generate_user_config,
    load_config,
    timeout_for,
    validate_config,
)


def test_collector_health_url_openusage_defaults_and_overrides():
    assert collector_health_url(DEFAULT_CONFIG, "openusage_ai") == "http://127.0.0.1:6736/v1/limits"
    assert (
        collector_health_url(
            {
                "collectors": {
                    "openusage_ai": {
                        "base_url": "http://127.0.0.1:9",
                        "health_path": "/healthz",
                    }
                }
            },
            "openusage_ai",
        )
        == "http://127.0.0.1:9/healthz"
    )
    assert (
        collector_health_url(
            {"collectors": {"openusage_ai": {"probe_url": "http://example/probe"}}},
            "openusage_ai",
        )
        == "http://example/probe"
    )
    assert collector_health_url(DEFAULT_CONFIG, "cswap") is None


def test_default_config_path_uses_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_config_path() == tmp_path / "aiuse" / "config.toml"
    assert default_toml_config_path() == tmp_path / "aiuse" / "config.toml"


def test_load_config_reads_canonical_toml(monkeypatch, tmp_path):
    config_path = tmp_path / "aiuse" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("[analysis]\nmin_remaining_percent = 55\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_config()

    assert config["analysis"]["min_remaining_percent"] == 55


def test_load_config_maps_legacy_openusage_name_to_openusage_ai(monkeypatch, tmp_path):
    config_path = tmp_path / "aiuse" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("[collectors.openusage]\nenabled = false\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_config()

    assert config["collectors"]["openusage_ai"]["enabled"] is False
    assert "openusage" not in config["collectors"]


def test_relative_xdg_config_home_is_ignored(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")

    path = default_config_path()
    assert path == Path.home() / ".config" / "aiuse" / "config.toml"


def test_load_config_reads_legacy_yaml_only(monkeypatch, tmp_path):
    legacy_path = tmp_path / "aiuse" / "services.yaml"
    legacy_path.parent.mkdir()
    legacy_path.write_text("analysis:\n  min_remaining_percent: 55\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert load_config()["analysis"]["min_remaining_percent"] == 55


def test_load_config_rejects_both_default_config_files(monkeypatch, tmp_path):
    config_dir = tmp_path / "aiuse"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("[timeouts]\ndefault = 30\n", encoding="utf-8")
    (config_dir / "services.yaml").write_text("analysis:\n  min_remaining_percent: 55\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="Both user config files exist"):
        load_config()


def test_explicit_config_remains_usable_when_both_default_files_exist(monkeypatch, tmp_path):
    config_dir = tmp_path / "aiuse"
    config_dir.mkdir()
    canonical = config_dir / "config.toml"
    canonical.write_text("[timeouts]\ndefault = 30\n", encoding="utf-8")
    (config_dir / "services.yaml").write_text("analysis:\n  min_remaining_percent: 55\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert timeout_for(load_config(canonical), "cswap") == 30


def test_load_config_explicit_missing_path_exits():
    with pytest.raises(SystemExit, match="Config file not found"):
        load_config("/nonexistent/ai-config-does-not-exist.yaml")


def test_default_analysis_persist_and_learn_flags():
    from aiuse.config import DEFAULT_CONFIG

    analysis = DEFAULT_CONFIG["analysis"]
    assert analysis["persist_snapshots"] is False
    assert analysis["learn_from_history"] == "auto"


def test_default_timeouts_are_45s():
    assert DEFAULT_SUBPROCESS_TIMEOUT == 45.0
    assert timeout_for({}, "tokscale") == 45.0
    assert timeout_for({"timeouts": {"default": 45}}, "cswap") == 45.0


def test_timeout_for_per_tool_and_force_precedence():
    cfg = {"timeouts": {"default": 45, "tokscale": 20, "force": 10}}
    assert timeout_for(cfg, "tokscale") == 10.0  # force wins
    cfg_no_force = {"timeouts": {"default": 45, "tokscale": 20}}
    assert timeout_for(cfg_no_force, "tokscale") == 20.0
    assert timeout_for(cfg_no_force, "cswap") == 45.0


def test_load_config_merges_toml_timeouts(monkeypatch, tmp_path):
    ai_dir = tmp_path / "aiuse"
    ai_dir.mkdir()
    (ai_dir / "config.toml").write_text(
        "[timeouts]\ndefault = 30\ntokscale = 12\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_config()

    assert timeout_for(config, "cswap") == 30.0
    assert timeout_for(config, "tokscale") == 12.0


def test_ensure_config_dir_creates_nested_levels(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert not (tmp_path / "xdg").exists()
    ai_dir = ensure_config_dir()
    assert ai_dir == tmp_path / "xdg" / "aiuse"
    assert ai_dir.is_dir()


def test_validate_config_clean_defaults():
    # Empty dict is fine (defaults not required for validation of known keys)
    assert validate_config({}) == []
    assert (
        validate_config(
            {
                "timeouts": {"default": 45},
                "collectors": {"cswap": {"enabled": True}},
                "analysis": {"scoring_mode": "pace"},
                "plans": {"claude": {"monthly_price": 20}},
            }
        )
        == []
    )


def test_validate_config_accepts_and_checks_account_aliases():
    assert validate_config({"account_aliases": {"codex": {"openusage_sh": {"codex-cli": "me@example.com"}}}}) == []

    issues = validate_config({"account_aliases": {"codex": {"openusage_sh": {"codex-cli": 3}}}})
    assert "account_aliases.codex.openusage_sh entries need non-empty account names" in "\n".join(issues)


def test_validate_config_unknown_and_bad_timeouts():
    issues = validate_config(
        {
            "timeouts": {"default": -5, "nope": 1},
            "extra": True,
            "collectors": {"cswap": {"enabled": True, "wat": 1}, "ghost": True},
            "plans": {"antigravity": {"monthly_price": 20}},
            "analysis": {"scoring_mode": "magic", "provider_overrides": {"chatgpt": {}}},
        }
    )
    text = "\n".join(issues)
    assert "error: timeouts.default must be positive" in text
    assert "unknown timeouts key 'nope'" in text
    assert "unknown top-level config key 'extra'" in text
    assert "unknown collector 'ghost'" in text
    assert "unknown collectors.cswap key 'wat'" in text
    assert "plans key 'antigravity' is dead" in text
    assert "provider_overrides key 'chatgpt' is dead" in text
    assert "scoring_mode 'magic'" in text


def test_generate_user_config_writes_defaults_without_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    first = generate_user_config()
    assert [Path(p).name for p in first["created"]] == ["config.toml"]
    assert first["skipped"] == []
    assert first["errors"] == []
    assert (tmp_path / "aiuse" / "config.toml").is_file()
    assert "default = 45" in (tmp_path / "aiuse" / "config.toml").read_text(encoding="utf-8")

    # Second run must not overwrite
    stamp = "KEEP-ME"
    toml_path = tmp_path / "aiuse" / "config.toml"
    toml_path.write_text(stamp, encoding="utf-8")
    second = generate_user_config()
    assert second["created"] == []
    assert [Path(p).name for p in second["skipped"]] == ["config.toml"]
    assert toml_path.read_text(encoding="utf-8") == stamp


def test_generate_user_config_refuses_to_create_toml_beside_legacy_yaml(monkeypatch, tmp_path):
    config_dir = tmp_path / "aiuse"
    config_dir.mkdir()
    (config_dir / "services.yaml").write_text("collectors:\n  caut:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = generate_user_config()

    assert result["created"] == []
    assert "legacy config exists" in result["errors"][0]


def test_default_config_dir_is_under_xdg_ai(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_dir() == tmp_path / "aiuse"
