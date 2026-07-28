"""Unit tests for packaging/release.py helpers (no network / no git push)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PY = ROOT / "packaging" / "release.py"


def _load_release():
    spec = importlib.util.spec_from_file_location("aiuse_release", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aiuse_release"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def release():
    return _load_release()


def test_validate_version(release):
    release._validate_version("2.1.16")
    with pytest.raises(release.ReleaseError):
        release._validate_version("v2.1.16")
    with pytest.raises(release.ReleaseError):
        release._validate_version("not-a-version")


def test_rewrite_project_version(tmp_path: Path, release):
    (tmp_path / "src" / "aiuse").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('name = "aiuse"\nversion = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "src" / "aiuse" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text('name = "aiuse"\nversion = "1.0.0"\n', encoding="utf-8")

    release._rewrite_project_version(tmp_path, "9.9.9")
    assert 'version = "9.9.9"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "9.9.9"' in (tmp_path / "src" / "aiuse" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert 'version = "9.9.9"' in (tmp_path / "uv.lock").read_text(encoding="utf-8")


def test_rewrite_homebrew_formula(tmp_path: Path, release):
    formula = tmp_path / "aiuse.rb"
    formula.write_text(
        'url "https://github.com/djbclark/aiuse/archive/refs/tags/v1.0.0.tar.gz"\n'
        'sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        encoding="utf-8",
    )
    release._rewrite_homebrew_formula(formula, "2.3.4", "b" * 64)
    text = formula.read_text(encoding="utf-8")
    assert "v2.3.4.tar.gz" in text
    assert ("b" * 64) in text


def test_find_publish_run_matches_tag(monkeypatch, release):
    import json

    payload = [
        {
            "databaseId": 1,
            "status": "completed",
            "conclusion": "success",
            "headBranch": "v2.1.15",
            "displayTitle": "aiuse 2.1.15",
            "event": "release",
        },
        {
            "databaseId": 2,
            "status": "in_progress",
            "conclusion": None,
            "headBranch": "v2.1.16",
            "displayTitle": "aiuse 2.1.16",
            "event": "release",
        },
    ]

    def fake_run(argv, **kwargs):  # noqa: ARG001
        from subprocess import CompletedProcess

        if argv[:3] == ["gh", "run", "list"]:
            return CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(release, "_run", fake_run)
    run = release._find_publish_run("2.1.16")
    assert run is not None
    assert run["databaseId"] == 2
    assert release._find_publish_run("9.9.9") is None
