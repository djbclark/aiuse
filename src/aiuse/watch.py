"""Opt-in full-screen ``aiuse watch`` monitor (Rich Live, alternate screen)."""

from __future__ import annotations

import copy
import multiprocessing
import os
import re
import select
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TextIO

from aiuse.analysis.history import save_snapshot, should_persist_snapshots
from aiuse.analysis.local_runtimes import maybe_local_runtime_alerts
from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.collectors.runner import run_collectors
from aiuse.config import collection_policy_fingerprint, snapshot_matches_policy
from aiuse.models import Snapshot, UseOrLoseAlert, utcnow
from aiuse.report import _strip_ansi, format_clock, render_clock_matrix, render_stderr_meta

DEFAULT_INTERVAL_S = 600.0
MIN_INTERVAL_S = 5.0
WARN_INTERVAL_S = 30.0
_CONFIG_RELOAD_SECONDS = 2.0
# Manual key commands (u / a): one at a time, at most one firing per window.
# Both poll every vendor, so an impatient key-mash must not reach them.
_MANUAL_COOLDOWN_S = 120.0
# A `u` this soon after an `a` (running or just finished) is covered by the
# sweep — the user is impatient or forgot the sweep takes up to ~90s cold.
_SWEEP_ROLL_IN_S = 90.0
_KEY_NOTE_S = 10.0  # how long a denied-key note stays on the board
_INTERVAL_SUFFIX = {"s": 1.0, "m": 60.0, "h": 3600.0}
_INTERVAL_RE = re.compile(r"^(\d+(?:\.\d+)?)([smh])?$", re.I)

NowFn = Callable[[], float]
CollectFn = Callable[[], tuple[Snapshot, list[UseOrLoseAlert]]]


class KeySource(Protocol):
    def read(self) -> str | None: ...


class WatchError(ValueError):
    """User-facing watch argument / environment error (exit 2)."""


def parse_interval(value: str | float | int) -> float:
    """Parse ``600``, ``10m``, ``90s``, ``1h`` into seconds."""
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        match = _INTERVAL_RE.fullmatch(text)
        if not match:
            raise WatchError(f"invalid watch interval {value!r} (want seconds, or 90s / 10m / 1h)")
        seconds = float(match.group(1)) * _INTERVAL_SUFFIX.get(match.group(2) or "s", 1.0)
    if seconds <= 0:
        raise WatchError("watch interval must be greater than 0")
    if seconds < MIN_INTERVAL_S:
        raise WatchError(f"watch interval must be at least {MIN_INTERVAL_S:g}s")
    return seconds


def _overlay_burst_samples(snapshot: Snapshot) -> bool:
    """Replace accounts with any newer burst-tier reading. True if one applied.

    During a burst the sampler reads the burning providers every few minutes
    into partial samples, while full snapshots stay 15 minutes apart. The
    board is most useful exactly then, so it shows the newer reading for those
    providers and the full snapshot for everything else.
    """
    from aiuse.analysis.history import account_key, partial_sample_dir
    from aiuse.ledger import load_json_range
    from aiuse.models import canonical_provider
    from aiuse.serve import _snapshot_from_accounts_dict

    applied = False
    for row in load_json_range(partial_sample_dir(), snapshot.collected_at, None):
        for fresh in _snapshot_from_accounts_dict(row).accounts:
            if fresh.error or not fresh.windows:
                continue
            provider = canonical_provider(fresh.provider)
            same = [i for i, a in enumerate(snapshot.accounts) if canonical_provider(a.provider) == provider]
            exact = [i for i in same if account_key(snapshot.accounts[i].account) == account_key(fresh.account)]
            # A provider-scoped reading belongs to the provider's only account.
            target = exact[0] if exact else same[0] if len(same) == 1 else None
            if target is None:
                continue
            fresh.account = fresh.account or snapshot.accounts[target].account
            snapshot.accounts[target] = fresh
            applied = True
    return applied


