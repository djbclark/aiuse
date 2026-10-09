"""Cross-process minimum interval between live quota queries for one provider.

Some vendors punish a burst of quota probes with rate limits that also stop the
user's real work: agy (antigravity) answers queries in quick succession with
429s on every model. Every aiuse entry point — the CLI, the ``aiuse sample``
LaunchAgent, ``aiuse watch``, ``aiuse serve`` — collects through the same
collectors, and several can run at once, so the limit is enforced here through
a locked state file per provider rather than inside any one process.

While a provider is throttled, a collector reuses the payload it stored on its
last allowed query (CodexBar rows carry their own ``updatedAt``, so the reused
row reports its true age) or skips the part of its call that would reach the
vendor (OpenUsage's ``--force``).
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiuse.analysis import history
from aiuse.config import DEFAULT_QUERY_MIN_INTERVAL
from aiuse.models import parse_dt, utcnow

from .base import CollectorError


def throttle_dir() -> Path:
    return history.snapshot_dir().parent / "query-throttle"


def min_intervals(config: dict[str, Any] | None) -> dict[str, float]:
    """Provider -> minimum seconds between live queries (config over defaults)."""
    table = dict(DEFAULT_QUERY_MIN_INTERVAL)
    configured = (config or {}).get("query_min_interval")
    if isinstance(configured, dict):
        for provider, value in configured.items():
            try:
                table[str(provider).strip().lower()] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return {provider: seconds for provider, seconds in table.items() if seconds > 0}


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class QueryGate:
    """Hold one provider's query lock and decide whether a live query may run.

    Usage::

        with QueryGate("antigravity", min_interval=900, wait=60) as gate:
            if gate.allowed:
                outcome = query()
                gate.record("codexbar", outcome)
            else:
                outcome = gate.reuse("codexbar")

    The lock is held for the whole block, so a second process arriving mid-query
    waits (up to ``wait`` seconds) and then sees the fresh ``queried_at`` instead
    of firing its own query. A process that cannot get the lock in time is
    treated as throttled.
    """

    def __init__(
        self,
        provider: str,
        *,
        min_interval: float,
        wait: float,
        directory: Path | None = None,
    ) -> None:
        self.provider = provider
        self.min_interval = float(min_interval)
        self.wait = max(0.0, float(wait))
        self.directory = directory if directory is not None else throttle_dir()
        self.allowed = False
        self.locked = False
        self.state: dict[str, Any] = {}
        self.now: datetime = utcnow()
        self._lock_fd: int | None = None

    @property
    def _state_path(self) -> Path:
        return self.directory / f"{self.provider}.json"

    @property
    def last_queried_at(self) -> datetime | None:
        return parse_dt(self.state.get("queried_at"))

    @property
    def next_allowed_at(self) -> datetime | None:
        last = self.last_queried_at
        return None if last is None else last + timedelta(seconds=self.min_interval)

    def __enter__(self) -> QueryGate:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(self.directory / f"{self.provider}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        self.state = self._read_state()
        self.now = utcnow()
        last = self.last_queried_at
        self.allowed = self.locked and (
            last is None or last > self.now or (self.now - last).total_seconds() >= self.min_interval
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._lock_fd is not None:
            if self.locked:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def record(self, source: str, outcome: Any = None, *, store: bool = True) -> None:
        """Mark a live query by ``source`` as of now (after it ran); optionally keep its outcome."""
        if not self.locked:
            return
        at = utcnow()
        self.state["queried_at"] = at.isoformat()
        self.state["queried_by"] = source
        if store:
            entry: dict[str, Any] = {"fetched_at": at.isoformat()}
            if isinstance(outcome, BaseException):
                entry["error"] = str(outcome)
            else:
                entry["payload"] = outcome
            self.state.setdefault("sources", {})[source] = entry
        tmp = self._state_path.with_suffix(f".json.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(self.state, default=str), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            tmp.unlink(missing_ok=True)

    def describe(self) -> str:
        """Human-readable reason a query was skipped (for notes and errors)."""
        interval = _format_duration(self.min_interval)
        last = self.last_queried_at
        if not self.locked and last is None:
            return f"{self.provider} quota query skipped: another aiuse process is querying it"
        if last is None:
            return f"{self.provider} quota queried at most every {interval}"
        age = _format_duration((self.now - last).total_seconds())
        nxt = self.next_allowed_at
        remaining = _format_duration((nxt - self.now).total_seconds()) if nxt else "0s"
        by = self.state.get("queried_by") or "aiuse"
        return (
            f"{self.provider} quota queried at most every {interval} "
            f"(last query {age} ago via {by}; next allowed in {remaining})"
        )

    def reuse(self, source: str) -> Any:
        """The payload ``source`` stored on its last allowed query, else a CollectorError."""
        entry = (self.state.get("sources") or {}).get(source)
        if isinstance(entry, dict):
            if "payload" in entry:
                return entry["payload"]
            if entry.get("error"):
                return CollectorError(f"{entry['error']} [{self.describe()}]")
        return CollectorError(self.describe())

    def reused_from(self, source: str) -> datetime | None:
        entry = (self.state.get("sources") or {}).get(source)
        if isinstance(entry, dict) and "payload" in entry:
            return parse_dt(entry.get("fetched_at"))
        return None


class TimeoutBackoff:
    """Cross-process memory of providers whose live query hung until killed.

    A provider that hangs (CodexBar's ``alibabatokenplan`` waits forever when it
    cannot read browser cookies) costs its full timeout on every aiuse run and
    keeps the whole collection that slow. After a timeout the provider is
    skipped for ``base`` seconds, doubling with each further consecutive timeout
    up to ``cap``; a successful query (any answer that is not a timeout) clears
    it. State is one JSON file per source under :func:`throttle_dir`, shared by
    every aiuse process, so the scheduled sampler pays the hang once per
    backoff window instead of once per sample.
    """

    def __init__(
        self,
        source: str,
        *,
        base: float,
        cap: float | None = None,
        directory: Path | None = None,
    ) -> None:
        self.source = source
        self.base = max(0.0, float(base))
        self.cap = max(self.base, float(cap)) if cap is not None else self.base * 12
        self.directory = directory if directory is not None else throttle_dir()

    @property
    def enabled(self) -> bool:
        return self.base > 0

    @property
    def _path(self) -> Path:
        return self.directory / f"{self.source}-timeouts.json"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _update(self, provider: str, change: Callable[[Any], dict[str, Any] | None]) -> None:
        """Replace ``provider``'s entry with ``change(previous)`` under the file lock (None drops it)."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.directory / f"{self.source}-timeouts.lock", os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = self._read()
            entry = change(state.get(provider))
            if entry is None:
                if provider not in state:
                    return
                state.pop(provider, None)
            else:
                state[provider] = entry
            tmp = self._path.with_suffix(f".json.{os.getpid()}.tmp")
            try:
                tmp.write_text(json.dumps(state, default=str), encoding="utf-8")
                os.replace(tmp, self._path)
            except OSError:
                tmp.unlink(missing_ok=True)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _window(self, count: int) -> float:
        return min(self.cap, self.base * (2 ** max(0, count - 1)))

    def blocked(self, provider: str) -> str | None:
        """Why ``provider`` is skipped right now, or None when it may be queried."""
        if not self.enabled:
            return None
        entry = self._read().get(provider.lower())
        if not isinstance(entry, dict):
            return None
        last = parse_dt(entry.get("timed_out_at"))
        if last is None:
            return None
        count = int(entry.get("count") or 1)
        now = utcnow()
        until = last + timedelta(seconds=self._window(count))
        if last > now or now >= until:
            return None
        timeout = entry.get("timeout")
        took = f" ({_format_duration(float(timeout))})" if isinstance(timeout, (int, float)) else ""
        times = "once" if count == 1 else f"{count} times in a row"
        return (
            f"skipped: query timed out{took} {times}, last {_format_duration((now - last).total_seconds())} ago; "
            f"next try in {_format_duration((until - now).total_seconds())}"
        )

    def record_timeout(self, provider: str, timeout: float) -> None:
        if not self.enabled:
            return

        def bump(previous: Any) -> dict[str, Any]:
            count = int(previous.get("count") or 0) + 1 if isinstance(previous, dict) else 1
            return {"timed_out_at": utcnow().isoformat(), "count": count, "timeout": float(timeout)}

        self._update(provider.lower(), bump)

    def clear(self, provider: str) -> None:
        if not self.enabled or provider.lower() not in self._read():
            return
        self._update(provider.lower(), lambda _previous: None)
