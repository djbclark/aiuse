"""Tests for orchestration/gh-issues-to-beads.sh (aiuse-juk.1, US-001).

The importer talks to gh and bd; both are replaced by stubs on PATH so the
tests never touch GitHub or a real beads database.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "orchestration" / "gh-issues-to-beads.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")

FAKE_BD = """#!/usr/bin/env bash
{ printf '%s' "$*" | tr '\\n' '|'; echo; } >> "$BD_LOG"
for a in "$@"; do
  case "$a" in
    list) cat "$FAKE_BD_LIST"; exit 0 ;;
    create) echo aiuse-new; exit 0 ;;
    close) exit 0 ;;
  esac
done
exit 0
"""

ISSUES = [
    {
        "number": 16,
        "title": "[est. 4–12h · ~0.3–1M tok · ~$3–20] Add a second DeepSeek prepaid-balance source",
        "state": "OPEN",
        "labels": [{"name": "enhancement"}],
        "body": "Body of 16",
        "url": "https://github.com/djbclark/aiuse/issues/16",
    },
    {
        "number": 14,
        "title": "[est. 4–12h] Optional: aiuse watch pull-refresh (not a menubar)",
        "state": "OPEN",
        "labels": [{"name": "enhancement"}],
        "body": "Body of 14",
        "url": "https://github.com/djbclark/aiuse/issues/14",
    },
    {
        "number": 17,
        "title": "[est. 4–12h] Add a second OpenRouter account-credit source",
        "state": "CLOSED",
        "labels": [{"name": "enhancement"}],
        "body": "Body of 17",
        "url": "https://github.com/djbclark/aiuse/issues/17",
    },
    {
        "number": 9,
        "title": "Closed and not requested",
        "state": "CLOSED",
        "labels": [{"name": "bug"}],
        "body": "",
        "url": "https://github.com/djbclark/aiuse/issues/9",
    },
]


def _run(
    tmp_path: Path, *args: str, existing: list[dict] | None = None
) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    bd = bin_dir / "bd"
    bd.write_text(FAKE_BD, encoding="utf-8")
    bd.chmod(0o755)
    issues = tmp_path / "issues.json"
    issues.write_text(json.dumps(ISSUES), encoding="utf-8")
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps(existing or []), encoding="utf-8")
    log = tmp_path / "bd.log"
    log.write_text("", encoding="utf-8")
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_ISSUES_JSON": str(issues),
        "FAKE_BD_LIST": str(listing),
        "BD_LOG": str(log),
        "BD_DIR": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    return result, log.read_text(encoding="utf-8").splitlines()


def test_dry_run_is_default_and_writes_nothing(tmp_path):
    result, calls = _run(tmp_path, "--issue", "17")
    assert result.returncode == 0, result.stderr
    assert all(" create " not in f" {c} " and " close " not in f" {c} " for c in calls), calls
    assert "dry run, nothing written" in result.stdout
    assert "summary: 3 to create, 0 already present" in result.stdout
    assert "#9" not in result.stdout  # closed and not requested


def test_mapping_strips_estimate_and_sets_priority(tmp_path):
    result, _ = _run(tmp_path)
    assert "create #16 P2 feature (OPEN): Add a second DeepSeek prepaid-balance source" in result.stdout
    assert "create #14 P3 feature (OPEN): Optional: aiuse watch pull-refresh (not a menubar)" in result.stdout
    assert "--external-ref gh-16" in result.stdout


def test_existing_refs_are_skipped(tmp_path):
    result, _ = _run(tmp_path, existing=[{"id": "aiuse-x", "external_ref": "gh-16"}])
    assert result.returncode == 0, result.stderr
    assert "skip   #16 (gh-16 already in beads)" in result.stdout
    assert "summary: 1 to create, 1 already present" in result.stdout


def test_apply_creates_and_closes_closed_issue(tmp_path):
    result, calls = _run(tmp_path, "--apply", "--issue", "17")
    assert result.returncode == 0, result.stderr
    creates = [c for c in calls if " create " in f" {c} "]
    closes = [c for c in calls if " close " in f" {c} "]
    assert len(creates) == 3
    assert any("--external-ref gh-17" in c for c in creates)
    assert closes == [
        f"-C {tmp_path} close aiuse-new --reason Closed on GitHub as issue #17 before import; mirrored for cross-reference."
    ]
    assert any("Estimate: 4–12h · ~0.3–1M tok · ~$3–20" in c for c in creates)


def test_unknown_requested_issue_fails(tmp_path):
    result, _ = _run(tmp_path, "--issue", "999")
    assert result.returncode == 1
    assert "not found on GitHub: 999" in result.stderr