def _frame_from_disk(max_age: float, config: dict[str, Any]) -> tuple[Snapshot, list[UseOrLoseAlert]] | None:
    """The newest full snapshot on disk, if it is younger than ``max_age`` seconds."""
    from aiuse.analysis.history import load_recent_snapshots
    from aiuse.serve import _alerts_from_dicts, _disk_row_age_ok, _snapshot_from_accounts_dict

    rows = load_recent_snapshots(max_count=1)
    if not rows or not _disk_row_age_ok(rows[0], max_age):
        return None
    # A snapshot collected under an older disable list or collector switch is
    # not reusable, however young it is. That is how a long-lived watch kept
    # writing agy out of latest.json after config.toml had enabled it.
    if not snapshot_matches_policy(rows[0], config):
        return None
    snapshot = _snapshot_from_accounts_dict(rows[0])
    if _overlay_burst_samples(snapshot):
        # The stored alerts describe the older readings; re-derive them.
        alerts = analyze_use_or_lose(snapshot, config)
        alerts.extend(maybe_local_runtime_alerts(snapshot, config=config))
        return snapshot, alerts
    return snapshot, _alerts_from_dicts(rows[0].get("alerts") or [])


def all_providers_config(config: dict[str, Any]) -> dict[str, Any]:
    """Config for a screen-only sweep of every provider (watch's ``a`` key).

    Clears ``[disabled_services]``, turns every collector back on, and points
    CodexBar at all of its providers instead of its enabled list. The result
    is displayed and discarded; see ``collect_watch_frame(persist=False)``.
    Cross-process query throttling still applies — a sweep must not hammer a
    vendor the gate is protecting, even for one run.
    """
    sweep = copy.deepcopy(config)
    sweep.pop("disabled_services", None)
    collectors = sweep.get("collectors")
    if isinstance(collectors, dict):
        for name, entry in collectors.items():
            if entry is False:
                collectors[name] = True
            elif isinstance(entry, dict):
                entry["enabled"] = True
        codexbar = collectors.get("codexbar")
        if not isinstance(codexbar, dict):
            codexbar = {}
            collectors["codexbar"] = codexbar
        codexbar["enabled"] = True
        codexbar["providers"] = "all"
    return sweep


def collect_watch_frame(
    config: dict[str, Any], *, max_age: float | None = None, persist: bool | None = None
) -> tuple[Snapshot, list[UseOrLoseAlert]]:
    """One frame for the board, sharing one polling pipeline with ``aiuse sample``.

    With ``max_age`` set, a snapshot the scheduled sampler (or anything else)
    wrote within that many seconds is shown as is: the board and the sampler
    are not two clocks polling the same vendors. Otherwise this collects, and
    records the result the way a sample does — snapshot, token ledger, sampler
    state — so the scheduled job sees a fresh sample and skips its own.
    ``persist=False`` forces the screen-only mode of the all-providers sweep:
    no disk snapshot is reused or written, and the ledger and sampler state
    are left untouched.
    """
    raw_analysis = config.get("analysis")
    analysis_cfg: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    if persist is None:
        persist = should_persist_snapshots(analysis_cfg)
    if max_age and persist:
        cached = _frame_from_disk(max_age, config)
        if cached is not None:
            return cached

    from aiuse import sampler
    from aiuse.ledger import save_ledger

    ledger_sample = None
    if persist:
        snapshot, ledger_sample, _ledger_error = sampler.collect_with_ledger(config, run_collectors)
    else:
        snapshot = run_collectors(config)
    alerts = analyze_use_or_lose(snapshot, config)
    alerts.extend(maybe_local_runtime_alerts(snapshot, config=config))
    if persist:
        retention = int(analysis_cfg.get("snapshot_retention_days") or 90)
        try:
            save_snapshot(snapshot, alerts, retention_days=retention)
            if ledger_sample is not None:
                save_ledger(ledger_sample, collected_at=snapshot.collected_at, retention_days=retention)
            settings = sampler.sampling_settings(config)
            sampler.save_state(sampler.advance_state(sampler.load_state(), snapshot, settings, partial=False))
        except OSError:
            pass
    return snapshot, alerts


_schedule_cache: tuple[float, tuple[datetime, datetime, str] | None] | None = None


