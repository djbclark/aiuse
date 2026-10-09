from datetime import timedelta
from io import StringIO

import pytest

from aiuse import cli
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow
from aiuse.watch import (
    WatchError,
    WatchRuntime,
    _watch_color_enabled,
    collect_watch_frame,
    is_quit_key,
    parse_interval,
    render_watch_board,
    run_watch,
)


def _snap() -> Snapshot:
    return Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Codex weekly quota",
                        used_percent=10,
                        remaining_percent=90,
                        resets_at=utcnow() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600", 600.0),
        (600, 600.0),
        ("10m", 600.0),
        ("90s", 90.0),
        ("1h", 3600.0),
        ("5", 5.0),
    ],
)
def test_parse_interval_accepts_seconds_and_suffixes(raw, expected):
    assert parse_interval(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "4", "nope", ""])
def test_parse_interval_rejects_bad_values(raw):
    with pytest.raises(WatchError):
        parse_interval(raw)


def test_quit_keys():
    assert is_quit_key("q")
    assert is_quit_key("Q")
    assert is_quit_key("\x1b")
    assert is_quit_key("\x03")
    assert not is_quit_key("x")
    assert not is_quit_key(None)


@pytest.mark.parametrize(
    ("no_color", "detected", "force_color", "no_color_env", "expected"),
    [
        (False, "truecolor", None, False, True),
        (False, None, None, False, False),
        (True, "truecolor", "1", False, False),
        (False, "truecolor", None, True, False),
        (False, None, "1", False, True),
        (False, "truecolor", "0", False, False),
        (False, "truecolor", "1", True, False),
    ],
)
def test_watch_color_falls_back_without_disabling_terminal_control(
    monkeypatch,
    no_color,
    detected,
    force_color,
    no_color_env,
    expected,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    if no_color_env:
        monkeypatch.setenv("NO_COLOR", "1")
    if force_color is not None:
        monkeypatch.setenv("FORCE_COLOR", force_color)

    assert _watch_color_enabled(no_color=no_color, detected_color_system=detected) is expected


def test_render_watch_board_includes_header_and_matrix():
    text = render_watch_board(
        _snap(),
        [],
        color=False,
        last_at=utcnow(),
        next_in=125,
        collecting_for=None,
    )
    assert "aiuse watch" in text
    assert "q/esc quit" in text
    assert "next in 2:05" in text
    assert "codex" in text.lower() or "SERVICE" in text


def test_render_watch_board_shows_collecting():
    text = render_watch_board(None, [], collecting_for=7, color=False)
    assert "collecting… (7s)" in text
    assert "waiting for first collection" in text


def test_runtime_does_not_overlap_and_fires_immediately_if_collect_overruns():
    clock = [0.0]
    calls: list[float] = []

    def collect():
        calls.append(clock[0])
        clock[0] += 15
        return _snap(), []

    runtime = WatchRuntime(interval=10, collect=collect, now=lambda: clock[0])
    started: list = []

    def start(fn):
        started.append(fn)

    runtime.maybe_start(start)
    assert len(started) == 1
    runtime.maybe_start(start)
    assert len(started) == 1
    started[0]()
    assert calls == [0.0]
    assert runtime.next_due == 15
    runtime.maybe_start(start)
    assert len(started) == 2
    started[1]()
    assert len(calls) == 2


def test_run_watch_once_prints_one_frame():
    out = StringIO()
    code = run_watch(
        {},
        interval=600,
        once=True,
        no_color=True,
        stdout=out,
        collect=lambda: (_snap(), []),
        require_tty=False,
    )
    assert code == 0
    assert "aiuse watch" in out.getvalue()
    assert "q/esc quit" in out.getvalue()


def test_run_watch_loop_quits_on_injected_key(monkeypatch):
    keys = iter(["q"])

    class Reader:
        def read(self):
            return next(keys, None)

    class FakeLive:
        def __init__(self, *args, **kwargs):
            assert kwargs["console"].is_terminal
            assert kwargs["console"].no_color is False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def update(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("rich.live.Live", FakeLive)
    out = StringIO()
    code = run_watch(
        {},
        interval=600,
        once=False,
        no_color=True,
        stdout=out,
        key_reader=Reader(),
        collect=lambda: (_snap(), []),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0


def test_run_watch_stops_default_collection_process_on_quit(monkeypatch):
    instances = []

    class FakeProcessWorker:
        def __init__(self, config, max_age=None):
            self.config = config
            self.started = False
            self.stop_calls = 0
            instances.append(self)

        def start(self):
            self.started = True

        def poll(self):
            return None

        def stop(self):
            self.stop_calls += 1

    class Reader:
        def read(self):
            return "q"

    class FakeLive:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def update(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("aiuse.watch._WatchCollectionProcess", FakeProcessWorker)
    monkeypatch.setattr("rich.live.Live", FakeLive)

    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=Reader(),
        sleep=lambda _s: None,
        require_tty=False,
    )

    assert code == 0
    assert len(instances) == 1
    assert instances[0].started
    assert instances[0].stop_calls >= 1


def test_run_watch_rejects_dumb_terminal_instead_of_showing_blank_board(monkeypatch):
    class TtyOutput(StringIO):
        def isatty(self):
            return True

    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("TTY_COMPATIBLE", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    err = StringIO()

    code = run_watch(
        {},
        interval=600,
        stdout=TtyOutput(),
        stderr=err,
        collect=lambda: (_snap(), []),
    )

    assert code == 2
    assert "TERM=dumb" in err.getvalue()


def test_cli_watch_rejects_json_and_no_tui(monkeypatch):
    monkeypatch.setattr(cli, "run_collectors", lambda _c: (_ for _ in ()).throw(AssertionError("no collect")))
    assert cli.main(["watch", "--json"]) == 2
    assert cli.main(["watch", "--alerts-only"]) == 2
    assert cli.main(["watch", "--flatten"]) == 2
    assert cli.main(["watch", "--no-tui"]) == 2
    assert cli.main(["watch", "--interval", "0"]) == 2


def test_cli_watch_once_uses_collectors(monkeypatch, capsys):
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.should_persist_snapshots", lambda _c: False)
    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    assert cli.main(["watch", "--once", "-q", "--no-color"]) == 0
    captured = capsys.readouterr()
    assert "aiuse watch" in captured.out


def test_collect_watch_frame_persists_when_enabled(monkeypatch):
    saved: list = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.should_persist_snapshots", lambda _c: True)
    monkeypatch.setattr("aiuse.watch.save_snapshot", lambda *a, **k: saved.append(True) or "/tmp/x")
    collect_watch_frame({"analysis": {"persist_snapshots": True}})
    assert saved == [True]


def test_watch_frame_reuses_a_fresh_snapshot_instead_of_collecting(monkeypatch):
    """The board rides the scheduled sampler's data rather than polling beside it."""
    from aiuse.analysis.history import save_snapshot

    save_snapshot(_snap(), [])
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: pytest.fail("must not collect"))
    snapshot, alerts = collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600)
    assert [a.provider for a in snapshot.accounts] == [a.provider for a in _snap().accounts]
    assert alerts == []


def test_watch_frame_collects_when_the_snapshot_is_stale_and_records_a_sample(monkeypatch):
    from datetime import timedelta

    from aiuse import sampler
    from aiuse.analysis.history import load_recent_snapshots, save_snapshot

    old = _snap()
    old.collected_at -= timedelta(hours=2)
    save_snapshot(old, [])
    calls: list[int] = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: calls.append(1) or _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600)
    assert calls == [1] and len(load_recent_snapshots(retention_days=10_000)) == 2
    # The scheduled job now sees a sample this recent and skips its own.
    state = sampler.load_state()
    assert sampler.decide(state, sampler.sampling_settings(None), _snap().collected_at).action == "skip"


def test_board_header_shows_now_data_time_and_the_sampler_schedule():
    from datetime import datetime, timedelta, timezone

    from aiuse import sampler
    from aiuse.report import format_clock
    from aiuse.watch import render_watch_board

    snap = _snap()
    snap.collected_at = datetime(2026, 10, 3, 12, 0, 5, tzinfo=timezone.utc)
    now = snap.collected_at + timedelta(minutes=7)
    state = {"tier": "active", "last_sample_at": snap.collected_at.isoformat()}
    schedule = sampler.schedule(state, sampler.sampling_settings(None))
    assert schedule is not None and schedule[1] == snap.collected_at + timedelta(minutes=15)
    board = render_watch_board(snap, [], color=False, quiet=True, now=now, sample_schedule=schedule)
    header, sampler_line = board.splitlines()[:2]

    def hms(value):
        return format_clock(value, seconds=True)

    assert f"now: {hms(now)}" in header and f"last: {hms(snap.collected_at)}" in header
    assert sampler_line == f"sampler: previous {hms(snap.collected_at)} · next {hms(schedule[1])} · active tier"
    late = render_watch_board(snap, [], color=False, quiet=True, now=now + timedelta(hours=1), sample_schedule=schedule)
    assert "(due)" in late.splitlines()[1]
    assert sampler.schedule({}, sampler.sampling_settings(None)) is None


def test_watch_frame_overlays_newer_burst_samples_on_the_full_snapshot(monkeypatch):
    from datetime import timedelta

    from aiuse.analysis.history import save_snapshot

    def account(provider: str, used: float, source: str = "codexbar") -> AccountUsage:
        window = QuotaWindow(label=f"{provider} weekly", used_percent=used, window_minutes=10080)
        return AccountUsage(
            source=source, provider=provider, billing_kind=BillingKind.SUBSCRIPTION_WINDOW, windows=[window]
        )

    full_at = utcnow() - timedelta(minutes=5)
    save_snapshot(Snapshot(collected_at=full_at, accounts=[account("clinepass", 40), account("codex", 10)]), [])
    for minutes, used in ((2, 44), (4, 47)):
        burst = Snapshot(collected_at=full_at + timedelta(minutes=minutes), accounts=[account("clinepass", used)])
        save_snapshot(burst, [], partial_providers=["clinepass"])
    # A burst reading older than the full snapshot must not roll it back.
    stale = Snapshot(collected_at=full_at - timedelta(minutes=3), accounts=[account("codex", 1)])
    save_snapshot(stale, [], partial_providers=["codex"])

    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: pytest.fail("must not collect"))
    seen: list[Snapshot] = []
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda snap, _c: seen.append(snap) or [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    snapshot, _alerts = collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600)
    used = {a.provider: a.windows[0].used_percent for a in snapshot.accounts}
    assert used == {"clinepass": 47, "codex": 10}
    assert seen == [snapshot], "alerts are re-derived from the overlaid readings"


