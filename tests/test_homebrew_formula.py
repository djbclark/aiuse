"""The module delegates to Homebrew only once that install has the caam collector."""

from __future__ import annotations

import os
from pathlib import Path

from aiuse import homebrew_formula as hf


def _formula(root: Path, *, with_caam: bool, command: str = "aiuse") -> Path:
    binary = root / "bin" / command
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    if with_caam:
        module = root / "libexec/lib/python3.14/site-packages/aiuse/collectors"
        module.mkdir(parents=True)
        (module / "caam.py").write_text("# collector\n", encoding="utf-8")
    return binary


def test_formula_command_missing_when_collector_absent(tmp_path: Path):
    prefix = tmp_path / "opt" / "aiuse"
    _formula(prefix, with_caam=False)
    assert hf.formula_command(str(tmp_path / "aiuse"), (prefix,)) is None


def test_formula_command_present_when_collector_installed(tmp_path: Path):
    prefix = tmp_path / "opt" / "aiuse"
    binary = _formula(prefix, with_caam=True)
    assert hf.formula_command("/Users/someone/.local/bin/aiuse", (prefix,)) == binary


def test_formula_command_ignores_other_entrypoints(tmp_path: Path):
    prefix = tmp_path / "opt" / "aiuse"
    _formula(prefix, with_caam=True)
    assert hf.formula_command("python", (prefix,)) is None


def test_status_line_reports_each_state(tmp_path: Path):
    missing = tmp_path / "missing"
    assert hf.formula_status_line((missing,)) == "Homebrew formula: not installed"

    bare = tmp_path / "bare"
    _formula(bare, with_caam=False)
    assert "no caam collector" in hf.formula_status_line((bare,))

    full = tmp_path / "full"
    _formula(full, with_caam=True)
    assert "includes the caam collector" in hf.formula_status_line((full,))


def test_prefer_skips_under_pytest(monkeypatch, tmp_path: Path):
    prefix = tmp_path / "opt" / "aiuse"
    _formula(prefix, with_caam=True)
    monkeypatch.setattr(hf, "FORMULA_PREFIXES", (prefix,))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_homebrew_formula.py::test")
    called: list[list[str]] = []
    monkeypatch.setattr(hf.os, "execv", lambda *_a: called.append(["exec"]))
    hf.prefer_homebrew_formula()
    assert called == []


def test_prefer_execs_formula_when_not_under_pytest(monkeypatch, tmp_path: Path):
    prefix = tmp_path / "opt" / "aiuse"
    binary = _formula(prefix, with_caam=True)
    monkeypatch.setattr(hf, "FORMULA_PREFIXES", (prefix,))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("AIUSE_SKIP_HOMEBREW_FORMULA", raising=False)
    monkeypatch.setattr(hf.sys, "argv", [str(tmp_path / "pipx" / "aiuse"), "--version"])
    seen: list[tuple[str, list[str]]] = []

    def fake_exec(path: str, argv: list[str]) -> None:
        seen.append((path, argv))

    monkeypatch.setattr(hf.os, "execv", fake_exec)
    hf.prefer_homebrew_formula()
    assert seen == [(os.fspath(binary), [os.fspath(binary), "--version"])]


def test_prefer_stays_when_already_the_formula(monkeypatch, tmp_path: Path):
    prefix = tmp_path / "opt" / "aiuse"
    binary = _formula(prefix, with_caam=True)
    monkeypatch.setattr(hf, "FORMULA_PREFIXES", (prefix,))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(hf.sys, "argv", [str(binary), "--version"])
    monkeypatch.setattr(hf.os, "execv", lambda *_a: (_ for _ in ()).throw(AssertionError("exec")))
    hf.prefer_homebrew_formula()


def test_reexec_target_when_module_file_deleted_or_cellar_upgraded(monkeypatch, tmp_path: Path):
    cellar_old = tmp_path / "Cellar" / "aiuse" / "3.7.2"
    cellar_new = tmp_path / "Cellar" / "aiuse" / "3.8.0"
    old_mod = cellar_old / "libexec/lib/python3.14/site-packages/aiuse/watch.py"
    old_mod.parent.mkdir(parents=True)
    old_mod.write_text("# old\n", encoding="utf-8")
    new_bin = _formula(cellar_new, with_caam=True)

    opt_dir = tmp_path / "opt"
    opt_dir.mkdir(parents=True)
    prefix = opt_dir / "aiuse"
    prefix.symlink_to(cellar_new)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("AIUSE_SKIP_HOMEBREW_FORMULA", raising=False)

    # Old Cellar still exists on disk, but opt/aiuse points to 3.8.0
    assert hf.reexec_target(old_mod, argv0="aiuse", prefixes=(prefix,)) == prefix / "bin" / "aiuse"

    # Old Cellar was unlinked by brew upgrade
    old_mod.unlink()
    assert hf.reexec_target(old_mod, argv0="aiuse", prefixes=(prefix,)) == prefix / "bin" / "aiuse"

    # Running from the current 3.8.0 Cellar does not re-exec
    new_mod = cellar_new / "libexec/lib/python3.14/site-packages/aiuse/collectors/caam.py"
    assert hf.reexec_target(new_mod, argv0=str(new_bin), prefixes=(prefix,)) is None
