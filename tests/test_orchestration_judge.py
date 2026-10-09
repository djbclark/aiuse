"""Tests for orchestration/judge.sh (aiuse-juk.2, US-002).

Each test builds a throwaway git repo and a stub ``bd`` that prints a canned
``bd show --json`` record, then checks the judge's verdict and exit status.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JUDGE = ROOT / "orchestration" / "judge.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("git") is None, reason="jq and git required")


def _base_env() -> dict[str, str]:
    """The test environment minus BASH_ENV/ENV.

    A non-interactive bash sources $BASH_ENV, and a developer's rc file there
    can re-prepend directories to PATH, which would shadow the stubs below.
    """
    return {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV")}


GOOD_REASON = "implemented the feature and added tests"

FAKE_BD = """#!/usr/bin/env bash
case " $* " in
  *" show "*) cat "$FAKE_BD_SHOW"; exit "${FAKE_BD_RC:-0}" ;;
esac
exit 0
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_git_env())


def _git_env() -> dict[str, str]:
    return _base_env() | {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "work")
    started = _record_start(repo)
    assert started.returncode == 0, started.stderr
    return repo


def _commit_work(repo: Path) -> None:
    (repo / "feature.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "work")


def _judge(
    repo: Path,
    *,
    bead: object | None = None,
    expect_diff: str = "yes",
    test_cmd: str = "true",
    task_id: str | None = "aiuse-x1",
    bd_rc: int = 0,
    extra_env: dict[str, str] | None = None,
    payload: dict | None = None,
    judge: Path = JUDGE,
) -> subprocess.CompletedProcess:
    stub_dir = repo.parent / "bin"
    stub_dir.mkdir(exist_ok=True)
    bd = stub_dir / "bd"
    bd.write_text(FAKE_BD, encoding="utf-8")
    bd.chmod(0o755)
    if bead is None:
        bead = [{"id": "aiuse-x1", "status": "closed", "close_reason": GOOD_REASON}]
    show = repo.parent / "show.json"
    show.write_text(json.dumps(bead), encoding="utf-8")
    env = _git_env() | {
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "FAKE_BD_SHOW": str(show),
        "FAKE_BD_RC": str(bd_rc),
        "EXPECT_DIFF": expect_diff,
        "TEST_CMD": test_cmd,
    }
    env.pop("TASK_ID", None)
    if task_id is not None:
        env["TASK_ID"] = task_id
    env |= extra_env or {}
    return subprocess.run(
        ["bash", str(judge)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        input=json.dumps(payload) if payload is not None else "",
    )


def _payload(loop_id: str, event: str = "pre.loop.complete") -> dict:
    """The JSON ralph v2.10.1 writes to a hook's stdin (trimmed)."""
    return {"event": event.removeprefix("pre."), "phase_event": event, "loop": {"id": loop_id}}


def _record_start(repo: Path, loop_id: str | None = None, judge: Path = JUDGE) -> subprocess.CompletedProcess:
    """What the pre.loop.start hook does: `judge.sh --record-start`."""
    payload = json.dumps(_payload(loop_id, "pre.loop.start")) if loop_id else ""
    result = subprocess.run(
        ["bash", str(judge), "--record-start"],
        cwd=repo,
        env=_git_env(),
        text=True,
        capture_output=True,
        check=False,
        input=payload,
    )
    return result


def _first_line(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).strip().splitlines()[0]


def test_pass_when_tracker_git_and_tests_agree(repo):
    _commit_work(repo)
    result = _judge(repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "JUDGE PASS: tracker+git+tests agree"
    log = (repo / ".ralph" / "judge" / "verdicts.log").read_text(encoding="utf-8")
    assert "JUDGE PASS" in log


def test_accepts_single_object_json(repo):
    _commit_work(repo)
    result = _judge(repo, bead={"id": "aiuse-x1", "status": "closed", "close_reason": GOOD_REASON})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("bead", "expected"),
    [
        ([{"status": "in_progress", "close_reason": GOOD_REASON}], "status is 'in_progress', not 'closed'"),
        ([{"status": "closed", "close_reason": "done"}], "close reason is 4 chars"),
        ([{"status": "closed"}], "close reason is 0 chars"),
        ([{"status": "closed", "close_reason": "    padded but short     "}], "close reason is 16 chars"),
        ([], "returned no issue object"),
    ],
)
def test_refuses_on_tracker(repo, bead, expected):
    _commit_work(repo)
    result = _judge(repo, bead=bead)
    assert result.returncode == 1
    assert _first_line(result).startswith("JUDGE REFUSE: ")
    assert expected in result.stderr


def test_refuses_when_bd_fails(repo):
    _commit_work(repo)
    result = _judge(repo, bd_rc=1)
    assert result.returncode == 1
    assert "JUDGE REFUSE: bd show aiuse-x1 failed" in result.stderr


def test_refuses_without_task_id(repo):
    result = _judge(repo, task_id=None)
    assert result.returncode == 1
    assert "JUDGE REFUSE: TASK_ID is not set" in result.stderr


def test_refuses_bad_expect_diff(repo):
    result = _judge(repo, expect_diff="maybe")
    assert result.returncode == 1
    assert "EXPECT_DIFF must be 'yes' or 'no'" in result.stderr


def test_refuses_code_task_without_commits(repo):
    result = _judge(repo)
    assert result.returncode == 1
    assert "no commits on HEAD beyond the loop start" in result.stderr


def test_refuses_dirty_tree(repo):
    _commit_work(repo)
    (repo / "stray.txt").write_text("uncommitted\n", encoding="utf-8")
    result = _judge(repo)
    assert result.returncode == 1
    assert "uncommitted changes in the work tree" in result.stderr
    assert "stray.txt" in result.stderr


def test_ralph_state_dir_does_not_count_as_dirty(repo):
    _commit_work(repo)
    (repo / ".ralph" / "agent").mkdir(parents=True)
    (repo / ".ralph" / "agent" / "scratchpad.md").write_text("notes\n", encoding="utf-8")
    result = _judge(repo)
    assert result.returncode == 0, result.stderr


def test_research_task_passes_without_commits(repo):
    result = _judge(repo, expect_diff="no")
    assert result.returncode == 0, result.stderr


def test_research_task_refuses_commits(repo):
    _commit_work(repo)
    result = _judge(repo, expect_diff="no")
    assert result.returncode == 1
    assert "1 commit(s) beyond the loop start" in result.stderr
    assert "on a task that expects no diff" in result.stderr


def test_runs_the_real_test_command_and_refuses_on_failure(repo):
    _commit_work(repo)
    marker = repo.parent / "ran"
    result = _judge(repo, test_cmd=f"echo tests: pass; touch {marker}; exit 3")
    assert marker.exists(), "judge must execute the test command itself"
    assert result.returncode == 1
    first = _first_line(result)
    assert first.startswith("JUDGE REFUSE: test command ") and "exited 3" in first
    assert "tests: pass" in result.stderr  # shown as log tail, never trusted


def test_verdict_line_comes_first_even_with_noisy_tests(repo):
    _commit_work(repo)
    result = _judge(repo, test_cmd="for i in $(seq 1 5000); do echo noise $i; done; exit 1")
    assert result.returncode == 1
    assert result.stderr.startswith("JUDGE REFUSE: ")


def _commit_files(
    repo: Path, files: dict[str, str] | None = None, delete: tuple[str, ...] = (), msg: str = "change"
) -> None:
    for name, text in (files or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    for name in delete:
        _git(repo, "rm", "-q", name)
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def guarded_repo(tmp_path: Path) -> Path:
    """A repo whose base already has the files the judge relies on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit_files(
        repo,
        {
            "README": "base\n",
            "justfile": "check:\n    pytest\n",
            "pyproject.toml": "[tool.pytest.ini_options]\n",
            "orchestration/judge.sh": "#!/usr/bin/env bash\n",
            "tests/conftest.py": "",
            "tests/test_core.py": "def test_core():\n    assert True\n",
        },
        msg="base",
    )
    _git(repo, "checkout", "-q", "-b", "work")
    return repo


# Review 2, finding 5c: the agent can edit what the judge relies on (the check
# recipe, pytest config, the judge itself, skipped or deleted tests) and still
# get JUDGE PASS. Such work must be refused unless the operator allows it.
@pytest.mark.parametrize(
    ("files", "delete", "expected"),
    [
        ({"justfile": "check:\n    true\n"}, (), "justfile"),
        ({"orchestration/judge.sh": "#!/usr/bin/env bash\nexit 0\n"}, (), "orchestration/judge.sh"),
        ({"pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-k nothing'\n"}, (), "pyproject.toml"),
        ({"tests/conftest.py": "collect_ignore = ['test_core.py']\n"}, (), "tests/conftest.py"),
        ({"src/sub/conftest.py": "x = 1\n"}, (), "src/sub/conftest.py"),
        ({}, ("tests/test_core.py",), "deleted test file tests/test_core.py"),
        (
            {"tests/test_core.py": "import pytest\n\n\n@pytest.mark.skip\ndef test_core():\n    assert False\n"},
            (),
            "@pytest.mark.skip",
        ),
        (
            {"tests/test_core.py": "import pytest\n\n\ndef test_core():\n    pytest.skip('later')\n"},
            (),
            "pytest.skip(",
        ),
    ],
)
def test_refuses_changes_to_what_the_judge_relies_on(guarded_repo, files, delete, expected):
    _commit_files(guarded_repo, {"feature.txt": "work\n"}, msg="real work")
    _commit_files(guarded_repo, files, delete, msg="weaken the checks")
    result = _judge(guarded_repo, extra_env={"JUDGE_BASE": "main"})
    assert result.returncode == 1, result.stdout
    assert _first_line(result).startswith("JUDGE REFUSE: the work changes what the judge relies on")
    assert expected in result.stderr


def test_ordinary_work_in_a_guarded_repo_passes(guarded_repo):
    _commit_files(
        guarded_repo,
        {"src/feature.py": "X = 1\n", "tests/test_feature.py": "def test_x():\n    assert True\n"},
        msg="real work with a new test",
    )
    result = _judge(guarded_repo, extra_env={"JUDGE_BASE": "main"})
    assert result.returncode == 0, result.stderr


def test_operator_can_allow_protected_changes(guarded_repo):
    _commit_files(guarded_repo, {"pyproject.toml": "[project]\ndependencies = ['x']\n"}, msg="add a dependency")
    result = _judge(guarded_repo, extra_env={"JUDGE_BASE": "main", "JUDGE_ALLOW_PROTECTED": "yes"})
    assert result.returncode == 0, result.stderr
    log = (guarded_repo / ".ralph" / "judge" / "verdicts.log").read_text(encoding="utf-8")
    assert "protected changes allowed by JUDGE_ALLOW_PROTECTED: pyproject.toml" in log


def test_example_config_runs_pinned_copies_outside_the_clone():
    config = (ROOT / "orchestration" / "ralph.aiuse.example.yml").read_text(encoding="utf-8")
    assert "./orchestration/" not in config, "hooks must not run scripts the agent can edit"
    assert 'command: ["/Users/djbclark/.local/state/aiuse-ralph/pinned/judge.sh"]' in config
    assert 'command: ["/Users/djbclark/.local/state/aiuse-ralph/pinned/cswap-gate.sh"]' in config


# Review 2, finding 5d: the judge measured base..HEAD against origin/main, so a
# reused clone or branch with a leftover commit passed EXPECT_DIFF=yes with no
# new work from this loop. It now measures from the SHA recorded at
# pre.loop.start, and checks that the record belongs to this loop.
def test_record_start_writes_the_loop_start(repo):
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    result = _record_start(repo, "primary-1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"JUDGE START: loop primary-1 starts at {head}")
    record = json.loads((repo / ".ralph" / "judge" / "loop-start.json").read_text(encoding="utf-8"))
    assert record["sha"] == head
    assert record["loop_id"] == "primary-1"
    assert len(record["judge_hash"]) == 40


def test_leftover_commit_from_before_the_loop_is_not_this_loops_work(repo):
    _commit_work(repo)  # a leftover commit on a reused branch
    assert _record_start(repo, "primary-2").returncode == 0
    result = _judge(repo, payload=_payload("primary-2"))  # the loop itself committed nothing
    assert result.returncode == 1, result.stdout
    assert "no commits on HEAD beyond the loop start" in result.stderr


def test_new_commit_after_the_loop_start_passes(repo):
    _commit_work(repo)
    assert _record_start(repo, "primary-3").returncode == 0
    (repo / "more.txt").write_text("more\n", encoding="utf-8")
    _git(repo, "add", "more.txt")
    _git(repo, "commit", "-q", "-m", "this loop's work")
    result = _judge(repo, payload=_payload("primary-3"))
    assert result.returncode == 0, result.stderr


def test_start_record_from_another_loop_is_refused(repo):
    assert _record_start(repo, "primary-old").returncode == 0
    _commit_work(repo)
    result = _judge(repo, payload=_payload("primary-new"))
    assert result.returncode == 1
    assert "loop start record is for loop 'primary-old', not this loop 'primary-new'" in result.stderr


def test_no_start_record_and_no_judge_base_refuses(tmp_path):
    repo = tmp_path / "bare"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "work")
    _commit_work(repo)
    result = _judge(repo)
    assert result.returncode == 1
    assert "no loop start recorded" in result.stderr
    manual = _judge(repo, extra_env={"JUDGE_BASE": "main"})
    assert manual.returncode == 0, manual.stderr


def test_start_that_is_not_an_ancestor_of_head_refuses(repo):
    _commit_work(repo)
    assert _record_start(repo, "primary-4").returncode == 0
    _git(repo, "checkout", "-q", "-B", "work", "main")  # history rewritten under the loop
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-q", "-m", "other")
    result = _judge(repo, payload=_payload("primary-4"))
    assert result.returncode == 1
    assert "is not an ancestor of HEAD" in result.stderr


def test_judge_edited_since_the_loop_start_refuses(repo, tmp_path):
    pinned = tmp_path / "pinned-judge.sh"
    pinned.write_text(JUDGE.read_text(encoding="utf-8"), encoding="utf-8")
    assert _record_start(repo, "primary-5", judge=pinned).returncode == 0
    pinned.write_text(JUDGE.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    _commit_work(repo)
    result = _judge(repo, payload=_payload("primary-5"), judge=pinned)
    assert result.returncode == 1
    assert "judge.sh changed since the loop start" in result.stderr


def test_state_dir_can_live_outside_the_clone(repo, tmp_path):
    state = tmp_path / "state"
    started = subprocess.run(
        ["bash", str(JUDGE), "--record-start"],
        cwd=repo,
        env=_git_env() | {"JUDGE_STATE_DIR": str(state)},
        text=True,
        capture_output=True,
        check=False,
        input=json.dumps(_payload("primary-6", "pre.loop.start")),
    )
    assert started.returncode == 0, started.stderr
    assert (state / "loop-start.json").is_file()
    _commit_work(repo)
    result = _judge(repo, payload=_payload("primary-6"), extra_env={"JUDGE_STATE_DIR": str(state)})
    assert result.returncode == 0, result.stderr
