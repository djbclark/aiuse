"""Opt-in full-screen ``aiuse watch`` monitor (Rich Live, alternate screen)."""

from __future__ import annotations

import re
import select
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
        started = self.collecting_started if self.collecting_started is not None else self.now()
        try:
            snapshot, alerts = self.collect()
            self.snapshot = snapshot
            self.alerts = alerts
            self.last_wall = utcnow()
            self.error = None
        except Exception as exc:  # noqa: BLE001 — keep the board up
            self.error = f"{exc.__class__.__name__}: {exc}"
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

    use_style = should_use_tui(as_json=False, alerts_only=False, no_tui=no_color, stream=out)
    reader = key_reader or StdinKeyReader()
    stop = threading.Event()

    def start_worker(fn: Callable[[], None]) -> None:
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

        console = Console(file=out, force_terminal=use_style and not no_color, no_color=bool(no_color))

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
                if is_quit_key(reader.read()):
                    break
                runtime.maybe_start(start_worker)
                live.update(_render(), refresh=True)
                sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if fd is not None and old_attrs is not None:
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:  # noqa: BLE001 — restore is best-effort
                pass
    return 0
