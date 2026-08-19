from datetime import timedelta
from io import StringIO

import pytest

from aiuse import cli
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow
from aiuse.watch import (
    WatchError,
    WatchRuntime,
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
            pass

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
