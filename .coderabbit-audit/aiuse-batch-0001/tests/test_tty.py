"""Tests for stdin TTY save/restore helpers."""

from __future__ import annotations

from aiuse.tty import restore_stdin_tty, save_stdin_tty


def test_save_restore_noop_when_not_tty(monkeypatch):
    class _Fake:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("aiuse.tty.sys.stdin", _Fake())
    assert save_stdin_tty() is None
    restore_stdin_tty(None)  # must not raise


def test_restore_ignores_corrupt_saved(monkeypatch):
    # Passing an invalid snapshot must not raise.
    restore_stdin_tty(["not", "real", "termios"])
