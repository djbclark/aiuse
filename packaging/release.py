#!/usr/bin/env python3
"""Cut a full aiuse release: version bump → tag → GitHub Release → Homebrew.

Preferred operator / agent entrypoint::

    just release 2.1.16
    just release 2.1.16 --dry-run
    just release 2.1.16 --notes-file /tmp/notes.md

Implements the maintainer flow in docs/packaging.md (OIDC PyPI via GitHub
Release + in-repo Homebrew formula + optional tap sync).
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_PY = REPO_ROOT / "src" / "aiuse" / "__init__.py"
UV_LOCK = REPO_ROOT / "uv.lock"
PACKAGING_DOC = REPO_ROOT / "docs" / "packaging.md"
HOMEBREW_FORMULA = REPO_ROOT / "packaging" / "homebrew" / "aiuse.rb"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[a-zA-Z0-9.-]*)?$")
DEFAULT_TAP = Path.home() / "src" / "homebrew-aiuse"


class ReleaseError(SystemExit):
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    display = " ".join(argv)
    if dry_run:
        _log(f"[dry-run] {display}")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    _log(f"+ {display}")
    return subprocess.run(
        argv,
        cwd=cwd or REPO_ROOT,
        check=check,
        text=True,
        capture_output=capture,
        env=env,
    )


def _git(*args: str, capture: bool = True, check: bool = True, dry_run: bool = False) -> str:
    proc = _run(["git", *args], capture=capture, check=check, dry_run=dry_run)
    return (proc.stdout or "").strip() if capture else ""


def _current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise ReleaseError("could not find version in pyproject.toml")
    return match.group(1)


def _validate_version(version: str) -> None:
    if not VERSION_RE.match(version):
        raise ReleaseError(f"invalid version {version!r} (want X.Y.Z)")


def _ensure_clean_tree(*, allow_dirty: bool) -> None:
    status = _git("status", "--porcelain")
    if status and not allow_dirty:
        raise ReleaseError("working tree is dirty; commit/stash first, or pass --allow-dirty\n" + status)


def _ensure_on_main() -> None:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch not in {"main", "master"}:
        raise ReleaseError(f"refuse to release from branch {branch!r} (want main)")


def _rewrite_project_version(root: Path, version: str) -> None:
    """Rewrite version strings in pyproject / __init__ / uv.lock under ``root``."""
    pyproject = root / "pyproject.toml"
    init_py = root / "src" / "aiuse" / "__init__.py"
    uv_lock = root / "uv.lock"

    py = pyproject.read_text(encoding="utf-8")
    py_new, n = re.subn(
        r'(?m)^version\s*=\s*"[^"]+"',
        f'version = "{version}"',
        py,
        count=1,
    )
    if n != 1:
        raise ReleaseError("failed to rewrite pyproject.toml version")
    pyproject.write_text(py_new, encoding="utf-8")

    init = init_py.read_text(encoding="utf-8")
    init_new, n = re.subn(
        r'(?m)^__version__\s*=\s*"[^"]+"',
        f'__version__ = "{version}"',
        init,
        count=1,
    )
    if n != 1:
        raise ReleaseError("failed to rewrite src/aiuse/__init__.py __version__")
    init_py.write_text(init_new, encoding="utf-8")

    if uv_lock.is_file():
        lock = uv_lock.read_text(encoding="utf-8")
        lock_new, n = re.subn(
            r'(?m)^(name = "aiuse"\nversion = ")[^"]+(")',
            rf"\g<1>{version}\g<2>",
            lock,
            count=1,
        )
        if n != 1:
            raise ReleaseError("failed to rewrite uv.lock project version")
        uv_lock.write_text(lock_new, encoding="utf-8")


def _rewrite_packaging_version(root: Path, version: str) -> None:
    """Keep the published-version note in release documentation accurate."""
    path = root / "docs" / "packaging.md"
    text = path.read_text(encoding="utf-8")
    updated, n = re.subn(
        r"(?m)^by GitHub Actions via Trusted Publishing\. Current published release: \*\*`[^`]+`\*\*\.$",
        f"by GitHub Actions via Trusted Publishing. Current published release: **`{version}`**.",
        text,
        count=1,
    )
    if n != 1:
        raise ReleaseError("failed to rewrite docs/packaging.md current published release")
    path.write_text(updated, encoding="utf-8")


def _rewrite_homebrew_formula(path: Path, version: str, sha256: str) -> None:
    text = path.read_text(encoding="utf-8")
    text, n1 = re.subn(
        r'url "https://github.com/djbclark/aiuse/archive/refs/tags/v[^"]+\.tar\.gz"',
        f'url "https://github.com/djbclark/aiuse/archive/refs/tags/v{version}.tar.gz"',
        text,
        count=1,
    )
    text, n2 = re.subn(
        r'sha256 "[0-9a-f]+"',
        f'sha256 "{sha256}"',
        text,
        count=1,
    )
    if n1 != 1 or n2 != 1:
        raise ReleaseError(f"failed to rewrite {path}")
    path.write_text(text, encoding="utf-8")


def _bump_version(version: str, *, dry_run: bool) -> None:
    if dry_run:
        _log(f"[dry-run] bump version → {version}")
        return
    _rewrite_project_version(REPO_ROOT, version)
    _rewrite_packaging_version(REPO_ROOT, version)


def _run_tests(*, skip_tests: bool, dry_run: bool) -> None:
    if skip_tests:
        _log("skipping tests (--skip-tests)")
        return
    pytest = REPO_ROOT / ".venv" / "bin" / "python"
    if pytest.is_file():
        _run([str(pytest), "-m", "pytest", "-q"], dry_run=dry_run)
    else:
        _run(["uv", "run", "--extra", "dev", "pytest", "-q"], dry_run=dry_run)


def _commit_version(version: str, *, dry_run: bool) -> None:
    files = ["pyproject.toml", "src/aiuse/__init__.py", str(PACKAGING_DOC.relative_to(REPO_ROOT))]
    if UV_LOCK.is_file():
        files.append("uv.lock")
    _run(["git", "add", *files], dry_run=dry_run)
    # Skip if nothing staged (already bumped).
    if not dry_run:
        staged = _git("diff", "--cached", "--name-only")
        if not staged:
            _log("version files already committed; skipping bump commit")
            return
    msg = f"Bump version to {version}"
    _run(["git", "commit", "-m", msg], dry_run=dry_run)


def _push_main(*, dry_run: bool) -> None:
    _run(["git", "push", "origin", "HEAD"], dry_run=dry_run)


def _create_tag(version: str, *, dry_run: bool) -> str:
    tag = f"v{version}"
    existing = _git("tag", "-l", tag)
    if existing:
        _log(f"tag {tag} already exists locally")
    else:
        _run(["git", "tag", "-a", tag, "-m", f"aiuse {version}"], dry_run=dry_run)
    _run(["git", "push", "origin", tag], dry_run=dry_run)
    return tag


def _default_notes(version: str) -> str:
    prev = _git("describe", "--tags", "--abbrev=0", "HEAD^", check=False)
    range_spec = f"{prev}..HEAD" if prev else "HEAD"
    log = _git("log", "--pretty=format:- %s", range_spec, check=False)
    body = log or "- (no commits listed)"
    return (
        f"## aiuse {version}\n\n"
        f"{body}\n\n"
        "### Install / upgrade\n\n"
        "```bash\n"
        "pipx upgrade aiuse\n"
        "# or\n"
        "brew upgrade aiuse   # after tap formula update\n"
        "```\n\n"
        "External data sources still separate: `./packaging/install-deps.sh`\n"
    )


def _build_and_release(
    version: str,
    tag: str,
    *,
    notes: str,
    dry_run: bool,
) -> None:
    dist = REPO_ROOT / "dist"
    if not dry_run:
        if dist.exists():
            shutil.rmtree(dist)
        dist.mkdir(parents=True, exist_ok=True)
    _run(
        ["uv", "run", "--with", "build", "python", "-m", "build"],
        dry_run=dry_run,
    )
    # Existing release? update notes/assets carefully.
    view = _run(
        ["gh", "release", "view", tag],
        check=False,
        capture=True,
        dry_run=dry_run,
    )
    if dry_run:
        _log(f"[dry-run] gh release create {tag} dist/* …")
        return
    assets = sorted(dist.glob("*"))
    if view.returncode == 0:
        _log(f"GitHub release {tag} already exists; uploading assets if needed")
        if assets:
            _run(["gh", "release", "upload", tag, *[str(p) for p in assets], "--clobber"])
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(notes)
        notes_path = fh.name
    try:
        _run(
            [
                "gh",
                "release",
                "create",
                tag,
                *[str(p) for p in assets],
                "--title",
                f"aiuse {version}",
                "--notes-file",
                notes_path,
            ]
        )
    finally:
        os.unlink(notes_path)


@contextmanager
def _open_release_https_url(url: str, *, timeout: int) -> Iterator[http.client.HTTPResponse]:
    """Open a fixed release endpoint after rejecting unsafe schemes and hosts."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "pypi.org"}:
        raise ReleaseError(f"refuse non-release URL: {url!r}")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        port=parsed.port,
        timeout=timeout,
        context=ssl.create_default_context(),  # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
    )
    try:
        connection.request("GET", target)
        with connection.getresponse() as response:
            yield response
    finally:
        connection.close()


