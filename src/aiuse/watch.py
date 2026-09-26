"""Opt-in full-screen ``aiuse watch`` monitor (Rich Live, alternate screen)."""

from __future__ import annotations

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
from aiuse.models import Snapshot, UseOrLoseAlert, utcnow
from aiuse.report import render_clock_matrix, render_stderr_meta
from aiuse.tui import should_use_tui

DEFAULT_INTERVAL_S = 600.0
MIN_INTERVAL_S = 5.0
WARN_INTERVAL_S = 30.0
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


def collect_watch_frame(config: dict[str, Any]) -> tuple[Snapshot, list[UseOrLoseAlert]]:
    """One live collect + analysis pass (same as a normal CLI run)."""
    snapshot = run_collectors(config)
    alerts = analyze_use_or_lose(snapshot, config)
    alerts.extend(maybe_local_runtime_alerts(snapshot, config=config))
    raw_analysis = config.get("analysis")
    analysis_cfg: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    if should_persist_snapshots(analysis_cfg):
        try:
            save_snapshot(
                snapshot,
                alerts,
                retention_days=int(analysis_cfg.get("snapshot_retention_days") or 90),
            )
        except OSError:
            pass
    return snapshot, alerts


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
    error: str | None = None,
) -> str:
    """Header + clock matrix + optional footer for the alternate-screen board."""
    header_bits = ["aiuse watch"]
    if last_at is not None:
        header_bits.append(f"last: {last_at.astimezone().strftime('%H:%M:%S')}")
    else:
        header_bits.append("last: —")
    if collecting_for is not None:
        header_bits.append(f"collecting… ({collecting_for:.0f}s)")
    elif next_in is not None:
        mins, secs = divmod(max(0, int(next_in)), 60)
        header_bits.append(f"next in {mins}:{secs:02d}")
    header_bits.append("q/esc quit")
    lines = [" · ".join(header_bits)]
    if error:
        lines.append(f"collect error: {error}")
    if snapshot is not None:
        lines.append(render_clock_matrix(alerts, snapshot=snapshot, config=config, color=color).rstrip())
        if not quiet:
            footer = render_stderr_meta(snapshot, alerts, color=color).rstrip()
            if footer:
                lines.append(footer)
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


def _collect_process_entry(config: dict[str, Any], send: Any) -> None:
    """Collect in an isolated process so an in-flight refresh is cancellable."""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        snapshot, alerts = collect_watch_frame(config)
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

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        methods = multiprocessing.get_all_start_methods()
        self.context: Any = multiprocessing.get_context("fork" if "fork" in methods else "spawn")
        self.process: multiprocessing.Process | None = None
        self.recv: Any | None = None

    def start(self) -> None:
        recv, send = self.context.Pipe(duplex=False)
        process = self.context.Process(
            target=_collect_process_entry,
            args=(self.config, send),
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
    quiet: bool = False,
    no_color: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    key_reader: KeySource | None = None,
    collect: CollectFn | None = None,
    now: NowFn | None = None,
    sleep: Callable[[float], None] = time.sleep,
    require_tty: bool | None = None,
) -> int:
    """Run ``aiuse watch``. Always exit 0 on a clean quit."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    color = False if no_color else None
    collect_fn = collect or (lambda: collect_watch_frame(config))
    runtime = WatchRuntime(interval=interval, collect=collect_fn, now=now or time.monotonic)

    if once:
        runtime._run_collect()
        print(
            render_watch_board(
                runtime.snapshot,
                runtime.alerts,
                config=config,
                color=color,
                quiet=quiet,
                last_at=runtime.last_wall,
                next_in=None,
                collecting_for=None,
                error=runtime.error,
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
    force_compatible = os.environ.get("TTY_COMPATIBLE") == "1" or os.environ.get("FORCE_COLOR") not in {
        None,
        "",
        "0",
    }
    if require_tty and os.environ.get("TERM", "").casefold() == "dumb" and not force_compatible:
        print(
            "aiuse watch requires an ANSI-compatible terminal (TERM=dumb cannot display the full-screen board).",
            file=err,
        )
        return 2

    use_style = should_use_tui(as_json=False, alerts_only=False, no_tui=False, stream=out)
    reader = key_reader or StdinKeyReader()
    stop = threading.Event()
    process_worker = _WatchCollectionProcess(config) if collect is None else None

    def start_worker(fn: Callable[[], None]) -> None:
        if process_worker is not None:
            process_worker.start()
        else:
            threading.Thread(target=fn, name="aiuse-watch-collect", daemon=True).start()

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
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        # Full-screen cursor control is required even when color is disabled.
        # Explicitly supply a basic color system because Rich otherwise emits
        # no Live screen at all for TERM=dumb or ``--no-color``. Rich's
        # ``no_color`` also strips screen-control escapes, so color suppression
        # happens in _render() while the console keeps terminal controls.
        console = Console(file=out, force_terminal=True, color_system="standard", no_color=False)

        def _render() -> Text:
            return Text.from_ansi(
                render_watch_board(
                    runtime.snapshot,
                    runtime.alerts,
                    config=config,
                    color=False if no_color else use_style,
                    quiet=quiet,
                    last_at=runtime.last_wall,
                    next_in=runtime.next_in(),
                    collecting_for=runtime.collecting_for(),
                    error=runtime.error,
                )
            )

        with Live(_render(), console=console, screen=True, auto_refresh=False, transient=True) as live:
            while not stop.is_set():
                if process_worker is not None:
                    result = process_worker.poll()
                    if result is not None:
                        snapshot, alerts, error = result
                        runtime._finish_collect(snapshot=snapshot, alerts=alerts, error=error)
                if is_quit_key(reader.read()):
                    if process_worker is not None:
                        process_worker.stop()
                    break
                runtime.maybe_start(start_worker)
                live.update(_render(), refresh=True)
                sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if process_worker is not None:
            process_worker.stop()
        if fd is not None and old_attrs is not None:
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:  # noqa: BLE001 — restore is best-effort
                pass
    return 0
