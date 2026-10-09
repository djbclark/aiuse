"""Tests for terminal attribute save/restore helpers."""

from __future__ import annotations

import pytest

from aiuse import tty
from aiuse.collectors import runner
from aiuse.tty import restore_stdin_tty, save_stdin_tty


class _Stream:
    def __init__(self, *, is_tty: bool, fd: int) -> None:
        self._is_tty = is_tty
        self._fd = fd

    def isatty(self) -> bool:
        return self._is_tty

    def fileno(self) -> int:
        return self._fd


def _streams(monkeypatch, *, stdin: bool, stdout: bool, stderr: bool) -> None:
    monkeypatch.setattr(tty.sys, "stdin", _Stream(is_tty=stdin, fd=0))
    monkeypatch.setattr(tty.sys, "stdout", _Stream(is_tty=stdout, fd=1))
    monkeypatch.setattr(tty.sys, "stderr", _Stream(is_tty=stderr, fd=2))


def test_save_restore_noop_when_not_tty(monkeypatch):
    _streams(monkeypatch, stdin=False, stdout=False, stderr=False)
    assert save_stdin_tty() is None
    restore_stdin_tty(None)  # must not raise


def test_restore_ignores_corrupt_saved(monkeypatch):
    # Passing an invalid snapshot must not raise.
    restore_stdin_tty(["not", "real", "termios"])
    restore_stdin_tty((999999, ["nope"]))


def test_save_uses_stdout_when_stdin_is_redirected(monkeypatch):
    """``aiuse </dev/null`` still has a terminal to protect on stdout."""
    import termios

    _streams(monkeypatch, stdin=False, stdout=True, stderr=True)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["attrs", fd])
    assert save_stdin_tty() == (1, ["attrs", 1])


def test_restore_targets_the_saved_fd(monkeypatch):
    import termios

    calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["raw"])
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, attrs: calls.append((fd, when, attrs)))
    monkeypatch.setattr(tty, "_owns_terminal", lambda fd: True)
    restore_stdin_tty((2, ["attrs"]))
    assert calls == [(2, termios.TCSADRAIN, ["attrs"])]


def test_restore_is_a_noop_when_nothing_changed(monkeypatch):
    """No tcsetattr at all in the common case (no SIGTTOU risk, no drain)."""
    import termios

    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["attrs"])
    monkeypatch.setattr(termios, "tcsetattr", lambda *_a: pytest.fail("tcsetattr on unchanged attrs"))
    monkeypatch.setattr(tty, "_owns_terminal", lambda fd: pytest.fail("checked pgrp on unchanged attrs"))
    restore_stdin_tty((2, ["attrs"]))


def test_restore_never_writes_from_a_background_job(monkeypatch):
    """``aiuse --version &`` with stderr on the tty: tcsetattr would SIGTTOU-stop us.

    Regression: 3.3.1's ``brew test`` hung for five minutes in state T.
    """
    import termios

    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["raw"])
    monkeypatch.setattr(termios, "tcsetattr", lambda *_a: pytest.fail("tcsetattr from a background process group"))
    monkeypatch.setattr(tty.os, "tcgetpgrp", lambda fd: 4242)
    monkeypatch.setattr(tty.os, "getpgrp", lambda: 1)
    restore_stdin_tty((2, ["attrs"]))


def test_owns_terminal_matches_foreground_pgrp(monkeypatch):
    monkeypatch.setattr(tty.os, "tcgetpgrp", lambda fd: 7)
    monkeypatch.setattr(tty.os, "getpgrp", lambda: 7)
    assert tty._owns_terminal(2)
    monkeypatch.setattr(tty.os, "tcgetpgrp", lambda fd: (_ for _ in ()).throw(OSError("not a tty")))
    assert not tty._owns_terminal(2)


def test_run_collectors_restores_the_terminal_before_returning(monkeypatch):
    """A child that leaves the terminal raw must not corrupt the report printed next."""
    events: list[str] = []
    monkeypatch.setattr(runner, "save_stdin_tty", lambda: events.append("save") or "token")
    monkeypatch.setattr(runner, "restore_stdin_tty", lambda saved: events.append(f"restore:{saved}"))

    def fake_inner(config):
        events.append("collect")
        return "snapshot"

    monkeypatch.setattr(runner, "_run_collectors", fake_inner)
    assert runner.run_collectors({}) == "snapshot"
    assert events == ["save", "collect", "restore:token"]

    def failing_inner(config):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_collectors", failing_inner)
    events.clear()
    try:
        runner.run_collectors({})
    except RuntimeError:
        pass
    assert events == ["save", "restore:token"]