def _pypi_has_version(version: str) -> bool:
    url = f"https://pypi.org/pypi/aiuse/{version}/json"
    try:
        with _open_release_https_url(url, timeout=15) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _find_publish_run(version: str) -> dict[str, object] | None:
    """Return the publish.yml run for tag ``v{version}``, if listed yet."""
    import json

    tag = f"v{version}"
    runs = _run(
        [
            "gh",
            "run",
            "list",
            "--workflow=publish.yml",
            "-L",
            "20",
            "--json",
            "databaseId,status,conclusion,headBranch,displayTitle,event",
        ],
        capture=True,
        check=False,
    )
    if runs.returncode != 0 or not (runs.stdout or "").strip():
        return None
    for run in json.loads(runs.stdout):
        if run.get("headBranch") == tag:
            return run
        title = str(run.get("displayTitle") or "")
        if version in title and tag in title:
            return run
    return None


def _wait_for_pypi(version: str, *, timeout_s: int, dry_run: bool) -> None:
    if dry_run:
        _log(f"[dry-run] wait for PyPI aiuse=={version}")
        return
    _log(f"waiting for publish.yml / PyPI for {version}…")
    # Must match this version's tag run — never accept a prior release's success.
    deadline = time.time() + timeout_s
    watched: str | None = None
    while time.time() < deadline:
        if _pypi_has_version(version):
            _log(f"PyPI has aiuse {version}")
            return

        run = _find_publish_run(version)
        if run is not None:
            rid = str(run["databaseId"])
            status = run.get("status")
            conclusion = run.get("conclusion")
            _log(f"publish run {rid} (v{version}): status={status} conclusion={conclusion}")
            if status == "completed":
                if conclusion != "success":
                    raise ReleaseError(f"publish.yml failed for v{version}: {conclusion}")
                # Workflow green — keep polling PyPI until the file is visible.
            elif rid != watched:
                watched = rid
                _run(["gh", "run", "watch", rid, "--exit-status"], check=False)
                continue
        time.sleep(5)

    if _pypi_has_version(version):
        _log(f"PyPI has aiuse {version}")
        return
    raise ReleaseError(f"timed out waiting for PyPI aiuse=={version}")


