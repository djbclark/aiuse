"""omp API-key pools, read from omp's local credential metadata.

omp (oh-my-pi) keeps Anthropic logins in ``~/.omp/agent/agent.db``. An enabled
``anthropic`` credential of type ``api_key`` spends Console API credits, a pool
separate from the Claude subscription windows. The database holds no balance,
so this row says only whether the pool is *usable now* (binary present, an
enabled key, no active rate-limit block) and leaves headroom unknown.

omp also reads OpenAI API-key configuration from ``~/.omp/agent/models.yml``.
Only presence of a non-blank ``providers.openai.apiKey`` string and the sibling
``config.yml`` disabledProviders are inspected; no omp process is started and
the value is never resolved, executed, logged or returned. This advertises
configuration, not authentication; project and runtime overlays are unobserved.

The Anthropic database ``data`` column holds the key itself. It is never
selected: the queries name only ``provider``, ``credential_type``,
``disabled_cause`` and the block table's timestamps.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, utcnow
from aiuse.omp_rate_events import resolve_omp_agent_db

from .base import CollectorNotInstalled, which

ANTHROPIC_WINDOW_LABEL = "api key"
ANTHROPIC_WINDOW_ID = "omp-api-key"
ANTHROPIC_ACCOUNT_LABEL = "anthropic api key"
OPENAI_SOURCE = "omp_models_yml"
OPENAI_WINDOW_LABEL = "OpenAI api key"
OPENAI_WINDOW_ID = "omp-openai-api-key"
OPENAI_ACCOUNT_LABEL = "openai api key"
_CONFIG_READ_LIMIT_BYTES = 256 * 1024


def _inspect(path: Path, now_ms: int) -> tuple[int, int, int | None]:
    """(enabled api_key count, unblocked count, earliest block end among blocked keys)."""
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            """
            SELECT c.id,
                   (SELECT MAX(b.blocked_until_ms) FROM auth_credential_blocks b
                     WHERE b.credential_id = c.id AND b.blocked_until_ms > ?)
            FROM auth_credentials c
            WHERE c.provider = 'anthropic'
              AND c.credential_type = 'api_key'
              AND c.disabled_cause IS NULL
            """,
            (now_ms,),
        ).fetchall()
    blocked_ends = [int(end) for _, end in rows if end is not None]
    unblocked = len(rows) - len(blocked_ends)
    return len(rows), unblocked, min(blocked_ends) if blocked_ends else None


def resolve_omp_models_yml(models_yml_path: str | Path | None = None) -> Path:
    if models_yml_path is not None:
        return Path(models_yml_path)
    override = os.environ.get("AIUSE_OMP_MODELS_YML")
    if override:
        return Path(override)
    return _omp_agent_dir() / "models.yml"


def _omp_agent_dir() -> Path:
    profile = os.environ.get("OMP_PROFILE", os.environ.get("PI_PROFILE", "default")).strip()
    root = Path(os.path.normpath(str(Path.home()) + "/" + (os.environ.get("PI_CONFIG_DIR") or ".omp")))
    if profile and profile != "default":
        return root / "profiles" / profile / "agent"
    override = os.environ.get("PI_CODING_AGENT_DIR")
    # Node path.resolve resolves against cwd without expanding a literal '~'.
    return Path(os.path.abspath(override)) if override else root / "agent"


def _read_mapping(path: Path, *, label: str) -> tuple[dict | None, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(_CONFIG_READ_LIMIT_BYTES + 1)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"{label} unreadable: {type(exc).__name__}"
    if len(raw) > _CONFIG_READ_LIMIT_BYTES:
        return None, f"{label} exceeds 256 KiB read cap"
    try:
        text = raw.decode("utf-8")
        loaded = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError, RecursionError, ValueError) as exc:
        return None, f"{label} malformed: {type(exc).__name__}"
    if loaded is None:
        return {}, None
    if not isinstance(loaded, dict):
        return None, f"{label} root is not a mapping"
    return loaded, None


def _openai_config_reason(path: Path) -> str | None:
    """Inspect only agent-level config.yml; never resolve settings or credentials."""
    loaded, reason = _read_mapping(path, label="omp config.yml")
    if reason:
        return reason
    if loaded is None:  # Missing config is the default (no disabled providers).
        return None
    disabled = loaded.get("disabledProviders", [])
    if not isinstance(disabled, list) or any(not isinstance(item, str) for item in disabled):
        return "omp config.yml disabledProviders is not a flat list of strings"
    return "openai disabled in omp config.yml" if "openai" in disabled else None


def _has_openai_api_key(path: Path) -> bool:
    loaded, _ = _read_mapping(path, label="omp models.yml")
    if loaded is None:
        return False
    providers = loaded.get("providers")
    if not isinstance(providers, dict):
        return False
    openai = providers.get("openai")
    if not isinstance(openai, dict):
        return False
    api_key = openai.get("apiKey")
    return isinstance(api_key, str) and bool(api_key.strip())


def collect_omp_agent_db(
    *,
    db_path: str | Path | None = None,
    models_yml_path: str | Path | None = None,
    now: datetime | None = None,
    omp_binary: str | None = None,
) -> list[AccountUsage]:
    """One ``omp`` row per configured API-key route; nothing when omp has no local route."""
    moment = now or utcnow()
    binary = omp_binary if omp_binary is not None else which("omp")
    rows: list[AccountUsage] = []

    path = resolve_omp_agent_db(db_path)
    if path.is_file():
        rows.append(_anthropic_row(path, now=moment, omp_binary=binary))

    models_path = resolve_omp_models_yml(models_yml_path)
    if binary and _has_openai_api_key(models_path):
        rows.append(
            _openai_row(
                models_path, now=moment, disabled_reason=_openai_config_reason(models_path.parent / "config.yml")
            )
        )

    if not rows:
        raise CollectorNotInstalled("omp has no local API-key route")
    return rows


def _anthropic_row(path: Path, *, now: datetime, omp_binary: str | None) -> AccountUsage:
    now_ms = int(now.timestamp() * 1000)
    available_at: datetime | None = None
    try:
        enabled, unblocked, first_block_end = _inspect(path, now_ms)
    except (sqlite3.Error, OSError) as exc:
        enabled, unblocked, first_block_end = 0, 0, None
        reason = f"omp agent database unreadable: {type(exc).__name__}"
    else:
        if not enabled:
            reason = "no enabled Anthropic api_key credential in omp (oauth logins spend the Claude plan windows)"
        elif unblocked == 0:
            assert first_block_end is not None
            available_at = datetime.fromtimestamp(first_block_end / 1000, tz=timezone.utc)
            reason = "every enabled Anthropic api_key credential is rate-limit blocked"
        else:
            reason = ""
    if not reason and not omp_binary:
        reason = "omp binary not on PATH"

    usable = not reason
    window = QuotaWindow(
        label=ANTHROPIC_WINDOW_LABEL,
        window_id=ANTHROPIC_WINDOW_ID,
        reset_description="Anthropic API credits; no balance known (omp's database records none)",
    )
    return AccountUsage(
        source="omp_agent_db",
        provider="omp",
        account=ANTHROPIC_ACCOUNT_LABEL,
        billing_kind=BillingKind.PAYG_API,
        windows=[window],
        api_key_pool={
            "usable": usable,
            "available_at": available_at.isoformat() if available_at is not None else None,
            "reason": reason or None,
            "enabled_credentials": enabled,
        },
        notes=[
            "omp Anthropic api_key pool read from ~/.omp/agent/agent.db (credential metadata only); "
            "headroom is unknown: omp records no balance."
        ]
        + ([f"Not usable: {reason}."] if reason else []),
        raw={"db": str(path)},
        collected_at=now,
    )


def _openai_row(path: Path, *, now: datetime, disabled_reason: str | None = None) -> AccountUsage:
    usable = disabled_reason is None
    window = QuotaWindow(
        label=OPENAI_WINDOW_LABEL,
        window_id=OPENAI_WINDOW_ID,
        reset_description="OpenAI API credits configured for omp; balance unknown (omp models.yml records no spend)",
    )
    return AccountUsage(
        source=OPENAI_SOURCE,
        provider="omp",
        account=OPENAI_ACCOUNT_LABEL,
        billing_kind=BillingKind.PAYG_API,
        windows=[window],
        api_key_pool={
            "usable": usable,
            "available_at": None,
            "reason": disabled_reason,
            "enabled_credentials": 1,
            "provider": "openai",
            "credential_source": "models_yml",
        },
        notes=[
            "omp OpenAI advertisement: non-blank providers.openai.apiKey in models.yml and "
            "disabledProviders in the same directory's config.yml only; omp is never invoked, "
            "the apiKey value is not resolved, executed or emitted, and headroom is unknown. "
            "Project overlays, CLI/runtime flags and PI_CONFIG_FILES overlays are not observed. "
            "Configured and not disabled does not mean authenticated: failing key commands, "
            "unset environment references and insufficient credits are not detected. "
            "Explicit omp source pins suppress this independent row."
        ]
        + ([f"Not usable: {disabled_reason}."] if disabled_reason else []),
        raw={"models_yml": str(path), "openai_api_key_configured": True},
        collected_at=now,
    )
