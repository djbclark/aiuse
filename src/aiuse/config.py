"""Load YAML/JSON/TOML config with defaults.

User config layers (later wins on deep-merge where keys overlap):

1. Built-in ``DEFAULT_CONFIG``
2. Optional ``~/.config/aiuse/config.toml`` (or ``$XDG_CONFIG_HOME/aiuse/config.toml``)
   — preferred home for tool settings (timeouts, future knobs)
3. Optional services file (``services.yaml`` / JSON via ``--config`` or default
   path) — plans, analysis thresholds, collector enable flags
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Default wall-clock budget for every external CLI subprocess. Tools either
# return within tens of seconds or hang; long budgets only delay failure.
DEFAULT_SUBPROCESS_TIMEOUT = 45.0

DEFAULT_CONFIG: dict[str, Any] = {
    # Subprocess timeouts (seconds). ``default`` applies to any tool that does
    # not set its own key. Known keys: cswap, codexbar, codexbar_discovery,
    # tokscale. Override via config.toml or CLI ``--timeout`` / ``-t``.
    "timeouts": {
        "default": DEFAULT_SUBPROCESS_TIMEOUT,
    },
    "analysis": {
        "min_remaining_percent": 40,
        "max_days_until_reset": 14,
        "urgent_remaining_percent": 70,
        "urgent_days_until_reset": 7,
        "use_multi_dim_scoring": True,
        "scoring_mode": "pace",
        "pace": {
            "waste_alert_fraction": 0.30,
            "min_elapsed_fraction": 0.15,
            "conserve_min_lead_hours": 4.0,
        },
        # Persist JSON under ~/.cache/aiuse/snapshots without changing scoring.
        # learn_from_history true/auto-active implies persist (see cli.save gate).
        "persist_snapshots": False,
        # true | false | "auto" — auto learns once enough snapshots exist (>= 2).
        "learn_from_history": "auto",
        "snapshot_retention_days": 90,
        "waking_hours_per_day": 16,
        "min_value_at_risk_usd": 0.50,
        "min_value_fraction": 0.05,
        "max_sustained_tokens_per_minute": 200000,
        "max_requests_per_minute": 0.5,
        "max_usd_per_minute": 0.05,
        "consumption_flexibility_defaults": {
            "5h": 0.0,
            "weekly": 0.7,
            "monthly": 1.0,
        },
        "provider_overrides": {
            "claude": {
                "shared_allotment": True,  # 5h ⊂ weekly; pace-score governing window only
                "5h": {"flexibility": 0.0, "refill_capacity_unit": "requests", "refill_capacity": 45},
            },
            "gemini": {
                "shared_allotment": True,
                "5h": {"flexibility": 0.0, "refill_capacity_unit": "requests", "refill_capacity": 50},
            },
            # 5h ⊂ weekly ⊂ monthly — burning a short window draws the same Go budget.
            "opencode": {"shared_allotment": True},
            # Included ⊃ Auto/API category breakdowns — score Included only.
            "cursor": {"shared_allotment": True},
            "grok": {"weekly": {"flexibility": 0.5, "refill_capacity_unit": "requests", "refill_capacity": 100}},
        },
        # Advisory only: probe local LLM ports when cloud quotas look empty.
        "local_runtimes": {
            "enabled": False,
            "when": "empty",  # empty | always
            "probe_timeout_seconds": 0.35,
            "allow_non_loopback": False,
            "endpoints": [
                {"name": "Ollama", "host": "127.0.0.1", "port": 11434},
                {"name": "LM Studio", "host": "127.0.0.1", "port": 1234},
            ],
        },
    },
    "plans": {
        "codex": {
            "name": "ChatGPT / Codex Plus",
            "notes": "Weekly Codex limits reset; unused weekly quota is lost.",
            "monthly_price": 20,
        },
        "claude": {
            "name": "Claude Pro / Max",
            "notes": (
                "Anthropic Claude Code 5h/weekly; multi-account via cswap. "
                "Not the same as Google Antigravity's Claude/GPT pool."
            ),
            "monthly_price": 20,
            "value_multiplier": {"5h": 1.4},
        },
        "cursor": {
            "name": "Cursor",
            "notes": (
                "Monthly included usage (Auto/API are category breakdowns of the same pool); "
                "on-demand is a separate dollar cap."
            ),
            "monthly_price": 20,
        },
        "copilot": {
            "name": "GitHub Copilot",
            "notes": "Premium-request quota only (completions/chat omitted — not comparable burn windows).",
            "monthly_price": 10,
        },
        "grok": {
            "name": "SuperGrok",
            "notes": "Credits / rate windows reset on a short cycle.",
            "monthly_price": 30,
        },
        "gemini": {
            "name": "Google AI Pro / Ultra",
            "notes": (
                "Antigravity / Gemini CLI. Gemini and Claude/GPT are independent "
                "Google pools (list separately). Claude/GPT here is Google-sold, "
                "not Anthropic Claude Code (cswap)."
            ),
            "monthly_price": 20,
        },
        "opencode": {
            "name": "OpenCode Go",
            "notes": "Has 5h / weekly / monthly windows when subscribed.",
            "monthly_price": 10,
        },
        "deepseek": {
            "name": "DeepSeek (prepaid API)",
            "notes": "No monthly subscription — purchased tokens do not expire; never use-or-lose.",
        },
        "openrouter": {
            "name": "OpenRouter (prepaid)",
            "notes": "API credits usually roll until spent (not use-or-lose).",
        },
    },
    "collectors": {
        "cswap": {"enabled": True},
        "codexbar": {"enabled": True, "providers": "enabled"},
        # caut + OpenUsage enabled by default for multi-source cross-checks.
        # "both" = claude+codex (providers caut can actually fill windows for).
        "caut": {"enabled": True, "providers": "both"},
        "openusage": {
            "enabled": True,
            "force_refresh": True,
            "try_launch_app": True,
            "base_url": "http://127.0.0.1:6736",
            # Doctor / preflight probe (payload collect still uses /v1/limits via base_url).
            "health_path": "/v1/limits",
        },
        "tokscale": {"enabled": True},
    },
}


# Top-level and nested keys recognized by the loader / doctor (unknown → warning).
KNOWN_TOP_LEVEL_KEYS = frozenset({"timeouts", "analysis", "plans", "collectors"})
KNOWN_TIMEOUT_KEYS = frozenset(
    {
        "default",
        "force",
        "cswap",
        "codexbar",
        "codexbar_discovery",
        "caut",
        "openusage",
        "tokscale",
    }
)
KNOWN_COLLECTOR_KEYS = frozenset({"cswap", "codexbar", "caut", "openusage", "tokscale"})
KNOWN_COLLECTOR_ENTRY_KEYS = frozenset(
    {
        "enabled",
        "providers",
        "force_refresh",
        "try_launch_app",
        "base_url",
        "health_path",
        "probe_url",
    }
)


def collector_health_url(config: dict[str, Any] | None, name: str) -> str | None:
    """Optional HTTP health/probe URL for a collector (doctor / preflight).

    Precedence:

    1. ``collectors.<name>.probe_url`` — full URL
    2. ``base_url`` + ``health_path`` (path may be absolute path on the host)
    3. For ``openusage`` only: ``base_url`` + ``/v1/limits`` when path omitted
    """
    collectors = (config or {}).get("collectors")
    if not isinstance(collectors, dict):
        return None
    entry = collectors.get(name)
    if not isinstance(entry, dict):
        return None
    probe = entry.get("probe_url")
    if probe:
        return str(probe).strip() or None
    base = entry.get("base_url")
    if not base:
        return None
    base_s = str(base).rstrip("/")
    path = entry.get("health_path")
    if path is None and name == "openusage":
        path = "/v1/limits"
    if path is None:
        return None
    path_s = str(path).strip()
    if path_s.startswith("http://") or path_s.startswith("https://"):
        return path_s
    if not path_s.startswith("/"):
        path_s = "/" + path_s
    return base_s + path_s
KNOWN_ANALYSIS_KEYS = frozenset(DEFAULT_CONFIG["analysis"].keys())
KNOWN_PACE_KEYS = frozenset(DEFAULT_CONFIG["analysis"]["pace"].keys())
KNOWN_SCORING_MODES = frozenset({"pace", "multi_dim", "legacy"})

# Collector provider ids that resolve via provider_config_key / aliases.
# Using these as *plan* or *provider_overrides* keys is a no-op (dead config).
_DEAD_PLAN_KEYS: dict[str, str] = {
    "antigravity": "gemini",
    "opencode-go": "opencode",
    "opencodego": "opencode",
    "chatgpt": "codex",
    "openai-codex": "codex",
    "github-copilot": "copilot",
    "supergrok": "grok",
    "grok-build": "grok",
}


def timeout_for(config: dict[str, Any] | None, name: str) -> float:
    """Resolve subprocess timeout (seconds) for a named tool.

    Precedence:

    1. ``timeouts.force`` — set by CLI ``--timeout`` / ``-t`` (wins over everything)
    2. ``timeouts.<name>`` — per-tool override in config.toml / services.yaml
    3. ``timeouts.default``
    4. :data:`DEFAULT_SUBPROCESS_TIMEOUT`
    """
    timeouts = (config or {}).get("timeouts") if isinstance((config or {}).get("timeouts"), dict) else {}
    if timeouts.get("force") is not None:
        return float(timeouts["force"])
    default = float(timeouts.get("default", DEFAULT_SUBPROCESS_TIMEOUT))
    if name in timeouts and timeouts[name] is not None:
        return float(timeouts[name])
    return default


def validate_config(config: dict[str, Any] | None) -> list[str]:
    """Return human-readable config problems/warnings (empty = clean).

    Does not raise. Used by ``ai doctor``; safe to call after ``load_config``.
    Severity is encoded in the message prefix: ``error:`` vs ``warning:``.
    """
    cfg = config or {}
    issues: list[str] = []

    for key in cfg:
        if key not in KNOWN_TOP_LEVEL_KEYS:
            issues.append(f"warning: unknown top-level config key {key!r} (ignored)")

    timeouts = cfg.get("timeouts")
    if timeouts is not None and not isinstance(timeouts, dict):
        issues.append("error: timeouts must be a mapping")
    elif isinstance(timeouts, dict):
        for key, value in timeouts.items():
            if key not in KNOWN_TIMEOUT_KEYS:
                issues.append(f"warning: unknown timeouts key {key!r}")
            if value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                issues.append(f"error: timeouts.{key} must be a number (got {value!r})")
                continue
            if num <= 0:
                issues.append(f"error: timeouts.{key} must be positive (got {num:g})")

    collectors = cfg.get("collectors")
    if collectors is not None and not isinstance(collectors, dict):
        issues.append("error: collectors must be a mapping")
    elif isinstance(collectors, dict):
        for name, entry in collectors.items():
            if name not in KNOWN_COLLECTOR_KEYS:
                issues.append(
                    f"warning: unknown collector {name!r} "
                    f"(known: {', '.join(sorted(KNOWN_COLLECTOR_KEYS))})"
                )
            if isinstance(entry, bool):
                continue
            if not isinstance(entry, dict):
                issues.append(f"error: collectors.{name} must be a bool or mapping")
                continue
            for ek in entry:
                if ek not in KNOWN_COLLECTOR_ENTRY_KEYS:
                    issues.append(f"warning: unknown collectors.{name} key {ek!r}")

    analysis = cfg.get("analysis")
    if analysis is not None and not isinstance(analysis, dict):
        issues.append("error: analysis must be a mapping")
    elif isinstance(analysis, dict):
        for key in analysis:
            if key not in KNOWN_ANALYSIS_KEYS:
                issues.append(f"warning: unknown analysis key {key!r}")
        mode = analysis.get("scoring_mode")
        if mode is not None and str(mode) not in KNOWN_SCORING_MODES:
            issues.append(
                f"warning: analysis.scoring_mode {mode!r} is not one of "
                f"{', '.join(sorted(KNOWN_SCORING_MODES))}"
            )
        pace = analysis.get("pace")
        if pace is not None and not isinstance(pace, dict):
            issues.append("error: analysis.pace must be a mapping")
        elif isinstance(pace, dict):
            for key in pace:
                if key not in KNOWN_PACE_KEYS:
                    issues.append(f"warning: unknown analysis.pace key {key!r}")
        overrides = analysis.get("provider_overrides")
        if overrides is not None and not isinstance(overrides, dict):
            issues.append("error: analysis.provider_overrides must be a mapping")
        elif isinstance(overrides, dict):
            for name in overrides:
                canon = _DEAD_PLAN_KEYS.get(str(name).lower().replace(" ", "-"))
                if canon:
                    issues.append(
                        f"warning: analysis.provider_overrides key {name!r} is dead — "
                        f"use {canon!r} (see provider_config_key aliases)"
                    )

    plans = cfg.get("plans")
    if plans is not None and not isinstance(plans, dict):
        issues.append("error: plans must be a mapping")
    elif isinstance(plans, dict):
        for name in plans:
            canon = _DEAD_PLAN_KEYS.get(str(name).lower().replace(" ", "-"))
            if canon:
                issues.append(
                    f"warning: plans key {name!r} is dead — use {canon!r} "
                    f"(collector id aliases to that config key)"
                )

    return issues


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = _deep_copy(DEFAULT_CONFIG)

    toml_path = default_toml_config_path()
    if toml_path.is_file():
        data = _read_file(toml_path)
        if isinstance(data, dict):
            cfg = _deep_merge(cfg, data)

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser())
    else:
        candidates.append(default_config_path())

    for candidate in candidates:
        if path is not None and not candidate.is_file():
            raise SystemExit(f"Config file not found: {candidate}")
        if candidate.is_file():
            data = _read_file(candidate)
            if isinstance(data, dict):
                cfg = _deep_merge(cfg, data)
            break
    return cfg


def default_config_dir() -> Path:
    """User config directory: ``$XDG_CONFIG_HOME/aiuse`` or ``~/.config/aiuse``."""
    return _xdg_config_home() / "aiuse"


def default_config_path() -> Path:
    """Return the XDG user configuration path for services.yaml."""
    return default_config_dir() / "services.yaml"


def default_toml_config_path() -> Path:
    """Optional TOML settings path (timeouts and future tool knobs)."""
    return default_config_dir() / "config.toml"


def _xdg_config_home() -> Path:
    """XDG config home: prefer absolute ``$XDG_CONFIG_HOME``, else ``~/.config``."""
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        # XDG requires these environment-variable paths to be absolute.
        if candidate.is_absolute():
            return candidate
    return Path.home() / ".config"


def ensure_config_dir() -> Path:
    """Create ``~/.config`` (or XDG home) and ``…/aiuse`` if missing; return it.

    Raises ``OSError`` if a component exists but is not a directory.
    """
    xdg = _xdg_config_home()
    app_dir = xdg / "aiuse"
    for path in (xdg, app_dir):
        if path.exists():
            if not path.is_dir():
                raise OSError(f"config path exists but is not a directory: {path}")
            continue
        path.mkdir(mode=0o755)
    return app_dir


def generate_user_config() -> dict[str, list[str]]:
    """Write default config files under the standard config directory.

    Creates parent directories as needed. Never overwrites an existing file —
    conflicts are listed in the return value under ``skipped``.

    Returns a dict with keys ``created``, ``skipped``, ``errors`` (path strings).
    """
    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    try:
        ensure_config_dir()
    except OSError as exc:
        return {"created": [], "skipped": [], "errors": [str(exc)]}

    targets: list[tuple[Path, str]] = [
        (default_toml_config_path(), _default_toml_text()),
        (default_config_path(), _default_services_yaml_text()),
    ]
    for path, content in targets:
        try:
            if path.exists():
                skipped.append(str(path))
                continue
            path.write_text(content, encoding="utf-8")
            # Restrictive perms for user config (plans are not secrets, but habit).
            try:
                path.chmod(0o600)
            except OSError:
                pass
            created.append(str(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    return {"created": created, "skipped": skipped, "errors": errors}


def _default_toml_text() -> str:
    """Default config.toml body matching built-in timeout defaults."""
    return (
        "# Tool settings for the `aiuse` CLI (generated by `aiuse --generate-config`).\n"
        f"# Location: {default_toml_config_path()}\n"
        "#\n"
        "# This file is separate from services.yaml (plans / analysis thresholds).\n"
        "# Both are optional; built-in defaults apply when a file is missing.\n"
        "\n"
        "[timeouts]\n"
        "# Wall-clock seconds for every external data source\n"
        "# (cswap, codexbar, caut, openusage, tokscale).\n"
        "# Tools either return quickly or hang — long budgets only delay failure.\n"
        f"default = {DEFAULT_SUBPROCESS_TIMEOUT:g}\n"
        "\n"
        "# Optional per-tool overrides (omit to use default):\n"
        "# cswap = 45\n"
        "# codexbar = 45\n"
        "# codexbar_discovery = 45   # `codexbar config providers` (local, usually ms)\n"
        "# caut = 45\n"
        "# openusage = 45\n"
        "# tokscale = 45\n"
        "\n"
        "# CLI `--timeout` / `-t` overrides every tool for that run.\n"
        "# Install tools: packaging/install-deps.sh\n"
    )


def _default_services_yaml_text() -> str:
    """Default services.yaml from DEFAULT_CONFIG (analysis / plans / collectors)."""
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(
            "PyYAML is required to generate services.yaml. Install with: pip install pyyaml"
        ) from exc

    payload = {
        "analysis": _deep_copy(DEFAULT_CONFIG["analysis"]),
        "plans": _deep_copy(DEFAULT_CONFIG["plans"]),
        "collectors": _deep_copy(DEFAULT_CONFIG["collectors"]),
    }
    header = (
        f"# Generated by `aiuse --generate-config`\n"
        f"# Location: {default_config_path()}\n"
        "# Override plan metadata and analysis thresholds for use-it-or-lose-it alerts.\n"
        "# Provider credentials stay in cswap / CodexBar / tokscale — not here.\n"
        "\n"
    )
    body = yaml.safe_dump(
        payload,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return header + body


def _read_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise SystemExit(
                "PyYAML is required for YAML config. Install with: pip install pyyaml  (or use JSON config)"
            ) from exc
        return yaml.safe_load(text)
    if suffix == ".toml":
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover — project requires 3.11+
            import tomli as tomllib  # type: ignore[no-redef]
        return tomllib.loads(text)
    return json.loads(text)


def _deep_copy(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy(v) for v in obj]
    return obj


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = _deep_copy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = _deep_copy(value)
    return out