def _tarball_sha256(version: str, *, dry_run: bool) -> str:
    url = f"https://github.com/djbclark/aiuse/archive/refs/tags/v{version}.tar.gz"
    if dry_run:
        _log(f"[dry-run] fetch sha256 for {url}")
        return "0" * 64
    _log(f"hashing {url}")
    digest = hashlib.sha256()
    with _open_release_https_url(url, timeout=60) as resp:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _update_homebrew_formula(version: str, sha256: str, *, dry_run: bool) -> None:
    if dry_run:
        _log(f"[dry-run] update {HOMEBREW_FORMULA} → v{version} sha256={sha256[:12]}…")
        return
    _rewrite_homebrew_formula(HOMEBREW_FORMULA, version, sha256)
    _run(["git", "add", str(HOMEBREW_FORMULA.relative_to(REPO_ROOT))])
    staged = _git("diff", "--cached", "--name-only")
    if staged:
        _run(["git", "commit", "-m", f"Update Homebrew formula for v{version}"])
        _run(["git", "push", "origin", "HEAD"])


def _sync_tap(version: str, *, tap_path: Path, dry_run: bool) -> None:
    if not tap_path.is_dir():
        _log(f"tap path {tap_path} missing; skip Homebrew tap sync")
        return
    dest = tap_path / "Formula" / "aiuse.rb"
    if dry_run:
        _log(f"[dry-run] copy formula → {dest} and push tap")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOMEBREW_FORMULA, dest)
    _run(["git", "add", "Formula/aiuse.rb"], cwd=tap_path)
    status = _run(["git", "status", "--porcelain"], cwd=tap_path, capture=True)
    if not (status.stdout or "").strip():
        _log("tap already up to date")
        return
    _run(["git", "commit", "-m", f"aiuse {version}"], cwd=tap_path)
    _run(["git", "push", "origin", "HEAD"], cwd=tap_path)
    _log(f"updated tap {tap_path}")


