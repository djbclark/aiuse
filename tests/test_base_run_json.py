"""Tests for collectors.base.run_json recovery when stdout is not clean JSON."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from aiuse.collectors.base import CollectorError, probe_output, run_json


def _fake_run(stdout: str, *, returncode: int = 0):
    def _run(argv, **kwargs):  # noqa: ARG001
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return _run


def test_clean_json_object(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run('{"a": 1}'))
    assert run_json(["tool"]) == {"a": 1}


def test_clean_json_array(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run("[1, 2]"))
    assert run_json(["tool"]) == [1, 2]


def test_banner_then_payload_prefers_full_array_not_bracketed_number(monkeypatch):
    # Bug: first candidate was `[1]` from "Fetched [1] provider".
    stdout = 'Fetched [1] provider\n[{"a": 1}]'
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run(stdout))
    assert run_json(["tool"]) == [{"a": 1}]


def test_concatenated_json_prefers_larger_trailing_payload(monkeypatch):
    stdout = '{"warning": "x"}\n[{"a": 1}]'
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run(stdout))
    assert run_json(["tool"]) == [{"a": 1}]


def test_banner_then_single_json_value_still_works(monkeypatch):
    stdout = 'note: ok\n{"ok": true}'
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run(stdout))
    assert run_json(["tool"]) == {"ok": True}


def test_trailing_noise_after_json_keeps_longest_value(monkeypatch):
    # raw_decode stops at end of value; remaining non-JSON text is ok if we
    # pick the longest successful decode.
    stdout = '[{"a": 1}]\ntrailing junk'
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run(stdout))
    assert run_json(["tool"]) == [{"a": 1}]


def test_banner_noise_only_raises(monkeypatch):
    # No `{`/`[` that form a complete value — must not invent a payload.
    monkeypatch.setattr(
        "aiuse.collectors.base.subprocess.run",
        _fake_run("Fetched 1 provider\nnot json at all { unclosed"),
    )
    with pytest.raises(CollectorError, match="invalid JSON|no JSON"):
        run_json(["tool"])


def test_empty_stdout_raises(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _fake_run(""))
    with pytest.raises(CollectorError, match="no JSON"):
        run_json(["tool"])


def test_json_loads_path_still_used_for_clean_output(monkeypatch):
    """Ensure we still take the fast path (no need to exercise candidates)."""
    payload = {"nested": [1, 2, {"x": True}]}
    monkeypatch.setattr(
        "aiuse.collectors.base.subprocess.run",
        _fake_run(json.dumps(payload)),
    )
    assert run_json(["tool"]) == payload


def test_run_json_passes_stdin_devnull(monkeypatch):
    """Collectors must not inherit the TTY (caut raw-mode / echo-off bug)."""
    import subprocess

    seen: dict[str, object] = {}

    def _run(argv, **kwargs):  # noqa: ARG001
        seen.update(kwargs)
        return SimpleNamespace(stdout='{"ok": true}', stderr="", returncode=0)

    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _run)
    assert run_json(["tool"]) == {"ok": True}
    assert seen.get("stdin") is subprocess.DEVNULL


def test_run_json_detaches_children_from_the_terminal(monkeypatch):
    """No stdin and a new session: a child cannot open /dev/tty and leave it raw."""
    seen: dict[str, object] = {}

    def _run(argv, **kwargs):  # noqa: ARG001
        seen.update(kwargs)
        return SimpleNamespace(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _run)
    assert run_json(["tool"]) == {}
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["start_new_session"] is True


def test_probe_output_is_detached_and_swallows_failure(monkeypatch):
    seen: dict[str, object] = {}

    def _run(argv, **kwargs):  # noqa: ARG001
        seen.update(kwargs)
        return SimpleNamespace(stdout="out ", stderr="err", returncode=1)

    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _run)
    assert probe_output(["tool", "--help"]) == "out err"
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["start_new_session"] is True

    def _boom(argv, **kwargs):  # noqa: ARG001
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr("aiuse.collectors.base.subprocess.run", _boom)
    assert probe_output(["tool", "--help"]) == ""