def _sample_schedule(config: dict[str, Any]) -> tuple[datetime, datetime, str] | None:
    """When ``aiuse sample`` last ran and is next due. Re-read every 2 seconds:
    the board redraws four times a second and the state file changes rarely."""
    global _schedule_cache
    from aiuse import sampler

    if _schedule_cache is not None and time.monotonic() - _schedule_cache[0] < 2.0:
        return _schedule_cache[1]
    try:
        value = sampler.schedule(sampler.load_state(), sampler.sampling_settings(config))
    except Exception:  # noqa: BLE001 — a header detail must never take the board down
        value = None
    _schedule_cache = (time.monotonic(), value)
    return value


def render_watch_board(
    snapshot: Snapshot | None,
    alerts: list[UseOrLoseAlert],
    *,
    config: dict[str, Any] | None = None,
    color: bool | None = None,
    quiet: bool = False,
    last_at: datetime | None = None,
    next_in: float | None = None,
    collecting_for: float | None = None,
    collecting_all_for: float | None = None,
    error: str | None = None,
    now: datetime | None = None,
    sample_schedule: tuple[datetime, datetime, str] | None = None,
    all_providers: bool = False,
    key_note: str | None = None,
) -> str:
    """Header + clock matrix + optional footer for the alternate-screen board.

    ``last`` is when the data on the board was collected, which is not when
    the board last refreshed: a frame can come from a snapshot the scheduled
    sampler wrote some minutes ago. ``all_providers`` marks the screen-only
    sweep view (``a`` key / ``--all-providers``): every provider was queried
    once and nothing was recorded. ``key_note`` is the short-lived
    explanation shown after a denied ``u``/``a`` press.
    """
    header_bits = ["aiuse watch"]
    if all_providers:
        header_bits.append("ALL PROVIDERS · screen only")
    if now is not None:
        header_bits.append(f"now: {format_clock(now, seconds=True)}")
    data_at = snapshot.collected_at if snapshot is not None else last_at
    if data_at is not None:
        header_bits.append(f"last: {format_clock(data_at, seconds=True)}")
    else:
        header_bits.append("last: —")
    if collecting_for is not None:
        header_bits.append(f"collecting… ({collecting_for:.0f}s)")
    elif next_in is not None:
        mins, secs = divmod(max(0, int(next_in)), 60)
        header_bits.append(f"next in {mins}:{secs:02d}")
    if collecting_all_for is not None:
        header_bits.append(f"collecting all providers… ({collecting_all_for:.0f}s)")
    if key_note:
        header_bits.append(key_note)
    header_bits.append("q/esc quit · u update now · a all providers")
    lines = [" · ".join(header_bits)]
    if sample_schedule is not None:
        previous, due, tier = sample_schedule
        overdue = " (due)" if now is not None and due <= now else ""
        lines.append(
            f"sampler: previous {format_clock(previous, seconds=True)}"
            f" · next {format_clock(due, seconds=True)}{overdue} · {tier} tier"
        )
    if error:
        lines.append(f"collect error: {error}")
    if snapshot is not None:
        matrix = render_clock_matrix(alerts, snapshot=snapshot, config=config, color=color).rstrip()
        lines.append(matrix)
        if not quiet:
            footer = render_stderr_meta(snapshot, alerts, color=color).rstrip()
            if footer:
                # Centered under the table, like the legend lines above it.
                width = max((len(_strip_ansi(row)) for row in matrix.splitlines()), default=0)
                for row in footer.splitlines():
                    lines.append(" " * max(0, (width - len(_strip_ansi(row))) // 2) + row)
    else:
        lines.append("waiting for first collection…")
    return "\n".join(lines)


class StdinKeyReader:
    """Non-blocking stdin key poller. Injectable in tests via ``read``."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream if stream is not None else sys.stdin

    def read(self) -> str | None:
        fd = getattr(self.stream, "fileno", lambda: None)()
        if fd is None:
            return None
        try:
            ready, _, _ = select.select([self.stream], [], [], 0)
        except (OSError, ValueError):
            return None
        if not ready:
            return None
        try:
            chunk = self.stream.read(1)
        except OSError:
            return None
        return chunk or None


def is_quit_key(key: str | None) -> bool:
    return key in {"q", "Q", "\x1b", "\x03"}


def _watch_color_enabled(*, no_color: bool, detected_color_system: str | None) -> bool:
    """Use color when requested and supported; terminal control stays separate."""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    force_color = os.environ.get("FORCE_COLOR")
    if force_color is not None:
        return force_color not in {"", "0"}
    return detected_color_system is not None


@dataclass
class WatchRuntime:
    """Collect scheduling + board state. Collect never overlaps."""

    interval: float
    collect: CollectFn
    now: NowFn = time.monotonic
    snapshot: Snapshot | None = None
    alerts: list[UseOrLoseAlert] = field(default_factory=list)
    last_wall: datetime | None = None
    error: str | None = None
    collecting_started: float | None = None
    next_due: float = 0.0
    _busy: bool = False

    def __post_init__(self) -> None:
        self.next_due = self.now()

    @property
    def collecting(self) -> bool:
        return self.collecting_started is not None

    def maybe_start(self, start: Callable[[Callable[[], None]], None]) -> None:
        if self._busy:
            return
        if self.now() < self.next_due:
            return
        self._busy = True
        self.collecting_started = self.now()
        start(self._run_collect)

    def _run_collect(self) -> None:
        try:
            snapshot, alerts = self.collect()
        except Exception as exc:  # noqa: BLE001 — keep the board up
            self._finish_collect(error=f"{exc.__class__.__name__}: {exc}")
        else:
            self._finish_collect(snapshot=snapshot, alerts=alerts)

    def _finish_collect(
        self,
        *,
        snapshot: Snapshot | None = None,
        alerts: list[UseOrLoseAlert] | None = None,
        error: str | None = None,
    ) -> None:
        """Apply one asynchronous collection result and schedule the next."""
        started = self.collecting_started if self.collecting_started is not None else self.now()
        if error is None and snapshot is not None:
            self.snapshot = snapshot
            self.alerts = alerts or []
            self.last_wall = utcnow()
            self.error = None
        elif error is not None:
            self.error = error
        finished = self.now()
        due = started + self.interval
        self.next_due = finished if finished >= due else due
        self.collecting_started = None
        self._busy = False

    def collecting_for(self) -> float | None:
        if self.collecting_started is None:
            return None
        return max(0.0, self.now() - self.collecting_started)

    def next_in(self) -> float | None:
        if self.collecting:
            return None
        return max(0.0, self.next_due - self.now())


def _collect_process_entry(
    config: dict[str, Any], send: Any, max_age: float | None = None, persist: bool | None = None
) -> None:
    """Collect in an isolated process so an in-flight refresh is cancellable."""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        snapshot, alerts = collect_watch_frame(config, max_age=max_age, persist=persist)
        send.send(("ok", snapshot, alerts))
    except BaseException as exc:  # noqa: BLE001 — return a board error instead of losing the worker
        try:
            send.send(("error", f"{exc.__class__.__name__}: {exc}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        send.close()


class _WatchCollectionProcess:
    """One cancellable live-collection process for interactive watch mode."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        max_age: float | None = None,
        persist: bool | None = None,
    ) -> None:
        self.config = config or {}
        self.max_age = max_age
        self.persist = persist
        methods = multiprocessing.get_all_start_methods()
        self.context: Any = multiprocessing.get_context("fork" if "fork" in methods else "spawn")
        self.process: multiprocessing.Process | None = None
        self.recv: Any = None

    def start(self, config: dict[str, Any] | None = None, max_age: float | None = None) -> None:
        if config is not None:
            self.config = config
        effective_max_age = self.max_age if max_age is None else max_age
        recv, send = self.context.Pipe(duplex=False)
        process = self.context.Process(
            target=_collect_process_entry,
            args=(self.config, send, effective_max_age, self.persist),
            name="aiuse-watch-collect",
            daemon=True,
        )
        process.start()
        send.close()
        self.recv = recv
        self.process = process

    def poll(self) -> tuple[Snapshot | None, list[UseOrLoseAlert], str | None] | None:
        process = self.process
        recv = self.recv
        if process is None or recv is None:
            return None
        if recv.poll():
            try:
                message = recv.recv()
            except EOFError:
                message = ("error", "collection process closed without a result")
            self._cleanup_finished()
            if message[0] == "ok":
                return message[1], message[2], None
            return None, [], str(message[1])
        if not process.is_alive():
            exit_code = process.exitcode
            self._cleanup_finished()
            return None, [], f"collection process exited without a result (status {exit_code})"
        return None

    def stop(self) -> None:
        """Terminate the process group, including active collector subprocesses."""
        process = self.process
        if process is not None and process.is_alive():
            terminated_group = False
            pid = process.pid
            if os.name == "posix" and pid is not None:
                try:
                    os.killpg(pid, signal.SIGTERM)
                    terminated_group = True
                except (ProcessLookupError, PermissionError):
                    pass
            if not terminated_group:
                process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        self._cleanup_finished()

    def _cleanup_finished(self) -> None:
        if self.recv is not None:
            self.recv.close()
        if self.process is not None:
            self.process.join(timeout=1.0)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1.0)
            self.process.close()
        self.recv = None
        self.process = None