def _upgrade_and_test_homebrew(version: str, *, dry_run: bool) -> None:
    """Refresh the published tap, upgrade this Mac, and test the new formula."""
    formula = "djbclark/aiuse/aiuse"
    # ``brew update`` can decide a tap is current without fetching its remote
    # (notably when Homebrew auto-updates are disabled).  Refresh the specific
    # just-pushed tap as well, so an upgrade cannot silently use its old formula.
    _run(["brew", "update", "--force"], dry_run=dry_run)
    tap = _run(["brew", "--repository", "djbclark/aiuse"], capture=True, dry_run=dry_run)
    tap_path = Path((tap.stdout or "").strip())
    if not dry_run:
        if not tap_path.is_dir():
            raise ReleaseError("Homebrew did not report the djbclark/aiuse tap checkout")
        _run(["git", "fetch", "origin", "main"], cwd=tap_path)
        _run(["git", "merge", "--ff-only", "origin/main"], cwd=tap_path)
        formula_path = tap_path / "Formula" / "aiuse.rb"
        if f"tags/v{version}.tar.gz" not in formula_path.read_text(encoding="utf-8"):
            raise ReleaseError(f"Homebrew tap formula is not aiuse {version}")
    _run(["brew", "upgrade", formula], dry_run=dry_run)
    prefix = _run(["brew", "--prefix", formula], capture=True, dry_run=dry_run)
    if not dry_run:
        installed = (prefix.stdout or "").strip()
        if not installed:
            raise ReleaseError(f"brew did not report an install prefix for {formula}")
        output = _run([str(Path(installed) / "bin" / "aiuse"), "--version"], capture=True)
        if f"aiuse {version}" not in (output.stdout or ""):
            raise ReleaseError(f"Homebrew install is not aiuse {version}: {(output.stdout or '').strip()}")
    _run(["brew", "test", formula], dry_run=dry_run)


def _upgrade_and_verify_default_path(version: str, *, dry_run: bool) -> None:
    """Upgrade the pipx copy that shadows Homebrew and verify normal commands."""
    _run(["pipx", "upgrade", "aiuse"], dry_run=dry_run)
    for command in ("aiuse", "ai"):
        output = _run([command, "--version"], capture=True, dry_run=dry_run)
        if not dry_run and f"aiuse {version}" not in (output.stdout or ""):
            raise ReleaseError(f"default-PATH {command} is not aiuse {version}: {(output.stdout or '').strip()}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("version", help="New version, e.g. 2.1.16 (no leading v)")
    p.add_argument("--dry-run", action="store_true", help="Print actions without changing anything")
    p.add_argument("--allow-dirty", action="store_true", help="Allow a dirty working tree")
    p.add_argument("--skip-tests", action="store_true", help="Skip pytest (not recommended)")
    p.add_argument("--skip-pypi-wait", action="store_true", help="Do not wait for OIDC publish")
    p.add_argument("--skip-homebrew", action="store_true", help="Skip formula + tap update")
    p.add_argument(
        "--notes-file",
        type=Path,
        help="Release notes Markdown file (default: git log since previous tag)",
    )
    p.add_argument(
        "--notes",
        help="Release notes Markdown string (overrides --notes-file)",
    )
    p.add_argument(
        "--tap-path",
        type=Path,
        default=DEFAULT_TAP,
        help=f"Local homebrew-aiuse clone (default: {DEFAULT_TAP})",
    )
    p.add_argument(
        "--pypi-timeout",
        type=int,
        default=600,
        help="Seconds to wait for PyPI/publish.yml (default: 600)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    version = args.version.lstrip("v")
    _validate_version(version)
    dry = bool(args.dry_run)

    if not dry:
        _ensure_on_main()
        _ensure_clean_tree(allow_dirty=bool(args.allow_dirty))

    current = _current_version()
    _log(f"current version: {current} → {version}")
    if current == version and not dry:
        _log("version already set; continuing with tag/release/homebrew steps")
    else:
        _bump_version(version, dry_run=dry)
        _run_tests(skip_tests=bool(args.skip_tests), dry_run=dry)
        _commit_version(version, dry_run=dry)
        _push_main(dry_run=dry)

    tag = _create_tag(version, dry_run=dry)

    if args.notes is not None:
        notes = args.notes
    elif args.notes_file is not None:
        notes = args.notes_file.read_text(encoding="utf-8")
    else:
        notes = _default_notes(version)

    _build_and_release(version, tag, notes=notes, dry_run=dry)

    if not args.skip_pypi_wait:
        _wait_for_pypi(version, timeout_s=int(args.pypi_timeout), dry_run=dry)

    if not args.skip_homebrew:
        sha = _tarball_sha256(version, dry_run=dry)
        _update_homebrew_formula(version, sha, dry_run=dry)
        _sync_tap(version, tap_path=args.tap_path, dry_run=dry)
        _upgrade_and_test_homebrew(version, dry_run=dry)
    _upgrade_and_verify_default_path(version, dry_run=dry)

    _log(f"done: aiuse {version} ({tag})")
    _log(f"  release: https://github.com/djbclark/aiuse/releases/tag/{tag}")
    _log(f"  pypi:    https://pypi.org/project/aiuse/{version}/")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"command failed ({exc.returncode}): {' '.join(exc.cmd)}") from exc
