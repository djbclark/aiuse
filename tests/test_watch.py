from datetime import timedelta
from io import StringIO

import pytest

from aiuse import cli
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow
from aiuse.watch import (
    WatchError,
    WatchRuntime,
    _watch_color_enabled,
    all_providers_config,
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
        def __init__(self, config=None, max_age=None, persist=None):
            self.config = config or {}
            self.started = False
            self.stop_calls = 0
            instances.append(self)

        def start(self, config=None, max_age=None):
            if config is not None:
                self.config = config
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
    # instances[0] is the regular worker; instances[1] the all-providers sweep.
    assert len(instances) == 2
    assert instances[0].started
    assert instances[0].config == {"collectors": {}}
    assert instances[0].stop_calls >= 1
    assert instances[1].stop_calls >= 1


def test_run_watch_starts_collection_with_reloaded_config(monkeypatch):
    instances = []

    class FakeProcessWorker:
        def __init__(self, config=None, max_age=None, persist=None):
            self.config = config or {}
            self.starts = []
            self.pending = False
            self.stop_calls = 0
            instances.append(self)

        def start(self, config=None, max_age=None):
            if config is not None:
                self.config = config
            self.starts.append(self.config)
            self.pending = True

        def poll(self):
            if self.pending:
                self.pending = False
                return _snap(), [], None
            return None

        def stop(self):
            self.stop_calls += 1

    class Reader:
        def __init__(self):
            self.keys = iter([None, None, "q"])

        def read(self):
            return next(self.keys, "q")

    class FakeLive:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def update(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("aiuse.watch._CONFIG_RELOAD_SECONDS", 0)
    monkeypatch.setattr("aiuse.watch._WatchCollectionProcess", FakeProcessWorker)
    monkeypatch.setattr("rich.live.Live", FakeLive)
    loaded = {"n": 0}

    def loader():
        loaded["n"] += 1
        if loaded["n"] <= 2:
            return {}
        return {"disabled_services": {"hermes": "off"}}

    code = run_watch(
        {"collectors": {"hermes": {"enabled": True}}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=Reader(),
        sleep=lambda _s: None,
        require_tty=False,
        config_loader=loader,
    )

    assert code == 0
    assert [item.get("disabled_services") for item in instances[0].starts] == [
        None,
        {"hermes": "off"},
    ]


def test_run_watch_keeps_the_previous_config_when_reload_fails(monkeypatch):
    instances = []

    class FakeProcessWorker:
        def __init__(self, config=None, max_age=None, persist=None):
            self.config = config or {}
            instances.append(self)

        def start(self, config=None, max_age=None):
            if config is not None:
                self.config = config

        def poll(self):
            return None

        def stop(self):
            return None

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
    initial = {"disabled_services": {"antigravity": "keep"}}
    err = StringIO()

    def loader():
        raise SystemExit("toml broken")

    code = run_watch(
        initial,
        interval=600,
        no_color=True,
        stdout=StringIO(),
        stderr=err,
        key_reader=Reader(),
        sleep=lambda _s: None,
        require_tty=False,
        config_loader=loader,
    )

    assert code == 0
    assert instances[0].config is initial
    assert err.getvalue().count("config reload failed") == 1


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


def test_cli_watch_once_reapplies_cli_overrides_when_it_reloads(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[collectors.cswap]\nenabled = true\n")
    seen: list[dict] = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda config: seen.append(config) or _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.should_persist_snapshots", lambda _c: False)
    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    assert cli.main(["watch", "--config", str(path), "--no-cswap", "--once", "-q", "--no-color"]) == 0
    assert seen and seen[0]["collectors"]["cswap"]["enabled"] is False


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


def test_watch_frame_does_not_reuse_a_snapshot_from_a_different_disable_list(monkeypatch):
    """The failure that dropped agy: a young snapshot still listed it as disabled."""
    from aiuse.analysis.history import save_snapshot

    stale = _snap()
    stale.disabled_services = {"antigravity": "operator 2026-10-08: purposefully disabled"}
    save_snapshot(stale, [])
    calls: list[int] = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: calls.append(1) or _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600)
    assert calls == [1]


def test_watch_frame_does_not_reuse_a_snapshot_with_a_different_policy_fingerprint(monkeypatch):
    from aiuse.analysis.history import save_snapshot
    from aiuse.config import collection_policy_fingerprint

    stale = _snap()
    stale.config_fingerprint = "deadbeefdeadbeef"
    save_snapshot(stale, [])
    config = {"analysis": {"persist_snapshots": True}}
    assert collection_policy_fingerprint(config) != "deadbeefdeadbeef"
    calls: list[int] = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: calls.append(1) or _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    collect_watch_frame(config, max_age=600)
    assert calls == [1]


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


def test_all_providers_config_enables_everything_and_keeps_the_original():
    original = {
        "disabled_services": {"grok": "burst limit"},
        "collectors": {
            "grok_billing": {"enabled": False},
            "cswap": False,
            "codexbar": {"providers": "codex", "timeout": 30},
        },
    }
    sweep = all_providers_config(original)
    assert "disabled_services" not in sweep
    assert sweep["collectors"]["grok_billing"]["enabled"] is True
    assert sweep["collectors"]["cswap"] is True
    assert sweep["collectors"]["codexbar"]["providers"] == "all"
    assert sweep["collectors"]["codexbar"]["timeout"] == 30
    # the live config object is never mutated
    assert original["disabled_services"] == {"grok": "burst limit"}
    assert original["collectors"]["grok_billing"]["enabled"] is False
    assert original["collectors"]["codexbar"]["providers"] == "codex"


def test_collect_watch_frame_persist_false_is_screen_only(monkeypatch):
    monkeypatch.setattr("aiuse.watch._frame_from_disk", lambda *a, **k: pytest.fail("must not read the disk cache"))
    monkeypatch.setattr("aiuse.watch.save_snapshot", lambda *a, **k: pytest.fail("must not write a snapshot"))
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.should_persist_snapshots", lambda _c: True)
    snapshot, alerts = collect_watch_frame({"analysis": {"persist_snapshots": True}}, max_age=600, persist=False)
    assert snapshot.accounts and alerts == []


def test_screen_only_sweep_collects_through_the_shared_pipeline(monkeypatch):
    """The docs promise the sweep's queries still pass the cross-process
    QueryGate. That holds only while collect_watch_frame(persist=False)
    collects through run_collectors — pin it so a future side path cannot
    silently bypass the vendors' rate-limit protection."""
    seen: list[dict] = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda cfg: seen.append(cfg) or _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    sweep_config = all_providers_config({"disabled_services": {"grok": "off"}})
    snapshot, _alerts = collect_watch_frame(sweep_config, max_age=600, persist=False)
    assert snapshot.accounts
    assert seen == [sweep_config], "the sweep must collect through run_collectors (QueryGate applies)"


def test_render_watch_board_marks_the_all_providers_view():
    text = render_watch_board(_snap(), [], color=False, quiet=True, all_providers=True)
    assert "ALL PROVIDERS · screen only" in text
    assert "u update now" in text and "a all providers" in text


def test_render_watch_board_shows_collecting_all():
    text = render_watch_board(None, [], collecting_all_for=9, color=False)
    assert "collecting all providers… (9s)" in text


class _LoopFakes:
    """Process-worker/Live doubles for key-handling loop tests."""

    def __init__(self, monkeypatch, hold_sweep=False, hold_u=False, hold_regular_polls=0):
        self.instances = []
        self.texts = []
        outer = self

        class FakeProcessWorker:
            def __init__(self, config=None, max_age=None, persist=None):
                self.config = config or {}
                self.max_age = max_age
                self.persist = persist
                self.starts = []
                self.pending = None
                self.polls = 0
                self.stop_calls = 0
                outer.instances.append(self)

            def start(self, config=None, max_age=None):
                if config is not None:
                    self.config = config
                self.starts.append({"config": config, "max_age": max_age})
                held = (hold_sweep and self.persist is False) or (hold_u and max_age == 0.0)
                self.pending = None if held else (_snap(), [], None)

            def poll(self):
                # hold_regular_polls: the regular worker's first N polls come
                # back empty, simulating a long-running collect that finishes.
                if self.persist is not False and self.polls < hold_regular_polls:
                    self.polls += 1
                    return None
                result = self.pending
                self.pending = None
                return result

            def stop(self):
                self.stop_calls += 1

        class FakeLive:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def update(self, *args, **_kwargs):
                outer.texts.append(str(args[0]))

        monkeypatch.setattr("aiuse.watch._WatchCollectionProcess", FakeProcessWorker)
        monkeypatch.setattr("rich.live.Live", FakeLive)

    @property
    def regular(self):
        return self.instances[0]

    @property
    def sweep(self):
        return self.instances[1]


class _KeySequence:
    def __init__(self, keys):
        self.keys = iter(keys)

    def read(self):
        return next(self.keys, "q")


def test_u_key_collects_immediately_bypassing_the_disk_snapshot(monkeypatch):
    fakes = _LoopFakes(monkeypatch)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "u", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    starts = fakes.regular.starts
    assert len(starts) == 2
    assert starts[0]["max_age"] is None  # scheduled refresh may reuse the disk snapshot
    assert starts[1]["max_age"] == 0.0  # `u` must not show the same cached frame again


def test_a_key_runs_a_screen_only_all_providers_sweep(monkeypatch):
    fakes = _LoopFakes(monkeypatch)
    config = {
        "disabled_services": {"grok": "burst limit"},
        "collectors": {"grok_billing": {"enabled": False}, "codexbar": {"providers": "codex"}},
    }
    code = run_watch(
        config,
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "a", None, "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    assert fakes.sweep.persist is False
    assert len(fakes.sweep.starts) == 1
    sweep_config = fakes.sweep.starts[0]["config"]
    assert "disabled_services" not in sweep_config
    assert sweep_config["collectors"]["grok_billing"]["enabled"] is True
    assert sweep_config["collectors"]["codexbar"]["providers"] == "all"
    assert any("ALL PROVIDERS · screen only" in text for text in fakes.texts)
    # the base config object was not mutated by the sweep
    assert config["disabled_services"] == {"grok": "burst limit"}


def test_a_key_is_ignored_while_a_sweep_is_still_running(monkeypatch):
    fakes = _LoopFakes(monkeypatch, hold_sweep=True)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence(["a", "a", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    assert len(fakes.sweep.starts) == 1, "a second `a` press must not restart an in-flight sweep"
    assert any("collecting all providers" in text for text in fakes.texts)


def test_u_within_90s_of_a_is_rolled_into_the_sweep(monkeypatch):
    fakes = _LoopFakes(monkeypatch, hold_sweep=True)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence(["a", "u", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    # Only the initial scheduled collect ran; the u was covered by the sweep.
    assert [start["max_age"] for start in fakes.regular.starts] == [None]
    assert len(fakes.sweep.starts) == 1
    assert any("rolled into the all-providers run" in text for text in fakes.texts)


def test_u_waits_for_a_long_running_sweep_beyond_90s(monkeypatch):
    fakes = _LoopFakes(monkeypatch, hold_sweep=True)
    clock = [0.0]

    def tick(_seconds):
        clock[0] += 30.0

    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence(["a", "u", "u", "u", "q"]),
        sleep=tick,
        now=lambda: clock[0],
        require_tty=False,
    )
    assert code == 0
    assert len(fakes.sweep.starts) == 1
    assert [start["max_age"] for start in fakes.regular.starts] == [None], "u never starts beside the sweep"
    texts = "\n".join(fakes.texts)
    assert "rolled into the all-providers run (30s in)" in texts
    assert "rolled into the all-providers run (60s in)" in texts
    assert "waiting for the all-providers run (90s in)" in texts


def test_u_shortly_after_a_finished_is_covered(monkeypatch):
    fakes = _LoopFakes(monkeypatch)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence(["a", None, "u", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    assert [start["max_age"] for start in fakes.regular.starts] == [None], "the sweep result already covers the u"
    assert any("covered by the all-providers run" in text for text in fakes.texts)


def test_manual_keys_share_a_two_minute_cooldown(monkeypatch):
    fakes = _LoopFakes(monkeypatch)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "u", "a", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    # scheduled collect, then the one allowed u; the a never fires.
    assert [start["max_age"] for start in fakes.regular.starts] == [None, 0.0]
    assert fakes.sweep.starts == []
    assert any("ready in" in text and "2min minimum" in text for text in fakes.texts)


def test_a_queues_behind_a_long_running_u(monkeypatch):
    fakes = _LoopFakes(monkeypatch, hold_u=True)
    clock = [0.0]

    def tick(_seconds):
        clock[0] += 65.0

    # A u that outlasts the 2-minute cooldown (held): an a past its cooldown
    # may not overlap it — it queues until the u update finishes.
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "u", None, "a", "q"]),
        sleep=tick,
        now=lambda: clock[0],
        require_tty=False,
    )
    assert code == 0
    assert fakes.sweep.starts == [], "a must not start while a u-triggered update is collecting"
    assert any("queued behind the u update" in text for text in fakes.texts)


def test_a_queued_behind_a_scheduled_refresh_fires_when_it_finishes(monkeypatch):
    fakes = _LoopFakes(monkeypatch, hold_regular_polls=2)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "a", None, None, None, "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    assert any("queued behind the scheduled refresh" in text for text in fakes.texts)
    # One scheduled collect, then the released sweep — never overlapping.
    assert len(fakes.regular.starts) == 1
    assert len(fakes.sweep.starts) == 1
    assert any("ALL PROVIDERS · screen only" in text for text in fakes.texts)


def test_u_waits_for_a_queued_sweep(monkeypatch):
    fakes = _LoopFakes(monkeypatch, hold_regular_polls=2)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "a", "u", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    assert any("u: waiting for the queued all-providers run" in text for text in fakes.texts)
    # The queued u never forced a regular collect beside the sweep.
    assert [start["max_age"] for start in fakes.regular.starts] == [None]
    assert len(fakes.sweep.starts) == 1


def test_second_u_within_two_minutes_is_throttled(monkeypatch):
    fakes = _LoopFakes(monkeypatch)
    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "u", None, "u", "q"]),
        sleep=lambda _s: None,
        require_tty=False,
    )
    assert code == 0
    assert [start["max_age"] for start in fakes.regular.starts] == [None, 0.0]
    assert any("u: manual refresh ready in" in text for text in fakes.texts)


def test_manual_cooldown_expires_after_two_minutes(monkeypatch):
    fakes = _LoopFakes(monkeypatch)
    clock = [0.0]

    def tick(_seconds):
        clock[0] += 65.0

    code = run_watch(
        {"collectors": {}},
        interval=600,
        no_color=True,
        stdout=StringIO(),
        key_reader=_KeySequence([None, "u", None, "u", "q"]),
        sleep=tick,
        now=lambda: clock[0],
        require_tty=False,
    )
    assert code == 0
    assert [start["max_age"] for start in fakes.regular.starts] == [None, 0.0, 0.0]


def test_cli_watch_all_providers_once(monkeypatch, capsys):
    seen: list[dict] = []
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda config: seen.append(config) or _snap())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.save_snapshot", lambda *a, **k: pytest.fail("must not record the sweep"))
    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    assert cli.main(["watch", "--all-providers", "-q", "--no-color"]) == 0
    captured = capsys.readouterr()
    assert "ALL PROVIDERS · screen only" in captured.out
    assert "Operator-only" in captured.err
    assert seen and "disabled_services" not in seen[0]


def test_cli_watch_all_providers_rejects_json(monkeypatch):
    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    assert cli.main(["watch", "--all-providers", "--json"]) == 2
