"""Run the US-004 judge/gate mutation test against a real ralph binary.

orchestration/mutation-test.sh drives ralph-orchestrator with a stub agent in
throwaway repos and exits 0 only when the judge blocks a lying agent, passes
an honest one, and the quota gate blocks an over-threshold window. It needs a
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

pytestmark = pytest.mark.skipif(
    not RALPH_BIN or not os.access(RALPH_BIN, os.X_OK) or not all(shutil.which(t) for t in ("jq", "git", "bd")),
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
    assert transcript.count("--- verdict: AS EXPECTED") == 3, transcript
    assert "JUDGE REFUSE: test command './check.sh' exited 1" in transcript
    assert "JUDGE PASS: tracker+git+tests agree" in transcript
    assert "Lifecycle hook 'cswap-gate' blocked orchestration at 'pre.iteration.start'" in transcript
