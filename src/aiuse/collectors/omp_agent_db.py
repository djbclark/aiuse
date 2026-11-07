"""omp's Anthropic API-key pool, read from omp's own credential database.

omp (oh-my-pi) keeps its logins in ``~/.omp/agent/agent.db``. An enabled
``anthropic`` credential of type ``api_key`` spends Console API credits, a pool
separate from the Claude subscription windows. The database holds no balance,
so this row says only whether the pool is *usable now* (binary present, an
enabled key, no active rate-limit block) and leaves headroom unknown.

The ``data`` column holds the key itself. It is never selected: the queries
name only ``provider``, ``credential_type``, ``disabled_cause`` and the block
table's timestamps.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, utcnow
from aiuse.omp_rate_events import resolve_omp_agent_db

from .base import CollectorNotInstalled, which

WINDOW_LABEL = "api key"
WINDOW_ID = "omp-api-key"
ACCOUNT_LABEL = "anthropic api key"


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


def collect_omp_agent_db(
    *,
    db_path: str | Path | None = None,
    now: datetime | None = None,
    omp_binary: str | None = None,
) -> list[AccountUsage]:
    """One ``omp`` row for the Anthropic API-key pool; nothing when omp has no database."""
    path = resolve_omp_agent_db(db_path)
    if not path.is_file():
        raise CollectorNotInstalled("omp has no agent database")
    moment = now or utcnow()
    now_ms = int(moment.timestamp() * 1000)
    binary = omp_binary if omp_binary is not None else which("omp")

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
    if not reason and not binary:
        reason = "omp binary not on PATH"

    usable = not reason
    window = QuotaWindow(
        label=WINDOW_LABEL,
        window_id=WINDOW_ID,
        reset_description="Anthropic API credits; no balance known (omp's database records none)",
    )
    return [
        AccountUsage(
            source="omp_agent_db",
            provider="omp",
            account=ACCOUNT_LABEL,
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
            collected_at=moment,
        )
    ]