def test_format_clock_is_twelve_hour_without_leading_zeros():
    from datetime import datetime

    from aiuse.report import format_clock

    evening = datetime(2026, 10, 3, 21, 2, 7).astimezone()
    assert format_clock(evening) == "9:02pm"
    assert format_clock(evening, seconds=True) == "9:02:07pm"
    assert format_clock(evening, date=True) == "Sat Oct 3 9:02pm"
    assert format_clock(datetime(2026, 10, 3, 0, 5).astimezone()) == "12:05am"
    assert format_clock(datetime(2026, 10, 3, 12, 0).astimezone()) == "12:00pm"


def test_board_footer_is_centered_under_the_table():
    from aiuse.watch import render_watch_board

    rows = render_watch_board(_snap(), [], color=False).splitlines()
    width = max(len(r) for r in rows[: rows.index(next(r for r in rows if "Collected at" in r))] if "watch" not in r)
    meta = next(r for r in rows if "Collected at" in r)
    left = len(meta) - len(meta.lstrip())
    assert left == max(0, (width - len(meta.strip())) // 2)
    assert "am ·" in meta or "pm ·" in meta


def test_cli_watch_passes_interval_quiet_color_and_timeout_to_the_board(monkeypatch):
    """Issue #14: -i / -q / --no-color / --timeout reach the board and its collect config."""
    seen: dict = {}

    def fake_run_watch(config, *, interval, once, quiet, no_color, **_kw):
        seen.update(config=config, interval=interval, once=once, quiet=quiet, no_color=no_color)
        return 0

    monkeypatch.setattr("aiuse.watch.run_watch", fake_run_watch)
    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    monkeypatch.setattr(cli, "run_collectors", lambda _c: (_ for _ in ()).throw(AssertionError("no collect")))
    assert cli.main(["watch", "-i", "2m", "-q", "--no-color", "--timeout", "7"]) == 0
    assert seen["interval"] == 120.0
    assert seen["quiet"] is True
    assert seen["no_color"] is True
    assert seen["once"] is False
    assert seen["config"]["timeouts"]["default"] == 7.0
    assert seen["config"]["timeouts"]["force"] == 7.0


def test_cli_watch_defaults_to_a_ten_minute_interval(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("aiuse.watch.run_watch", lambda _config, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    assert cli.main(["watch", "--once"]) == 0
    assert seen["interval"] == 600.0
    assert seen["once"] is True
    assert seen["quiet"] is False
    assert seen["no_color"] is False
