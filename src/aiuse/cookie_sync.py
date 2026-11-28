"""Sync CodexBar web-session cookies from a Mac to a headless box.

CodexBar on Linux cannot read browser or Keychain sessions, so ``cursor`` and
``grok`` fail there with "needs a Cookie header" / "No available fetch
strategy".  CodexBar has no command that exports a resolved cookie, but it does
accept one with ``codexbar config set-cookie --provider P --stdin``.

Two halves, both secret-safe (cookie values are never printed, logged, put in
argv, or left in files after ingest):

``aiuse sync-cookies push``   (Mac)
    cursor  <- ``cursor-access-token`` from the login Keychain (written by
               ``cursor-agent``), wrapped as ``WorkosCursorSessionToken``.
    grok    <- grok.com cookies (``sso``/``sso-rw``) from a Chrome profile.
    Each payload is piped over ssh into a drop directory on the box.

``aiuse sync-cookies ingest``   (box)
    Applies every pending drop file with ``codexbar config set-cookie --stdin``
    and shreds it.  The CodexBar collector calls the same ingest first, so a
    pushed cookie lands on the next ``aiuse`` run without a daemon.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROVIDERS = ("cursor", "grok")
SCHEMA = 1
DEFAULT_DROP_DIR = "/var/lib/aiuse-cookie-drop"
_CHROME_ROOT = Path.home() / "Library/Application Support/Google/Chrome"
_CURSOR_KEYCHAIN_SERVICE = "cursor-access-token"
_HEADER_RE = re.compile(r"^[\x21-\x7e ]+$")  # printable ASCII, no CR/LF/tab
_MAX_HEADER = 16384
_SSH_TIMEOUT_S = 60.0


class CookieSyncError(Exception):
    """An expected failure whose text never contains a cookie value."""


# --------------------------------------------------------------------------- paths


def drop_dir() -> Path:
    return Path(os.environ.get("AIUSE_COOKIE_DROP", DEFAULT_DROP_DIR))


def status_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "aiuse" / "cookie-sync-status.json"


# --------------------------------------------------------------------------- helpers


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise CookieSyncError("token is not a JWT")
    seg = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(seg))
    except (ValueError, json.JSONDecodeError) as exc:
        raise CookieSyncError("token payload is not valid JSON") from exc
    if not isinstance(data, dict):
        raise CookieSyncError("token payload is not an object")
    return data


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cursor_cookie_from_token(token: str, *, now: float | None = None) -> tuple[str, float]:
    """Return ``(Cookie header, exp epoch)`` for a Cursor access-token JWT."""
    token = token.strip()
    claims = _jwt_payload(token)
    sub = claims.get("sub")
    exp = claims.get("exp")
    if not isinstance(sub, str) or not isinstance(exp, (int, float)):
        raise CookieSyncError("Cursor token lacks sub/exp claims")
    user_id = [p for p in sub.split("|") if p][-1] if sub else ""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user_id):
        raise CookieSyncError("Cursor token has an unexpected user id")
    if exp - (now if now is not None else time.time()) < 60:
        raise CookieSyncError("Cursor access token is expired; open cursor-agent or Cursor on the Mac to refresh it")
    return f"WorkosCursorSessionToken={user_id}%3A%3A{token}", float(exp)


def extract_cursor() -> dict[str, Any]:
    if sys.platform != "darwin":
        raise CookieSyncError("cursor extraction needs the macOS Keychain (run this on the Mac)")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv
            ["/usr/bin/security", "find-generic-password", "-s", _CURSOR_KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CookieSyncError("Keychain read timed out (an access prompt may be waiting on the Mac)") from exc
    token = proc.stdout.strip()
    if proc.returncode != 0 or not token:
        raise CookieSyncError("no cursor-access-token in the login Keychain; run `cursor-agent login` on the Mac")
    header, exp = cursor_cookie_from_token(token)
    return {"provider": "cursor", "cookie_header": header, "expires_at": _iso(exp), "source": "keychain:cursor-agent"}


def _chrome_profiles() -> list[str]:
    if not _CHROME_ROOT.is_dir():
        return []
    names = [
        p.name for p in _CHROME_ROOT.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))
    ]
    return sorted(names)


def _chrome_cookie_file(profile: str) -> Path | None:
    for rel in ("Network/Cookies", "Cookies"):
        f = _CHROME_ROOT / profile / rel
        if f.is_file():
            return f
    return None


def grok_header_from_jar(jar: Any) -> tuple[str, float | None] | None:
    """Join grok.com cookies; require ``sso``/``sso-rw``.  Returns (header, earliest sso expiry)."""
    pairs: list[str] = []
    seen: set[str] = set()
    names: set[str] = set()
    sso_exp: list[float] = []
    for item in jar:
        domain = (item.domain or "").lstrip(".").lower()
        if domain != "grok.com" and not domain.endswith(".grok.com"):
            continue
        value = item.value or ""
        if not value or any(c in value for c in "\r\n;") or any(c in item.name for c in "\r\n;="):
            continue
        if item.name in seen:
            continue
        seen.add(item.name)
        names.add(item.name)
        pairs.append(f"{item.name}={value}")
        if item.name in ("sso", "sso-rw") and item.expires:
            sso_exp.append(float(item.expires))
    if not ({"sso", "sso-rw"} & names):
        return None
    return "; ".join(pairs), (min(sso_exp) if sso_exp else None)


def extract_grok(profile: str | None = None) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise CookieSyncError("grok extraction reads Chrome on the Mac (run this on the Mac)")
    try:
        import browser_cookie3
    except ImportError as exc:
        raise CookieSyncError("browser-cookie3 is not installed; install the chrome-refresh extra") from exc
    candidates = [profile] if profile else _chrome_profiles()
    found: list[tuple[float, str, str, float | None]] = []
    for name in candidates:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise CookieSyncError("Chrome profile must be a directory name such as Default or 'Profile 1'")
        cookie_file = _chrome_cookie_file(name)
        if cookie_file is None:
            continue
        try:
            jar = browser_cookie3.chrome(cookie_file=str(cookie_file), domain_name="grok.com")
        except Exception as exc:  # noqa: BLE001 - library error text can include paths, never values
            raise CookieSyncError(f"could not read Chrome profile {name!r} ({exc.__class__.__name__})") from exc
        got = grok_header_from_jar(jar)
        if got:
            header, exp = got
            found.append((exp or 0.0, name, header, exp))
    if not found:
        raise CookieSyncError("no grok.com session in Chrome; sign in at https://grok.com in the intended profile")
    # Prefer an explicit profile; otherwise the longest-lived session.  Several
    # distinct sessions may be different accounts: pin one with --grok-profile.
    found.sort(key=lambda t: t[0], reverse=True)
    _, name, header, exp = found[0]
    return {"provider": "grok", "cookie_header": header, "expires_at": _iso(exp), "source": f"chrome:{name}"}


def validate_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise CookieSyncError("unsupported payload schema")
    if data.get("provider") not in PROVIDERS:
        raise CookieSyncError("unsupported provider")
    header = data.get("cookie_header")
    if not isinstance(header, str) or not header or len(header) > _MAX_HEADER or not _HEADER_RE.match(header):
        raise CookieSyncError("invalid cookie header")
    if data["provider"] == "cursor" and not header.startswith("WorkosCursorSessionToken="):
        raise CookieSyncError("cursor payload is not a WorkosCursorSessionToken cookie")
    if data["provider"] == "grok" and "sso" not in header:
        raise CookieSyncError("grok payload has no sso cookie")
    return data


# --------------------------------------------------------------------------- push (Mac)


def build_payloads(providers: list[str], grok_profile: str | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    payloads: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for provider in providers:
        try:
            body = extract_cursor() if provider == "cursor" else extract_grok(grok_profile)
        except CookieSyncError as exc:
            errors[provider] = str(exc)
            continue
        payloads.append({"schema": SCHEMA, "pushed_at": _iso(time.time()), **body})
    return payloads, errors


def remote_write_command(directory: str, provider: str) -> str:
    """Shell snippet run on the box: stdin -> atomic 0660 file in the drop dir."""
    if provider not in PROVIDERS:
        raise CookieSyncError("unsupported provider")
    tmp = shlex.quote(f"{directory}/.{provider}.tmp")
    final = shlex.quote(f"{directory}/{provider}.json")
    return f"umask 007 && cat > {tmp} && mv -f {tmp} {final}"


def push_payload(payload: dict[str, Any], *, host: str, directory: str, ssh: str = "ssh") -> None:
    cmd = [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        host,
        remote_write_command(directory, payload["provider"]),
    ]
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell on this side
            cmd,
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=_SSH_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CookieSyncError("ssh to the box timed out") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise CookieSyncError(f"ssh push failed (rc={proc.returncode}): {err[0][:200]}")


def run_push(args: argparse.Namespace) -> int:
    providers = list(PROVIDERS) if args.provider == "all" else [args.provider]
    payloads, errors = build_payloads(providers, args.grok_profile)
    rc = 0
    for provider in providers:
        if provider in errors:
            print(f"{provider}: skipped - {errors[provider]}", file=sys.stderr)
            rc = 1
    for payload in payloads:
        provider = payload["provider"]
        exp = payload["expires_at"] or "unknown"
        if args.dry_run:
            print(f"{provider}: extracted from {payload['source']} (expires {exp}); dry run, nothing sent")
            continue
        try:
            push_payload(payload, host=args.to, directory=args.remote_dir, ssh=args.ssh)
        except CookieSyncError as exc:
            print(f"{provider}: {exc}", file=sys.stderr)
            rc = 1
            continue
        print(f"{provider}: pushed from {payload['source']} (expires {exp})")
    return rc


# --------------------------------------------------------------------------- ingest (box)


def _codexbar_bin() -> str:
    found = shutil.which("codexbar") or str(Path.home() / ".local/bin/codexbar")
    if not Path(found).exists():
        raise CookieSyncError("codexbar not found on this host")
    return found


def _shred(path: Path) -> None:
    if shutil.which("shred"):
        subprocess.run(["shred", "-u", str(path)], check=False, capture_output=True)  # noqa: S603,S607
    if path.exists():
        try:
            path.write_bytes(b"\0" * max(1, path.stat().st_size))
        finally:
            path.unlink(missing_ok=True)


def _record_status(provider: str, entry: dict[str, Any]) -> None:
    path = status_path()
    try:
        current = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    current[provider] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(current, fh, indent=2)
    os.replace(tmp, path)


def apply_cookie(provider: str, header: str, *, codexbar: str | None = None, timeout: float = 30.0) -> None:
    """Hand a cookie to CodexBar on stdin (never argv)."""
    proc = subprocess.run(  # noqa: S603
        [codexbar or _codexbar_bin(), "config", "set-cookie", "--provider", provider, "--stdin"],
        input=header.encode(),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise CookieSyncError(f"codexbar set-cookie failed for {provider} (rc={proc.returncode})")


def ingest(directory: Path | None = None, *, codexbar: str | None = None) -> dict[str, str]:
    """Apply and shred pending drop files.  Returns ``{provider: outcome}``."""
    d = directory or drop_dir()
    results: dict[str, str] = {}
    if not d.is_dir():
        return results
    for provider in PROVIDERS:
        path = d / f"{provider}.json"
        if not path.is_file():
            continue
        try:
            payload = validate_payload(json.loads(path.read_text()))
            if payload["provider"] != provider:
                raise CookieSyncError("payload provider does not match file name")
            apply_cookie(provider, payload["cookie_header"], codexbar=codexbar)
            _record_status(
                provider,
                {
                    "applied_at": _iso(time.time()),
                    "pushed_at": payload.get("pushed_at"),
                    "expires_at": payload.get("expires_at"),
                    "source": payload.get("source"),
                },
            )
            results[provider] = "applied"
        except (CookieSyncError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            results[provider] = f"failed: {exc.__class__.__name__}: {exc}"[:200]
        finally:
            _shred(path)
    return results


def ingest_quietly() -> None:
    """Best-effort hook for collectors: never raises, never prints."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    try:
        ingest()
    except Exception:  # noqa: BLE001
        return


