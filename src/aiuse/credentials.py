"""Interactive credential refresh helpers.

These commands are intentionally separate from normal collection.  They read a
single, user-selected browser profile only when explicitly requested, validate
the credential in memory, and then hand it to SecretSpec without printing it.
"""

from __future__ import annotations

import argparse
import errno
import os
import pty
import select
import shutil
import subprocess
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path

from aiuse.collectors.opencode_zen import (
    _BILLING_SERVER_ID,
    _WORKSPACES_SERVER_ID,
    _fetch_server,
    _first_workspace,
    _parse_billing_balance,
)
from aiuse.secretspec import default_manifest_path, ensure_manifest

_OPENCODE_ZEN = "opencode-zen"
_OPENCODE_HOST = "opencode.ai"
_OPENCODE_COOKIE_SECRET = "OPENCODE_ZEN_COOKIE"
_MUSE = "muse"
_MUSE_HOSTS = ("dev.meta.ai", "meta.ai", "facebook.com", "auth.meta.com")
_MUSE_COOKIE_SECRET = "MUSE_COOKIE"
_CHROME_ROOT = Path.home() / "Library/Application Support/Google/Chrome"
_DEFAULT_TIMEOUT_S = 10.0


class CredentialError(Exception):
    """An expected credential-refresh failure that is safe to display."""


def run_credential_command(argv: list[str]) -> int:
    """Dispatch ``aiuse credential`` without mixing it into collection flags."""
    parser = argparse.ArgumentParser(
        prog="aiuse credential",
        description="Interactively validate and refresh a provider credential.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    refresh = subparsers.add_parser("refresh", help="read and validate a browser credential before saving it")
    refresh.add_argument("provider", choices=(_OPENCODE_ZEN, _MUSE))
    refresh.add_argument("--from", dest="source", choices=("chrome",), default="chrome")
    refresh.add_argument("--profile", default="Default", help="Chrome profile directory (default: Default)")
    refresh.add_argument("--dry-run", action="store_true", help="validate only; do not replace SecretSpec")
    refresh.add_argument("--yes", action="store_true", help="replace SecretSpec without confirmation")
    refresh.add_argument(
        "--secretspec-file",
        type=Path,
        default=default_manifest_path(),
        metavar="PATH",
        help="SecretSpec manifest to update (default: ~/.config/aiuse/secretspec.toml)",
    )
    refresh.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_S, metavar="SECONDS")
    args = parser.parse_args(argv)
    if args.action != "refresh":  # pragma: no cover - argparse makes this unreachable
        return 2
    if args.provider == _MUSE:
        return _refresh_muse(args)
    return _refresh_opencode_zen(args)


