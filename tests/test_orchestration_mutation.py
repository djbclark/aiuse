"""Run the US-004 judge/gate mutation test against a real ralph binary.

orchestration/mutation-test.sh drives ralph-orchestrator with a stub agent in
throwaway repos and exits 0 only when the judge blocks a lying agent, passes
an honest one, blocks when the judge hook times out, and the quota gate
blocks an over-threshold window. It needs a
pinned ralph binary, so this test runs only when RALPH_BIN is set.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "orchestration" / "mutation-test.sh"

RALPH_BIN = os.environ.get("RALPH_BIN", "")

HAVE_TOOLS = all(shutil.which(t) for t in ("jq", "git", "bd"))


@pytest.mark.skipif(
    not RALPH_BIN or not os.access(RALPH_BIN, os.X_OK) or not HAVE_TOOLS,
    reason="set RALPH_BIN to a ralph v2.10.1 binary (and have jq, git, bd) to run the mutation test",
)
def test_judge_and_gate_mutation(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV")}
    env["TMPDIR"] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=1500
    )
    transcript = result.stdout + result.stderr
    assert result.returncode == 0, transcript
    assert transcript.count("--- verdict: AS EXPECTED") == 5, transcript
    assert "JUDGE REFUSE: test command './check.sh' exited 1" in transcript
    assert "JUDGE PASS: tracker+git+tests agree" in transcript
    assert "Lifecycle hook 'cswap-gate' blocked orchestration at 'pre.iteration.start'" in transcript
    # Review 2, 5d: a leftover commit from before the loop is not this loop's work.
    assert "JUDGE REFUSE: no commits on HEAD beyond the loop start" in transcript
    # Review 2, 5e: a judge hook that overruns timeout_seconds blocks, not continues.
    assert "Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete': hook timed out" in transcript


# Review 2, finding 5m: an unchecked `mktemp -d` left the scratch path empty,
# so the rigs pointed at /<scenario> and cleanup ran `rm -rf ""`.
@pytest.mark.skipif(not HAVE_TOOLS, reason="jq, git and bd are checked before the scratch directory")
def test_stops_when_the_scratch_directory_cannot_be_made(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV")}
    env |= {"TMPDIR": str(tmp_path / "missing"), "RALPH_BIN": shutil.which("true") or "/usr/bin/true"}
    result = subprocess.run(
        ["bash", str(SCRIPT), "lying"], cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=60
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "cannot create a scratch directory" in result.stderr
    assert "scenario:" not in result.stdout