def run_ingest(args: argparse.Namespace) -> int:
    results = ingest(Path(args.drop_dir) if args.drop_dir else None)
    if not results:
        print("no pending cookie drops")
        return 0
    rc = 0
    for provider, outcome in results.items():
        print(f"{provider}: {outcome}")
        if outcome != "applied":
            rc = 1
    return rc


def run_status(_args: argparse.Namespace) -> int:
    path = status_path()
    if not path.exists():
        print("no cookie sync recorded yet")
        return 0
    data = json.loads(path.read_text())
    now = time.time()
    for provider in PROVIDERS:
        entry = data.get(provider)
        if not entry:
            print(f"{provider}: never synced")
            continue
        exp = entry.get("expires_at")
        left = ""
        if exp:
            try:
                secs = datetime.strptime(exp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp() - now
                left = f", {secs / 86400:.1f} days left" if secs > 0 else ", EXPIRED"
            except ValueError:
                pass
        print(f"{provider}: applied {entry.get('applied_at')} from {entry.get('source')}, expires {exp}{left}")
    return 0


# --------------------------------------------------------------------------- CLI


def run_sync_cookies_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="aiuse sync-cookies",
        description="Copy CodexBar web-session cookies (cursor, grok) from a Mac to a headless box.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    push = sub.add_parser("push", help="(Mac) extract cookies and send them to the box over ssh")
    push.add_argument("--to", required=True, metavar="SSH_HOST", help="ssh host/alias of the box drop user")
    push.add_argument("--provider", choices=(*PROVIDERS, "all"), default="all")
    push.add_argument(
        "--grok-profile", metavar="DIR", help="Chrome profile holding the grok.com login (default: longest-lived)"
    )
    push.add_argument(
        "--remote-dir",
        default=DEFAULT_DROP_DIR,
        metavar="DIR",
        help=f"drop directory on the box (default: {DEFAULT_DROP_DIR})",
    )
    push.add_argument("--ssh", default="ssh", metavar="CMD")
    push.add_argument("--dry-run", action="store_true", help="extract and validate only; send nothing")
    ing = sub.add_parser("ingest", help="(box) apply pending drops with `codexbar config set-cookie`")
    ing.add_argument("--drop-dir", metavar="DIR", help="override drop directory ($AIUSE_COOKIE_DROP)")
    sub.add_parser("status", help="show when each cookie was applied and when it expires")
    args = parser.parse_args(argv)
    if args.action == "push":
        return run_push(args)
    if args.action == "ingest":
        return run_ingest(args)
    return run_status(args)