def _refresh_opencode_zen(args: argparse.Namespace) -> int:
    try:
        if args.timeout <= 0:
            raise CredentialError("timeout must be positive")
        cookie = _chrome_cookie_header(str(args.profile))
        _validate_opencode_zen_cookie(cookie, timeout=float(args.timeout))
    except CredentialError as exc:
        print(f"credential refresh failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Validated OpenCode Zen browser session; SecretSpec was not changed.")
        return 0
    if not args.yes:
        answer = input("Validated OpenCode Zen session. Replace OPENCODE_ZEN_COOKIE in SecretSpec? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("SecretSpec was not changed.")
            return 0
    try:
        manifest = Path(args.secretspec_file).expanduser()
        ensure_manifest(manifest)
        _save_with_secretspec(cookie, manifest=manifest, timeout=float(args.timeout))
    except CredentialError as exc:
        print(f"credential refresh failed: {exc}", file=sys.stderr)
        return 1
    print("Validated OpenCode Zen browser session and saved it to SecretSpec.")
    return 0


def _chrome_cookie_header(profile: str) -> str:
    """Load only cookies that apply to OpenCode from one Chrome profile."""
    if not profile or Path(profile).name != profile or profile in {".", ".."}:
        raise CredentialError("Chrome profile must be a profile directory name, such as Default or Profile 1")
    try:
        import browser_cookie3
    except ImportError as exc:
        raise CredentialError(
            "browser-cookie3 is not installed; install the optional chrome-refresh extra first"
        ) from exc
    cookie_file = _chrome_cookie_file(profile)
    if not cookie_file.is_file():
        raise CredentialError(f"Chrome cookie database not found for profile {profile!r}")
    try:
        jar = browser_cookie3.chrome(cookie_file=str(cookie_file), domain_name=_OPENCODE_HOST)
    except Exception as exc:  # library/provider error; its text can include local paths but no cookie values
        raise CredentialError(f"could not read the selected Chrome profile ({exc.__class__.__name__})") from exc
    return _cookie_header_for_opencode(jar)


def _chrome_cookie_file(profile: str) -> Path:
    profile_dir = _CHROME_ROOT / profile
    for candidate in (profile_dir / "Network/Cookies", profile_dir / "Cookies"):
        if candidate.is_file():
            return candidate
    return profile_dir / "Network/Cookies"


def _cookie_header_for_opencode(jar: CookieJar) -> str:
    pairs: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in jar:
        domain = item.domain.lstrip(".").lower()
        if domain != _OPENCODE_HOST and not domain.endswith(f".{_OPENCODE_HOST}"):
            continue
        value = item.value or ""
        if any(char in value for char in "\r\n;") or any(char in item.name for char in "\r\n;="):
            continue
        pair = (item.name, value)
        if pair not in seen:
            seen.add(pair)
            pairs.append(f"{item.name}={value}")
    if not pairs:
        raise CredentialError("no OpenCode cookies were found; sign in to OpenCode in the selected Chrome profile")
    return "; ".join(pairs)


def _validate_opencode_zen_cookie(cookie: str, *, timeout: float) -> None:
    """Prove the session reaches the same live billing route that collection uses."""
    workspace = _first_workspace(_fetch_server(_WORKSPACES_SERVER_ID, None, cookie, timeout))
    if workspace is None:
        raise CredentialError("OpenCode did not return an authenticated workspace")
    raw = _fetch_server(_BILLING_SERVER_ID, [workspace], cookie, timeout)
    if _parse_billing_balance(raw) is None:
        raise CredentialError("OpenCode did not return a Zen balance for this session")


def _refresh_muse(args: argparse.Namespace) -> int:
    try:
        if args.timeout <= 0:
            raise CredentialError("timeout must be positive")
        cookie = _chrome_cookie_header_for_muse(str(args.profile))
        _validate_muse_cookie(cookie, timeout=float(args.timeout))
    except CredentialError as exc:
        print(f"credential refresh failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Validated Muse browser session; SecretSpec was not changed.")
        return 0
    if not args.yes:
        answer = input("Validated Muse session. Replace MUSE_COOKIE in SecretSpec? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("SecretSpec was not changed.")
            return 0
    try:
        manifest = Path(args.secretspec_file).expanduser()
        ensure_manifest(manifest)
        _save_with_secretspec_for_muse(cookie, manifest=manifest, timeout=float(args.timeout))
    except CredentialError as exc:
        print(f"credential refresh failed: {exc}", file=sys.stderr)
        return 1
    print("Validated Muse browser session and saved it to SecretSpec.")
    return 0


def _chrome_cookie_header_for_muse(profile: str) -> str:
    if not profile or Path(profile).name != profile or profile in {".", ".."}:
        raise CredentialError("Chrome profile must be a profile directory name, such as Default or Profile 1")
    try:
        import browser_cookie3
    except ImportError as exc:
        raise CredentialError(
            "browser-cookie3 is not installed; install the optional chrome-refresh extra first"
        ) from exc
    cookie_file = _chrome_cookie_file(profile)
    if not cookie_file.is_file():
        raise CredentialError(f"Chrome cookie database not found for profile {profile!r}")
    # Read all muse-related hosts and merge, like the collector does
    pairs: list[str] = []
    seen: set[tuple[str, str]] = set()
    for host in _MUSE_HOSTS:
        try:
            jar = browser_cookie3.chrome(cookie_file=str(cookie_file), domain_name=host)
        except Exception as exc:
            raise CredentialError(f"could not read the selected Chrome profile ({exc.__class__.__name__})") from exc
        for item in jar:
            domain = item.domain.lstrip(".").lower()
            # Keep only muse/meta/facebook cookies that the collector would send
            if not any(domain == h or domain.endswith(f".{h}") for h in _MUSE_HOSTS):
                continue
            value = item.value or ""
            if any(char in value for char in "\r\n;") or any(char in item.name for char in "\r\n;="):
                continue
            pair = (item.name, value)
            if pair not in seen:
                seen.add(pair)
                pairs.append(f"{item.name}={value}")
    if not pairs:
        raise CredentialError("no Muse cookies were found; sign in to dev.meta.ai in the selected Chrome profile")
    return "; ".join(pairs)


def _validate_muse_cookie(cookie: str, *, timeout: float) -> None:
    """Prove the Muse cookie reaches the billing GraphQL route."""
    # Import lazily to avoid circular import at module load
    from aiuse.collectors.muse import _collect_via_cookie

    # Try cookie collection with a permissive env that may include team_id override
    env: dict[str, str] = {}
    # If collection can find team_id via HTML scrape it will succeed; otherwise it will
    # raise with actionable hint about AIUSE_MUSE_TEAM_ID
    try:
        accounts = _collect_via_cookie(cookie, env, timeout)
    except Exception as exc:
        raise CredentialError(str(exc)) from exc
    if not accounts:
        raise CredentialError("Muse did not return billing info for this session")
    # Basic sanity: must have balance
    if accounts[0].balance_usd is None and accounts[0].usage_credits is None:
        raise CredentialError("Muse did not return a balance for this session")


def _save_with_secretspec_for_muse(secret: str, *, manifest: Path, timeout: float) -> None:
    executable = shutil.which("secretspec")
    if executable is None:
        raise CredentialError("secretspec is not on PATH")
    if not manifest.is_file():
        raise CredentialError(f"SecretSpec manifest does not exist: {manifest}")
    if os.name == "nt":
        raise CredentialError("interactive SecretSpec saving is not supported on Windows")
    master, slave = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                executable,
                "set",
                "--file",
                str(manifest),
                "--reason",
                "aiuse validated Muse browser session",
                _MUSE_COOKIE_SECRET,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise CredentialError(f"could not start SecretSpec ({exc.__class__.__name__})") from exc
    finally:
        os.close(slave)
    sent = False
    prompt = b""
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.terminate()
                raise CredentialError("SecretSpec did not finish before the timeout")
            ready, _, _ = select.select([master], [], [], min(remaining, 0.25))
            if not ready:
                continue
            try:
                chunk = os.read(master, 1024)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    continue
                raise CredentialError(f"could not communicate with SecretSpec ({exc.__class__.__name__})") from exc
            prompt = (prompt + chunk)[-4096:]
            if not sent and b"Enter value" in prompt:
                os.write(master, secret.encode() + b"\n")
                sent = True
                prompt = b""
        if process.returncode != 0 or not sent:
            raise CredentialError("SecretSpec did not save the validated browser session")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        os.close(master)


def _save_with_secretspec(secret: str, *, manifest: Path, timeout: float) -> None:
    """Use SecretSpec's hidden prompt so ``secret`` is never an argv value."""
    executable = shutil.which("secretspec")
    if executable is None:
        raise CredentialError("secretspec is not on PATH")
    if not manifest.is_file():
        raise CredentialError(f"SecretSpec manifest does not exist: {manifest}")
    if os.name == "nt":  # pty is unavailable there; this feature currently targets Chrome on macOS/POSIX.
        raise CredentialError("interactive SecretSpec saving is not supported on Windows")
    master, slave = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                executable,
                "set",
                "--file",
                str(manifest),
                "--reason",
                "aiuse validated OpenCode Zen browser session",
                _OPENCODE_COOKIE_SECRET,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise CredentialError(f"could not start SecretSpec ({exc.__class__.__name__})") from exc
    finally:
        os.close(slave)

    sent = False
    prompt = b""
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.terminate()
                raise CredentialError("SecretSpec did not finish before the timeout")
            ready, _, _ = select.select([master], [], [], min(remaining, 0.25))
            if not ready:
                continue
            try:
                chunk = os.read(master, 1024)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    continue
                raise CredentialError(f"could not communicate with SecretSpec ({exc.__class__.__name__})") from exc
            prompt = (prompt + chunk)[-4096:]
            if not sent and b"Enter value" in prompt:
                os.write(master, secret.encode() + b"\n")
                sent = True
                prompt = b""
        if process.returncode != 0 or not sent:
            raise CredentialError("SecretSpec did not save the validated browser session")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        os.close(master)