def run_watch(
    config: dict[str, Any],
    *,
    interval: float,
    once: bool = False,
    all_providers: bool = False,
    quiet: bool = False,
    no_color: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    key_reader: KeySource | None = None,
    collect: CollectFn | None = None,
    collect_all: CollectFn | None = None,
    now: NowFn | None = None,
    sleep: Callable[[float], None] = time.sleep,
    require_tty: bool | None = None,
    config_loader: Callable[[], dict[str, Any]] | None = None,
) -> int:
    """Run ``aiuse watch``. Always exit 0 on a clean quit.

    ``config_loader`` re-reads config.toml (and reapplies CLI overrides) so a
    long-lived board does not keep the disable list from the moment it started.
    ``all_providers`` is the one-shot ``--all-providers`` sweep: every provider
    collected once (``a`` key's function), printed to stdout, nothing recorded.
    """
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    color = False if no_color else None
    loaded: dict[str, Any] = {"at": None, "config": config, "error": None}
    collected_policy: dict[str, str | None] = {"fingerprint": None}

    def live_config() -> dict[str, Any]:
        if config_loader is None:
            return config
        now_m = time.monotonic()
        loaded_at = loaded["at"]
        if loaded_at is not None and now_m - float(loaded_at) < _CONFIG_RELOAD_SECONDS:
            return loaded["config"]
        try:
            fresh = config_loader()
        except (Exception, SystemExit) as exc:
            loaded["at"] = now_m
            message = str(exc)
            if loaded["error"] != message:
                print(
                    f"warning: config reload failed ({message}); keeping the previous config",
                    file=err,
                )
                loaded["error"] = message
            return loaded["config"]
        loaded["at"] = now_m
        loaded["config"] = fresh
        loaded["error"] = None
        return fresh

    collect_fn = collect or (lambda: collect_watch_frame(live_config(), max_age=interval))
    runtime = WatchRuntime(interval=interval, collect=collect_fn, now=now or time.monotonic)

    if all_providers:
        sweep_config = all_providers_config(live_config())
        frame_error: str | None = None
        try:
            snapshot, alerts = collect_watch_frame(sweep_config, persist=False)
        except Exception as exc:  # noqa: BLE001 — show the board error line, not a traceback
            frame_error = f"{exc.__class__.__name__}: {exc}"
            snapshot, alerts = None, []
        print(
            render_watch_board(
                snapshot,
                alerts,
                config=sweep_config,
                color=color,
                quiet=quiet,
                last_at=utcnow() if snapshot is not None else None,
                next_in=None,
                collecting_for=None,
                error=frame_error,
                now=utcnow(),
                sample_schedule=_sample_schedule(sweep_config),
                all_providers=True,
            ),
            file=out,
        )
        return 0

    if once:
        runtime._run_collect()
        current = live_config()
        print(
            render_watch_board(
                runtime.snapshot,
                runtime.alerts,
                config=current,
                color=color,
                quiet=quiet,
                last_at=runtime.last_wall,
                next_in=None,
                collecting_for=None,
                error=runtime.error,
                now=utcnow(),
                sample_schedule=_sample_schedule(current),
            ),
            file=out,
        )
        return 0

    tty = bool(getattr(out, "isatty", lambda: False)())
    if require_tty is None:
        require_tty = True
    if require_tty and not tty:
        print(
            "aiuse watch requires an interactive terminal (try `aiuse --json` or the hourly LaunchAgent).",
            file=err,
        )
        return 2
    from rich.console import Console

    console = Console(file=out, force_terminal=True, color_system="auto", no_color=False)
    if require_tty and console.is_dumb_terminal:
        term = os.environ.get("TERM") or "unset"
        print(
            f"aiuse watch requires an ANSI-compatible terminal (TERM={term} cannot display the full-screen board).",
            file=err,
        )
        return 2

    color_enabled = _watch_color_enabled(
        no_color=no_color,
        detected_color_system=console.color_system,
    )
    reader = key_reader or StdinKeyReader()
    stop = threading.Event()
    process_worker = _WatchCollectionProcess(config, max_age=interval) if collect is None else None
    # The ``a``-key sweep: its own worker so it can run beside the regular
    # cycle, collecting every provider once with nothing written to disk.
    sweep_worker = _WatchCollectionProcess(persist=False) if collect is None else None
    collect_all_fn = collect_all or (lambda: collect_watch_frame(all_providers_config(live_config()), persist=False))
    sweep = WatchRuntime(interval=float("inf"), collect=collect_all_fn, now=now or time.monotonic)
    sweep.next_due = float("inf")  # only ever started by the ``a`` key
    sweep_config_held: dict[str, Any] = {"cfg": None}
    show_all_view: dict[str, bool] = {"v": False}
    # Manual key commands (u/a): one in flight at most, one firing per
    # _MANUAL_COOLDOWN_S, "forced" marks a u-triggered regular collect.
    manual: dict[str, Any] = {
        "forced": False,
        "last_fired": None,  # monotonic time the last u/a actually fired
        "u_in_flight": False,  # the running regular collect was u-triggered
        "a_fired_at": None,  # monotonic time the last a sweep fired
        "note": None,  # (expiry_monotonic, text) explaining a denied press
    }

    def _gate_manual(kind: str) -> str | None:
        """Why this ``u``/``a`` press cannot fire now; ``None`` when it may.

        One manual command at a time, at most one firing per two minutes
        (they both poll every vendor). A ``u`` within 90s of an ``a`` —
        running or just finished — is covered by that sweep: the user is
        impatient or forgot the sweep was already on its way.
        """
        now_m = runtime.now()
        if kind == "u":
            if sweep.collecting:
                elapsed = sweep.collecting_for() or 0.0
                if elapsed < _SWEEP_ROLL_IN_S:
                    return f"u: rolled into the all-providers run ({elapsed:.0f}s in)"
                return f"u: waiting for the all-providers run ({elapsed:.0f}s in)"
            if runtime.collecting:
                if manual["u_in_flight"]:
                    return "u: update already collecting"
                return "u: a refresh is already collecting"
            a_fired_at = manual["a_fired_at"]
            if a_fired_at is not None and now_m - a_fired_at < _SWEEP_ROLL_IN_S:
                return f"u: covered by the all-providers run ({now_m - a_fired_at:.0f}s ago)"
        else:
            if sweep.collecting:
                elapsed = sweep.collecting_for() or 0.0
                return f"a: all-providers run already in flight ({elapsed:.0f}s in)"
            if runtime.collecting and manual["u_in_flight"]:
                return "a: waiting for the u update to finish"
        last_fired = manual["last_fired"]
        if last_fired is not None:
            left = _MANUAL_COOLDOWN_S - (now_m - last_fired)
            if left > 0:
                return f"{kind}: manual refresh ready in {left:.0f}s (2min minimum)"
        return None

    def _deny_key(note: str) -> None:
        manual["note"] = (runtime.now() + _KEY_NOTE_S, note)

    def start_worker(fn: Callable[[], None]) -> None:
        fresh = live_config()
        collected_policy["fingerprint"] = collection_policy_fingerprint(fresh)
        force = manual["forced"]
        manual["forced"] = False
        manual["u_in_flight"] = force
        if force:
            manual["last_fired"] = runtime.now()
        if process_worker is not None:
            # max_age=0 skips the disk snapshot so ``u`` really means now.
            process_worker.start(fresh, max_age=0.0 if force else None)
        else:
            threading.Thread(target=fn, name="aiuse-watch-collect", daemon=True).start()

    def start_sweep(fn: Callable[[], None]) -> None:
        fired_at = runtime.now()
        manual["last_fired"] = fired_at
        manual["a_fired_at"] = fired_at
        if sweep_worker is not None:
            sweep_worker.start(sweep_config_held["cfg"] or all_providers_config(live_config()))
        else:
            threading.Thread(target=fn, name="aiuse-watch-all-providers", daemon=True).start()

    runtime.maybe_start(start_worker)

    fd = None
    old_attrs = None
    try:
        import termios
        import tty as tty_mod

        stream = getattr(reader, "stream", sys.stdin)
        fd = stream.fileno()
        old_attrs = termios.tcgetattr(fd)
        tty_mod.setcbreak(fd)
    except Exception:  # noqa: BLE001 — no cbreak available (tests, pipes, Windows)
        old_attrs = None

    try:
        from rich.live import Live
        from rich.text import Text

        def _render() -> Text:
            current = live_config()
            showing_all = show_all_view["v"] and sweep.snapshot is not None
            if showing_all:
                source = sweep
                view_config = sweep_config_held["cfg"] or current
                next_in_val: float | None = None
                collecting_for_val: float | None = None
            else:
                source = runtime
                view_config = current
                next_in_val = runtime.next_in()
                collecting_for_val = runtime.collecting_for()
            error = source.error
            if not showing_all and sweep.error:
                suffix = f"all-providers run: {sweep.error}"
                error = f"{error} · {suffix}" if error else suffix
            key_note: str | None = None
            note_state = manual["note"]
            if note_state is not None:
                note_expiry, note_text = note_state
                if runtime.now() < note_expiry:
                    key_note = note_text
                else:
                    manual["note"] = None
            return Text.from_ansi(
                render_watch_board(
                    source.snapshot,
                    source.alerts,
                    config=view_config,
                    color=color_enabled,
                    quiet=quiet,
                    last_at=source.last_wall,
                    next_in=next_in_val,
                    collecting_for=collecting_for_val,
                    collecting_all_for=sweep.collecting_for(),
                    error=error,
                    now=utcnow(),
                    sample_schedule=_sample_schedule(current),
                    all_providers=showing_all,
                    key_note=key_note,
                )
            )

        with Live(_render(), console=console, screen=True, auto_refresh=False, transient=True) as live:
            while not stop.is_set():
                if process_worker is not None:
                    result = process_worker.poll()
                    if result is not None:
                        snapshot, alerts, error = result
                        if snapshot is not None:
                            # A fresh regular frame replaces the sweep view.
                            show_all_view["v"] = False
                        runtime._finish_collect(snapshot=snapshot, alerts=alerts, error=error)
                if sweep_worker is not None:
                    sweep_result = sweep_worker.poll()
                    if sweep_result is not None:
                        sweep_snapshot, sweep_alerts, sweep_error = sweep_result
                        if sweep_snapshot is not None:
                            show_all_view["v"] = True
                        sweep._finish_collect(snapshot=sweep_snapshot, alerts=sweep_alerts, error=sweep_error)
                key = reader.read()
                if is_quit_key(key):
                    if process_worker is not None:
                        process_worker.stop()
                    break
                if key in ("u", "U"):
                    denial = _gate_manual("u")
                    if denial is None:
                        manual["forced"] = True
                        runtime.next_due = runtime.now()
                    else:
                        _deny_key(denial)
                elif key in ("a", "A"):
                    denial = _gate_manual("a")
                    if denial is None:
                        sweep_config_held["cfg"] = all_providers_config(live_config())
                        sweep.next_due = sweep.now()
                    else:
                        _deny_key(denial)
                # A config.toml edit has to take effect before the interval
                # elapses. Otherwise the board keeps the old disable list and
                # the next refresh can overwrite a newer snapshot with it.
                if config_loader is not None and not runtime.collecting:
                    fresh_fp = collection_policy_fingerprint(live_config())
                    if collected_policy["fingerprint"] is not None and fresh_fp != collected_policy["fingerprint"]:
                        runtime.next_due = runtime.now()
                runtime.maybe_start(start_worker)
                sweep.maybe_start(start_sweep)
                live.update(_render(), refresh=True)
                sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if process_worker is not None:
            process_worker.stop()
        if sweep_worker is not None:
            sweep_worker.stop()
        if fd is not None and old_attrs is not None:
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:  # noqa: BLE001 — restore is best-effort
                pass
    return 0
