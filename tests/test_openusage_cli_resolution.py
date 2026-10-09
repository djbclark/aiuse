"""The OpenUsage.ai collector must run OpenUsage.app's CLI, never a same-named binary.

Two products ship an ``openusage`` binary: OpenUsage.app's helper prints JSON
and exits; openusage.sh's Go dashboard opens a TUI on /dev/tty, hangs until the
collector timeout and leaves the terminal in raw mode when killed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from aiuse.collectors import base, openusage

APP_HELP = "Usage: openusage [provider] [--force]\n\nRead limits through OpenUsage's shared five-minute cache and exit. Output is always JSON.\n"
SH_HELP = (
    "OpenUsage is a terminal dashboard for monitoring AI coding tool usage and spend.\n\nUsage:\n  openusage [flags]\n"
)


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture(autouse=True)
def _fresh_verdicts():
    openusage.clear_cli_verdicts()
    yield
    openusage.clear_cli_verdicts()


def _bundle_cli(tmp_path: Path) -> Path:
    return _executable(tmp_path / "OpenUsage.app" / "Contents" / "Helpers" / "openusage")


def test_which_all_walks_every_path_entry_in_order_and_dedupes(tmp_path, monkeypatch):
    first = _executable(tmp_path / "a" / "tool")
    second = _executable(tmp_path / "b" / "tool")
    link_dir = tmp_path / "c"
    link_dir.mkdir()
    (link_dir / "tool").symlink_to(first)  # same file again via a symlink
    _executable(tmp_path / "d" / "other")  # different name, ignored
    monkeypatch.setenv(
        "PATH", os.pathsep.join(str(d) for d in (tmp_path / "a", tmp_path / "b", link_dir, tmp_path / "d"))
    )

    assert base.which_all("tool") == [str(first), str(second)]
    assert base.which_all("tool", extra_paths=(str(second), str(tmp_path / "missing"))) == [str(first), str(second)]


def test_first_tool_returns_first_accepted_and_the_rejected_before_it(tmp_path, monkeypatch):
    bad = _executable(tmp_path / "a" / "tool")
    good = _executable(tmp_path / "b" / "tool")
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")]))

    chosen, rejected = base.first_tool("tool", lambda p: p == str(good))
    assert chosen == str(good)
    assert rejected == [str(bad)]

    chosen, rejected = base.first_tool("tool", lambda _p: False)
    assert chosen is None
    assert rejected == [str(bad), str(good)]


def test_bundle_symlink_is_accepted_without_a_probe(tmp_path, monkeypatch):
    helper = _bundle_cli(tmp_path)
    link = tmp_path / "bin" / "openusage"
    link.parent.mkdir()
    link.symlink_to(helper)
    monkeypatch.setattr(openusage, "probe_output", lambda *_a, **_k: pytest.fail("probed a bundle path"))

    assert openusage.is_app_cli(str(link))


def test_foreign_binary_is_rejected_by_its_help_banner(tmp_path, monkeypatch):
    foreign = _executable(tmp_path / "homebrew" / "openusage")
    probes: list[list[str]] = []

    def fake_probe(argv, *, timeout):
        probes.append(argv)
        return SH_HELP

    monkeypatch.setattr(openusage, "probe_output", fake_probe)
    assert not openusage.is_app_cli(str(foreign))
    assert not openusage.is_app_cli(str(foreign))  # cached: no second probe
    assert probes == [[str(foreign), "--help"]]


def test_app_cli_anywhere_is_accepted_by_its_help_banner(tmp_path, monkeypatch):
    relocated = _executable(tmp_path / "elsewhere" / "openusage")
    monkeypatch.setattr(openusage, "probe_output", lambda *_a, **_k: APP_HELP)
    assert openusage.is_app_cli(str(relocated))


def test_resolver_skips_the_shadowing_binary_and_uses_the_app_cli_later_on_path(tmp_path, monkeypatch):
    foreign = _executable(tmp_path / "homebrew" / "openusage")
    helper = _bundle_cli(tmp_path)
    link = tmp_path / "usr-local" / "openusage"
    link.parent.mkdir()
    link.symlink_to(helper)
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "homebrew"), str(link.parent)]))
    monkeypatch.setattr(openusage, "_KNOWN_APP_CLI_PATHS", ())
    monkeypatch.setattr(openusage, "probe_output", lambda *_a, **_k: SH_HELP)

    assert openusage.resolve_app_cli() == (str(link), [str(foreign)])
    assert openusage.app_cli_path() == str(link)
    assert openusage.foreign_openusage_binaries() == [str(foreign)]


def test_resolver_falls_back_to_known_install_locations(tmp_path, monkeypatch):
    foreign = _executable(tmp_path / "homebrew" / "openusage")
    helper = _bundle_cli(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "homebrew"))
    monkeypatch.setattr(openusage, "_KNOWN_APP_CLI_PATHS", (str(helper),))
    monkeypatch.setattr(openusage, "probe_output", lambda *_a, **_k: SH_HELP)

    assert openusage.resolve_app_cli() == (str(helper), [str(foreign)])


def test_resolver_reports_no_cli_when_only_foreign_binaries_exist(tmp_path, monkeypatch):
    foreign = _executable(tmp_path / "homebrew" / "openusage")
    monkeypatch.setenv("PATH", str(tmp_path / "homebrew"))
    monkeypatch.setattr(openusage, "_KNOWN_APP_CLI_PATHS", ())
    monkeypatch.setattr(openusage, "probe_output", lambda *_a, **_k: SH_HELP)

    assert openusage.resolve_app_cli() == (None, [str(foreign)])


def test_fetch_limits_never_runs_a_foreign_binary(monkeypatch):
    monkeypatch.setattr(openusage, "app_cli_path", lambda: None)
    monkeypatch.setattr(openusage, "run_json", lambda *_a, **_k: pytest.fail("ran a CLI that is not OpenUsage.app's"))
    payload = {"providers": {}}
    monkeypatch.setattr(openusage, "_http_limits", lambda *, base_url, timeout: payload)

    got, via = openusage._fetch_limits(
        timeout=5.0, base_url="http://127.0.0.1:6736", force_refresh=True, try_launch_app=False
    )
    assert (got, via) == (payload, "http")


def test_fetch_limits_runs_the_verified_app_cli(monkeypatch):
    calls: list[list[str]] = []
    payload = {"providers": {}}

    def fake_run_json(argv, *, timeout):
        calls.append(argv)
        return payload

    monkeypatch.setattr(openusage, "app_cli_path", lambda: "/Applications/OpenUsage.app/Contents/Helpers/openusage")
    monkeypatch.setattr(openusage, "run_json", fake_run_json)

    got, via = openusage._fetch_limits(
        timeout=5.0, base_url="http://127.0.0.1:6736", force_refresh=True, try_launch_app=False
    )
    assert (got, via) == (payload, "cli")
    assert calls == [["/Applications/OpenUsage.app/Contents/Helpers/openusage", "--force"]]
