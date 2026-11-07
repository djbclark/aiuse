"""omp API-key pools, read from omp's local credential metadata.

omp (oh-my-pi) keeps Anthropic logins in ``~/.omp/agent/agent.db``. An enabled
``anthropic`` credential of type ``api_key`` spends Console API credits, a pool
separate from the Claude subscription windows. The database holds no balance,
so this row says only whether the pool is *usable now* (binary present, an
enabled key, no active rate-limit block) and leaves headroom unknown.

omp also reads OpenAI API-key configuration from ``~/.omp/agent/models.yml``.
Only the presence of a non-empty ``providers.openai.apiKey`` string is reported:
the value is never executed, selected from storage, logged or returned.

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
_DEFAULT_MODELS_YML = "~/.omp/agent/models.yml"


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
    return Path(os.path.expanduser(_DEFAULT_MODELS_YML))


def _has_openai_api_key(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return False
    if not isinstance(loaded, dict):
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
        rows.append(_openai_row(models_path, now=moment))

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


def _openai_row(path: Path, *, now: datetime) -> AccountUsage:
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
            "usable": True,
            "available_at": None,
            "reason": None,
            "enabled_credentials": 1,
            "provider": "openai",
            "credential_source": "models_yml",
        },
        notes=[
            "omp OpenAI apiKey route configured in ~/.omp/agent/models.yml (presence only); "
            "the apiKey value is not executed or emitted, and headroom is unknown."
        ],
        raw={"models_yml": str(path), "openai_api_key_configured": True},
        collected_at=now,
    )
