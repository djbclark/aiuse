"""macOS codesign / Keychain trust helpers for collectors (caut + CodexBar).

Keychain "Always Allow" binds to a binary's code identity. Cargo-installed caut is
often adhoc/linker-signed, so grants do not survive reinstalls. These helpers
inspect codesign status, guide a stable self-signed identity, and re-sign caut.

CodexBar CLI prompts for "CodexBar Cache" are a separate bug: cache items trust
only the .app, not CodexBarCLI (steipete/CodexBar#679). ``fix-codexbar-cache``
rewrites those ACLs (app + CLI + /usr/bin/security).

See docs/macos-keychain-trust.md and docs/macos-keychain-trust-plan.md.
"""

from __future__ import annotations

import getpass
import os
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

DEFAULT_CODESIGN_IDENTITY = "aiuse-local-codesign"
ENV_CODESIGN_IDENTITY = "AIUSE_CODESIGN_IDENTITY"
ENV_AUTOSIGN_CAUT = "AIUSE_AUTOSIGN_CAUT"
ENV_KEYCHAIN_PASSWORD = "AIUSE_KEYCHAIN_PASSWORD"  # optional; prefer interactive getpass

CODEXBAR_CACHE_SERVICE = "com.steipete.codexbar.cache"
CODEXBAR_CACHE_LABEL = "CodexBar Cache"
# Public Developer ID team for CodexBar (Peter Steinberger); overridden from codesign when possible.
CODEXBAR_DEFAULT_TEAM_ID = "Y5PE65HELJ"

# Common keychain item labels caut / CodexBar touch (not exhaustive).
KNOWN_KEYCHAIN_ITEMS: tuple[str, ...] = (
    "Claude Code-credentials",
    "Claude Safe Storage",
    "Cursor Safe Storage",
    "OpenCode Safe Storage",
    "Antigravity IDE Safe Storage",
    "CodexBar Cache",  # service often com.steipete.codexbar.cache
)

# CodexBar prefs domain (best-effort; keys may change across app versions).
CODEXBAR_PREFS_DOMAIN = "com.steipete.codexbar"
CODEXBAR_KEYCHAIN_PREF_KEYS: tuple[str, ...] = (
    "claudeOAuthKeychainPromptMode",
    "claudeOAuthKeychainReadStrategy",
    "debugDisableKeychainAccess",
    "claudeOAuthKeychainDeniedUntil",
)


@dataclass(frozen=True)
class CodesignInfo:
    path: Path
    exists: bool
    adhoc: bool = False
    signed: bool = False
    authority: str | None = None
    team_identifier: str | None = None
    identifier: str | None = None
    flags: str | None = None
    raw: str = ""
    error: str | None = None

    @property
    def stable(self) -> bool:
        """True when signed with a non-adhoc identity (Always Allow can stick)."""
        return self.exists and self.signed and not self.adhoc and not self.error


@dataclass
class TrustStatus:
    lines: list[str] = field(default_factory=list)
    caut_adhoc: bool = False
    caut_path: Path | None = None
    identity: str = DEFAULT_CODESIGN_IDENTITY
    identity_present: bool = False


def is_darwin() -> bool:
    return sys.platform == "darwin"


def configured_identity(
    config: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve preferred codesign identity name.

    Precedence: ``AIUSE_CODESIGN_IDENTITY`` → ``config.toml [macos].codesign_identity``
    → default ``aiuse-local-codesign``.
    """
    environ = env if env is not None else os.environ
    from_env = (environ.get(ENV_CODESIGN_IDENTITY) or "").strip()
    if from_env:
        return from_env
    macos = (config or {}).get("macos") if isinstance(config, Mapping) else None
    if isinstance(macos, Mapping):
        name = macos.get("codesign_identity")
        if name is not None and str(name).strip():
            return str(name).strip()
    return DEFAULT_CODESIGN_IDENTITY


def resolve_caut_binary(*, which_fn: Callable[[str], str | None] | None = None) -> Path | None:
    """Locate caut and return the **resolved** real path (follows symlinks)."""
    lookup = which_fn if which_fn is not None else shutil.which
    candidates: list[Path] = []
    found = lookup("caut")
    if found:
        candidates.append(Path(found))
    home = Path.home()
    candidates.extend(
        [
            home / ".cargo" / "bin" / "caut",
            home / ".local" / "bin" / "caut",
        ]
    )
    seen: set[Path] = set()
    for cand in candidates:
        try:
            if not cand.exists():
                continue
            real = cand.resolve()
            if real in seen:
                continue
            seen.add(real)
            if real.is_file() and os.access(real, os.X_OK):
                return real
        except OSError:
            continue
    return None


def resolve_codexbar_app() -> Path | None:
    """Locate CodexBar.app if installed."""
    candidates = [
        Path("/Applications/CodexBar.app"),
        Path.home() / "Applications" / "CodexBar.app",
    ]
    which = shutil.which("codexbar")
    if which:
        # Homebrew often links CLI; app may still live under /Applications.
        candidates.insert(0, Path("/Applications/CodexBar.app"))
        # If which points into a .app bundle, prefer that bundle.
        try:
            p = Path(which).resolve()
            for parent in p.parents:
                if parent.name.endswith(".app") and (parent / "Contents" / "MacOS").is_dir():
                    candidates.insert(0, parent)
                    break
        except OSError:
            pass
    for cand in candidates:
        if cand.is_dir() and (cand / "Contents" / "MacOS").is_dir():
            return cand.resolve()
    return None


def resolve_codexbar_cli(*, which_fn: Callable[[str], str | None] | None = None) -> Path | None:
    """Locate CodexBarCLI helper (the binary aiuse/hourly runs as ``codexbar``)."""
    app = resolve_codexbar_app()
    if app is not None:
        helper = app / "Contents" / "Helpers" / "CodexBarCLI"
        if helper.is_file() and os.access(helper, os.X_OK):
            return helper.resolve()
    lookup = which_fn if which_fn is not None else shutil.which
    found = lookup("codexbar")
    if found:
        try:
            real = Path(found).resolve()
            if real.is_file() and os.access(real, os.X_OK):
                return real
        except OSError:
            pass
    return None


def login_keychain_path() -> Path:
    return Path.home() / "Library" / "Keychains" / "login.keychain-db"


def codexbar_team_id(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Team ID from CodexBar.app codesign, else default public team."""
    app = resolve_codexbar_app()
    if app is not None:
        info = codesign_display(app, run_fn=run_fn)
        if info.team_identifier:
            return info.team_identifier
    return CODEXBAR_DEFAULT_TEAM_ID


def list_codexbar_cache_accounts(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    keychain: Path | None = None,
) -> list[str]:
    """List account names for service ``com.steipete.codexbar.cache`` (no secrets)."""
    if not is_darwin():
        return []
    kc = keychain or login_keychain_path()
    runner = run_fn if run_fn is not None else subprocess.run
    try:
        proc = runner(
            ["security", "dump-keychain", str(kc)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    text = (proc.stdout or "") + (proc.stderr or "")
    accounts: list[str] = []
    # dump-keychain emits acct then svce within each genp block — track last acct.
    last_acct: str | None = None
    for line in text.splitlines():
        acct_m = re.search(r'"acct"<blob>="([^"]*)"', line)
        if acct_m:
            last_acct = acct_m.group(1)
            continue
        if "com.steipete.codexbar.cache" in line and last_acct is not None:
            accounts.append(last_acct)
            last_acct = None
    return sorted(set(accounts))


def fix_codexbar_cache_account(
    account: str,
    *,
    dry_run: bool = False,
    keychain_password: str | None = None,
    app_path: Path | None = None,
    cli_path: Path | None = None,
    team_id: str | None = None,
    keychain: Path | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[bool, str]:
    """Rewrite one CodexBar Cache item so app + CLI are trusted (#679).

    Never includes the secret in the returned message.
    """
    if not is_darwin():
        return False, "macOS only"
    app = app_path or resolve_codexbar_app()
    cli = cli_path or resolve_codexbar_cli()
    if app is None:
        return False, "CodexBar.app not found under /Applications"
    if cli is None:
        return False, "CodexBarCLI helper not found"
    tid = team_id or codexbar_team_id(run_fn=run_fn)

    if dry_run:
        return True, (
            f"dry-run: would rewrite {CODEXBAR_CACHE_SERVICE} acct={account!r} "
            f"with -T {app} -T {cli} -T /usr/bin/security "
            f"+ partition teamid:{tid}"
        )

    kc = keychain or login_keychain_path()
    if not kc.is_file():
        return False, f"login keychain not found: {kc}"
    runner = run_fn if run_fn is not None else subprocess.run

    secret: str | None = None
    try:
        read = runner(
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                CODEXBAR_CACHE_SERVICE,
                "-a",
                account,
                str(kc),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if read.returncode != 0:
            err = (read.stderr or read.stdout or "").strip() or f"exit {read.returncode}"
            return False, f"read failed for acct={account!r}: {err[:200]}"
        secret = (read.stdout or "").rstrip("\n")
        if not secret:
            return False, f"empty secret for acct={account!r} — skip"

        delete = runner(
            [
                "security",
                "delete-generic-password",
                "-s",
                CODEXBAR_CACHE_SERVICE,
                "-a",
                account,
                str(kc),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if delete.returncode != 0:
            err = (delete.stderr or delete.stdout or "").strip() or f"exit {delete.returncode}"
            return False, f"delete failed for acct={account!r}: {err[:200]}"

        add = runner(
            [
                "security",
                "add-generic-password",
                "-s",
                CODEXBAR_CACHE_SERVICE,
                "-a",
                account,
                "-l",
                CODEXBAR_CACHE_LABEL,
                "-T",
                str(app),
                "-T",
                str(cli),
                "-T",
                "/usr/bin/security",
                "-w",
                secret,
                str(kc),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if add.returncode != 0:
            err = (add.stderr or add.stdout or "").strip() or f"exit {add.returncode}"
            return False, f"add failed for acct={account!r}: {err[:200]}"

        if keychain_password is not None and keychain_password != "":
            part = runner(
                [
                    "security",
                    "set-generic-password-partition-list",
                    "-S",
                    f"apple-tool:,apple:,teamid:{tid}",
                    "-s",
                    CODEXBAR_CACHE_SERVICE,
                    "-a",
                    account,
                    "-k",
                    keychain_password,
                    str(kc),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if part.returncode != 0:
                err = (part.stderr or part.stdout or "").strip() or f"exit {part.returncode}"
                return True, (
                    f"rewrote ACL for acct={account!r} (app+CLI trusted); "
                    f"partition-list failed: {err[:160]} — may still work; re-run with password"
                )
            return True, f"rewrote ACL + partition list for acct={account!r}"
        return True, (
            f"rewrote ACL for acct={account!r} (app+CLI trusted); "
            "skipped partition-list (no keychain password) — usually enough for CLI"
        )
    finally:
        secret = None  # noqa: F841 — drop reference to secret material


def fix_codexbar_cache_all(
    *,
    accounts: list[str] | None = None,
    dry_run: bool = False,
    keychain_password: str | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
) -> tuple[int, list[str]]:
    """Fix all (or selected) CodexBar Cache accounts. Returns (failures, lines)."""
    lines: list[str] = []
    if not is_darwin():
        return 0, ["macOS only: aiuse trust fix-codexbar-cache"]

    app = resolve_codexbar_app()
    cli = resolve_codexbar_cli(which_fn=which_fn)
    tid = codexbar_team_id(run_fn=run_fn)
    lines.append("CodexBar Cache ACL repair (steipete/CodexBar#679)")
    lines.append(f"  service: {CODEXBAR_CACHE_SERVICE}")
    lines.append(f"  app: {app or '(missing)'}")
    lines.append(f"  cli: {cli or '(missing)'}")
    lines.append(f"  teamid: {tid}")
    if dry_run:
        lines.append("  mode: dry-run (no keychain writes)")

    if app is None or cli is None:
        lines.append("error: need both CodexBar.app and CodexBarCLI")
        return 1, lines

    found = list_codexbar_cache_accounts(run_fn=run_fn)
    if accounts:
        targets = list(accounts)
    else:
        targets = found

    if not targets:
        lines.append("No CodexBar Cache accounts found (nothing to fix).")
        return 0, lines

    lines.append(f"  accounts ({len(targets)}): {', '.join(targets)}")
    if found and accounts:
        unknown = [a for a in accounts if a not in found]
        if unknown:
            lines.append(f"  note: not currently in keychain dump: {', '.join(unknown)}")

    failures = 0
    for acct in targets:
        ok, msg = fix_codexbar_cache_account(
            acct,
            dry_run=dry_run,
            keychain_password=keychain_password,
            app_path=app,
            cli_path=cli,
            team_id=tid,
            run_fn=run_fn,
        )
        lines.append(f"  {'ok' if ok else 'FAIL'}  {msg}")
        if not ok:
            failures += 1
    if not dry_run and failures == 0:
        lines.append("Done. Verify: codexbar usage --provider codex --json-only")
        lines.append("Note: older CodexBar builds may rewrite ACLs on refresh; upgrade when possible.")
    return failures, lines


def parse_codesign_output(text: str) -> dict[str, Any]:
    """Parse ``codesign -dv --verbose=4`` stderr/stdout into fields."""
    adhoc = False
    signed = False
    authority: str | None = None
    team: str | None = None
    identifier: str | None = None
    flags: str | None = None

    # flags=0x20002(adhoc,linker-signed)
    m_flags = re.search(r"flags=0x[0-9a-fA-F]+\(([^)]+)\)", text)
    if m_flags:
        flags = m_flags.group(1)
        if "adhoc" in flags.lower():
            adhoc = True

    if re.search(r"^Signature=adhoc\s*$", text, re.MULTILINE) or re.search(r"Signature=adhoc\b", text):
        adhoc = True
        signed = True  # adhoc is still a form of signature

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Authority="):
            # Prefer first Authority= (leaf)
            if authority is None:
                authority = line.split("=", 1)[1].strip() or None
                if authority:
                    signed = True
                    if authority.lower() in {"adhoc", "-"}:
                        adhoc = True
        elif line.startswith("TeamIdentifier="):
            team = line.split("=", 1)[1].strip()
            if team.lower() in {"not set", "n/a", ""}:
                team = None
        elif line.startswith("Identifier="):
            identifier = line.split("=", 1)[1].strip() or None
        elif line.startswith("Signature="):
            sig = line.split("=", 1)[1].strip().lower()
            if sig == "adhoc":
                adhoc = True
                signed = True
            elif sig and sig not in {"?", "none"}:
                signed = True

    if "linker-signed" in text.lower() and "adhoc" in text.lower():
        adhoc = True
        signed = True

    # If we got any codesign dump without error, treat as at least inspected.
    if not signed and not adhoc and "Executable=" in text:
        # Unsigned completely
        signed = False

    return {
        "adhoc": adhoc,
        "signed": signed or adhoc,
        "authority": authority,
        "team_identifier": team,
        "identifier": identifier,
        "flags": flags,
    }


def codesign_display(
    path: Path,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> CodesignInfo:
    """Inspect codesign metadata for a path (read-only)."""
    if not path.exists():
        return CodesignInfo(path=path, exists=False, error="path does not exist")
    runner = run_fn if run_fn is not None else subprocess.run
    try:
        proc = runner(
            ["codesign", "-dv", "--verbose=4", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except FileNotFoundError:
        return CodesignInfo(path=path, exists=True, error="codesign not found on PATH")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CodesignInfo(path=path, exists=True, error=str(exc))

    raw = (proc.stderr or "") + (proc.stdout or "")
    if proc.returncode != 0 and not raw.strip():
        return CodesignInfo(
            path=path,
            exists=True,
            error=f"codesign exited {proc.returncode}",
            raw=raw,
        )
    # codesign -d writes to stderr even on success
    if "code object is not signed at all" in raw.lower():
        return CodesignInfo(path=path, exists=True, signed=False, adhoc=False, raw=raw)

    fields = parse_codesign_output(raw)
    return CodesignInfo(
        path=path,
        exists=True,
        adhoc=bool(fields["adhoc"]),
        signed=bool(fields["signed"]),
        authority=fields["authority"],
        team_identifier=fields["team_identifier"],
        identifier=fields["identifier"],
        flags=fields["flags"],
        raw=raw,
    )


def list_codesigning_identities(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[str]:
    """Return identity names from ``security find-identity -v -p codesigning``."""
    if not is_darwin():
        return []
    runner = run_fn if run_fn is not None else subprocess.run
    try:
        proc = runner(
            ["security", "find-identity", "-v", "-p", "codesigning"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    text = proc.stdout or ""
    names: list[str] = []
    #  1) ABCDEF... "My Identity Name"
    for match in re.finditer(r'^\s*\d+\)\s+[0-9A-Fa-f]+\s+"([^"]+)"', text, re.MULTILINE):
        names.append(match.group(1))
    return names


def identity_available(
    identity: str,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    names = list_codesigning_identities(run_fn=run_fn)
    if identity in names:
        return True
    # Also match if identity is a hash prefix listed without relying on exact string
    return any(identity == n or identity in n for n in names)


def sign_caut(
    identity: str,
    path: Path | None = None,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
) -> tuple[bool, str]:
    """Force-sign caut with ``identity``. Returns (ok, message)."""
    if not is_darwin():
        return False, "macOS only"
    binary = path or resolve_caut_binary(which_fn=which_fn)
    if binary is None:
        return False, "caut binary not found (install via packaging/install-deps.sh)"
    real = binary.resolve()
    if not real.is_file():
        return False, f"caut path is not a file: {real}"
    runner = run_fn if run_fn is not None else subprocess.run
    try:
        proc = runner(
            [
                "codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                str(real),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError:
        return False, "codesign not found on PATH"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return False, f"codesign failed for {real}: {err}"
    return True, f"signed {real} with identity {identity!r}"


def codexbar_keychain_prefs(
    *,
    prefs_path: Path | None = None,
) -> dict[str, Any] | None:
    """Read known CodexBar keychain-related prefs (best-effort, read-only)."""
    path = prefs_path
    if path is None:
        path = Path.home() / "Library" / "Preferences" / f"{CODEXBAR_PREFS_DOMAIN}.plist"
    if not path.is_file():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    out: dict[str, Any] = {}
    for key in CODEXBAR_KEYCHAIN_PREF_KEYS:
        if key in data:
            val = data[key]
            # Avoid dumping large blobs
            if isinstance(val, (bytes, bytearray)):
                out[key] = f"<bytes len={len(val)}>"
            elif isinstance(val, str) and len(val) > 120:
                out[key] = val[:117] + "..."
            else:
                out[key] = val
    return out or None


def try_open_keychain_access(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str | None:
    """Best-effort: open Keychain Access.app. Returns a short status line or None."""
    if not is_darwin():
        return None
    runner = run_fn if run_fn is not None else subprocess.run
    try:
        proc = runner(
            ["open", "-a", "Keychain Access"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return 'Opened "Keychain Access" (Certificate Assistant → Create a Certificate…)'
    return None


def ensure_identity_guide(identity: str) -> list[str]:
    """Printable steps to create a self-signed Code Signing certificate."""
    return [
        f"Create a stable Code Signing identity (once per Mac) named: {identity}",
        "",
        "  1. Open Keychain Access:",
        '       open -a "Keychain Access"',
        "  2. Menu: Keychain Access → Certificate Assistant → Create a Certificate…",
        f"  3. Name: {identity}",
        "     Identity Type: Self Signed Root",
        "     Certificate Type: Code Signing",
        "  4. Optional: double-click the cert → Trust → Code Signing → Always Trust",
        "  5. Optional: expand cert → private key → Access Control",
        "     → allow /usr/bin/codesign (avoids prompts when signing)",
        "  6. Verify:",
        "       security find-identity -v -p codesigning",
        "",
        "Then:",
        "  aiuse trust sign-caut",
        "  aiuse trust probe          # optional, interactive Always Allow clicks",
        "",
        "Configure the name (optional):",
        f"  export {ENV_CODESIGN_IDENTITY}={identity}",
        "  # or in ~/.config/aiuse/config.toml:",
        "  # [macos]",
        f'  # codesign_identity = "{identity}"',
    ]


def caut_next_steps_footer(
    *,
    identity: str,
    identity_present: bool,
    caut_path: Path | None,
    caut_adhoc: bool,
) -> list[str]:
    """Short next-action lines after status (caut-focused)."""
    lines = ["", "Next (caut):"]
    if caut_path is None:
        lines.append("  • Install caut: packaging/install-deps.sh  (or just install-deps)")
        return lines
    if not identity_present:
        lines.append(f"  1. Create Code Signing cert {identity!r}:  aiuse trust ensure-identity")
        lines.append("  2. Sign:  aiuse trust sign-caut")
        lines.append("  3. Always Allow:  aiuse trust probe")
        return lines
    if caut_adhoc:
        lines.append("  1. Sign:  aiuse trust sign-caut")
        lines.append("  2. Always Allow:  aiuse trust probe")
        lines.append("  3. Confirm:  aiuse doctor")
        return lines
    lines.append("  • caut looks stable-signed — optional: aiuse trust probe")
    lines.append("  • After every cargo install: aiuse trust sign-caut")
    return lines


def grant_guide_lines() -> list[str]:
    items = "\n".join(f"  • {name}" for name in KNOWN_KEYCHAIN_ITEMS)
    return [
        "Keychain Access — grant caut (or CodexBar) access once per item",
        "",
        "There is no global “allow this app all keychain items.” ACLs are per item.",
        "",
        "=== caut (adhoc cargo binary) ===",
        "Option A — click Always Allow when prompted (preferred after stable sign):",
        "  1. Sign caut: aiuse trust sign-caut",
        "  2. Run: aiuse trust probe",
        "  3. For each dialog, click Always Allow (not Allow Once)",
        "",
        "Option B — edit ACL in Keychain Access:",
        "  1. Open Keychain Access → login keychain → Passwords (or All Items)",
        "  2. Find an item (common names below) → double-click → Access Control",
        "  3. Click + → Go to Folder (⌘⇧G) → paste the real caut path from",
        "     aiuse trust status → Add → Save Changes",
        "",
        "Common item names (not exhaustive):",
        items,
        "",
        "Do NOT default to “Allow all applications to access this item” — that",
        "lets any local process read the secret. Use only as a nuclear option.",
        "",
        "=== CodexBar Cache (CodexBar#679) ===",
        "If prompts say CodexBarCLI + 'CodexBar Cache' (not Claude Code-credentials):",
        "  aiuse trust fix-codexbar-cache --dry-run   # list plan",
        "  aiuse trust fix-codexbar-cache             # rewrite ACLs (may ask login password)",
        "This trusts both CodexBar.app and CodexBarCLI on com.steipete.codexbar.cache.",
        "Claude OAuth prefs (Avoid Keychain prompts) are separate — see trust status.",
        "Docs: docs/macos-keychain-trust.md",
    ]


def format_codesign_summary(info: CodesignInfo, *, label: str) -> list[str]:
    if not info.exists:
        return [f"{label}: missing ({info.path})"]
    if info.error and not info.raw:
        return [f"{label}: {info.path}", f"  error: {info.error}"]
    if info.adhoc:
        kind = "adhoc / linker-signed (Always Allow will not stick across reinstalls)"
    elif info.stable:
        kind = "stable-signed"
    elif info.signed:
        kind = "signed"
    else:
        kind = "unsigned"
    lines = [
        f"{label}: {info.path}",
        f"  codesign: {kind}",
    ]
    if info.authority:
        lines.append(f"  Authority: {info.authority}")
    if info.team_identifier:
        lines.append(f"  TeamIdentifier: {info.team_identifier}")
    if info.identifier:
        lines.append(f"  Identifier: {info.identifier}")
    if info.flags:
        lines.append(f"  flags: {info.flags}")
    if info.error:
        lines.append(f"  note: {info.error}")
    return lines


def collect_status(
    config: Mapping[str, Any] | None = None,
    *,
    which_fn: Callable[[str], str | None] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> TrustStatus:
    """Build human status lines for caut + CodexBar (always safe / non-mutating)."""
    st = TrustStatus()
    st.identity = configured_identity(config)
    if not is_darwin():
        st.lines = [
            "aiuse trust status",
            "  platform: not macOS — codesign / Keychain helpers are Darwin-only",
            f"  configured identity name: {st.identity}",
        ]
        return st

    st.lines.append("aiuse trust status  (macOS)")
    st.lines.append(f"  configured identity: {st.identity}")
    names = list_codesigning_identities(run_fn=run_fn)
    st.identity_present = identity_available(st.identity, run_fn=run_fn)
    if names:
        st.lines.append(f"  codesigning identities ({len(names)}):")
        for n in names[:12]:
            mark = " ← configured" if n == st.identity else ""
            st.lines.append(f"    • {n}{mark}")
        if len(names) > 12:
            st.lines.append(f"    … +{len(names) - 12} more")
        if not st.identity_present:
            st.lines.append(f"  note: configured identity {st.identity!r} not in find-identity list")
    else:
        st.lines.append("  codesigning identities: (none) — run: aiuse trust setup")

    st.lines.append("")
    caut = resolve_caut_binary(which_fn=which_fn)
    st.caut_path = caut
    if caut is None:
        st.lines.append("caut: not found on PATH or ~/.cargo/bin / ~/.local/bin")
    else:
        info = codesign_display(caut, run_fn=run_fn)
        st.caut_adhoc = bool(info.adhoc or (info.exists and not info.stable))
        # Prefer explicit adhoc flag for doctor; unsigned also needs sign
        if info.exists and (info.adhoc or not info.stable):
            st.caut_adhoc = True
        if info.stable:
            st.caut_adhoc = False
        st.lines.extend(format_codesign_summary(info, label="caut"))

    st.lines.append("")
    app = resolve_codexbar_app()
    if app is None:
        codexbar_cli = (which_fn or shutil.which)("codexbar")
        if codexbar_cli:
            st.lines.append(f"CodexBar CLI: {codexbar_cli} (app bundle not found under /Applications)")
            info = codesign_display(Path(codexbar_cli).resolve(), run_fn=run_fn)
            st.lines.extend(format_codesign_summary(info, label="codexbar CLI"))
        else:
            st.lines.append("CodexBar: not found")
    else:
        # Sign the app bundle
        info = codesign_display(app, run_fn=run_fn)
        st.lines.extend(format_codesign_summary(info, label="CodexBar.app"))
        if info.stable:
            st.lines.append("  note: Team-signed app — do not re-sign; use Settings for keychain prompts")

    prefs = codexbar_keychain_prefs()
    if prefs:
        st.lines.append("  keychain-related prefs (read-only):")
        for k, v in prefs.items():
            st.lines.append(f"    {k} = {v!r}")

    cli = resolve_codexbar_cli(which_fn=which_fn)
    if cli is not None:
        st.lines.append(f"  CodexBarCLI: {cli}")
    cache_accts = list_codexbar_cache_accounts(run_fn=run_fn)
    if cache_accts:
        st.lines.append(f"  CodexBar Cache accounts ({len(cache_accts)}): {', '.join(cache_accts)}")
        st.lines.append("  if CLI prompts for 'CodexBar Cache': aiuse trust fix-codexbar-cache")
        st.lines.append("  (CodexBar#679 — trust CodexBarCLI on cache items)")
    elif app is not None or cli is not None:
        st.lines.append("  CodexBar Cache accounts: (none found)")

    st.lines.append("")
    st.lines.append("Hints: aiuse trust setup · sign-caut · fix-codexbar-cache · docs/macos-keychain-trust.md")
    st.lines.extend(
        caut_next_steps_footer(
            identity=st.identity,
            identity_present=st.identity_present,
            caut_path=st.caut_path,
            caut_adhoc=st.caut_adhoc,
        )
    )
    return st


def doctor_caut_codesign_lines(
    config: Mapping[str, Any] | None,
    *,
    which_fn: Callable[[str], str | None] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    collector_enabled: bool = True,
) -> list[str]:
    """Lines for ``aiuse doctor`` (empty if N/A). Never raises."""
    if not is_darwin() or not collector_enabled:
        return []
    path = resolve_caut_binary(which_fn=which_fn)
    if path is None:
        return []
    info = codesign_display(path, run_fn=run_fn)
    if info.stable:
        return [
            "macOS codesign (caut)",
            f"  ok       stable-signed · {path}" + (f" · {info.authority}" if info.authority else ""),
        ]
    # adhoc or unsigned
    return [
        "macOS codesign (caut)",
        "  WARN     Binary is ad-hoc signed or unsigned (no stable identity).",
        '           macOS Keychain "Always Allow" grants will not survive the next update.',
        "           Fix: Run `aiuse trust setup` then `aiuse trust sign-caut` after cargo install.",
        f"           path: {path}",
    ]


def run_trust_command(
    argv: list[str],
    *,
    config: Mapping[str, Any] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    stdout_print: Callable[[str], None] | None = None,
) -> int:
    """CLI entry for ``aiuse trust [subcommand]``. Returns process exit code."""
    out = stdout_print or (lambda s: print(s))
    args = list(argv)
    cmd = args[0] if args else "status"

    if cmd in ("-h", "--help", "help"):
        out(_TRUST_HELP)
        return 0

    if cmd == "status":
        st = collect_status(config, which_fn=which_fn, run_fn=run_fn)
        out("\n".join(st.lines))
        return 0

    if cmd == "ensure-identity":
        identity = configured_identity(config)
        if is_darwin() and not identity_available(identity, run_fn=run_fn):
            opened = try_open_keychain_access(run_fn=run_fn)
            if opened:
                out(opened)
                out("")
        out("\n".join(ensure_identity_guide(identity)))
        if is_darwin() and identity_available(identity, run_fn=run_fn):
            out("")
            out(f"Identity {identity!r} is already available in the login keychain.")
            out("Next: aiuse trust sign-caut")
        return 0

    if cmd == "grant-guide":
        out("\n".join(grant_guide_lines()))
        return 0

    if cmd == "sign-caut":
        if not is_darwin():
            out("macOS only: aiuse trust sign-caut")
            return 0
        identity = configured_identity(config)
        if not identity_available(identity, run_fn=run_fn):
            out(f"error: codesigning identity {identity!r} not found.")
            opened = try_open_keychain_access(run_fn=run_fn)
            if opened:
                out(opened)
                out("")
            out("\n".join(ensure_identity_guide(identity)))
            return 1
        path = resolve_caut_binary(which_fn=which_fn)
        if path is None:
            out("error: caut binary not found")
            return 1
        before = codesign_display(path, run_fn=run_fn)
        out("Before:")
        out("\n".join(format_codesign_summary(before, label="caut")))
        ok, msg = sign_caut(identity, path, run_fn=run_fn, which_fn=which_fn)
        if not ok:
            out(f"error: {msg}")
            return 1
        out(msg)
        after = codesign_display(path, run_fn=run_fn)
        out("After:")
        out("\n".join(format_codesign_summary(after, label="caut")))
        if after.adhoc or not after.stable:
            out("warning: binary still looks adhoc/unstable — check identity trust settings")
            return 1
        out("Next: aiuse trust probe  (click Always Allow), then: aiuse doctor")
        return 0

    if cmd == "setup":
        identity = configured_identity(config)
        out("aiuse trust setup")
        out("")
        st = collect_status(config, which_fn=which_fn, run_fn=run_fn)
        out("\n".join(st.lines))
        out("")
        if not is_darwin():
            out("Nothing else to do on non-macOS.")
            return 0
        if not identity_available(identity, run_fn=run_fn):
            opened = try_open_keychain_access(run_fn=run_fn)
            if opened:
                out(opened)
                out("")
            out("\n".join(ensure_identity_guide(identity)))
            out("")
            out("After creating the certificate, re-run: aiuse trust setup")
            out("\n".join(grant_guide_lines()))
            return 0
        # Try sign if caut present and not stable
        path = resolve_caut_binary(which_fn=which_fn)
        if path is None:
            out("caut not installed — skip sign. Install: packaging/install-deps.sh")
        else:
            info = codesign_display(path, run_fn=run_fn)
            if info.stable and info.authority and identity in (info.authority or ""):
                out(f"caut already stable-signed with matching authority ({info.authority})")
            elif info.stable:
                out(
                    f"caut already stable-signed ({info.authority or 'ok'}). "
                    f"Re-sign with {identity!r}? run: aiuse trust sign-caut"
                )
            else:
                ok, msg = sign_caut(identity, path, run_fn=run_fn, which_fn=which_fn)
                if ok:
                    out(msg)
                    after = codesign_display(path, run_fn=run_fn)
                    out("\n".join(format_codesign_summary(after, label="caut")))
                    out("Next: aiuse trust probe  (click Always Allow on each dialog)")
                else:
                    out(f"sign-caut skipped/failed: {msg}")
                    out("Create/trust the identity, then: aiuse trust sign-caut")
        out("")
        out("\n".join(grant_guide_lines()))
        return 0

    if cmd == "probe":
        if not is_darwin():
            out("macOS only: aiuse trust probe")
            return 0
        out("Interactive probe — click Always Allow on each Keychain dialog.")
        out("This is optional education, not part of install-deps or LaunchAgent.")
        out("")
        caut = resolve_caut_binary(which_fn=which_fn)
        if caut is None:
            out("caut not found — skip caut probe")
        else:
            info = codesign_display(caut, run_fn=run_fn)
            if info.adhoc or not info.stable:
                out("warning: caut is still adhoc/unsigned — Always Allow may not stick.")
                out("         Run aiuse trust sign-caut first.")
            # "both" matches aiuse default collectors.caut.providers
            out(f"Running: {caut} usage --provider both --json")
            runner = run_fn if run_fn is not None else subprocess.run
            try:
                proc = runner(
                    [str(caut), "usage", "--provider", "both", "--json"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=90,
                )
                if proc.stdout:
                    out(proc.stdout.rstrip()[:4000])
                if proc.returncode != 0 and proc.stderr:
                    out(proc.stderr.rstrip()[:2000])
                out(f"caut exit: {proc.returncode}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                out(f"caut probe failed: {exc}")
        out("")
        codex = (which_fn or shutil.which)("codexbar")
        if codex:
            out(
                f"Running: {codex} usage --provider codex --json-only  "
                "(may prompt for CodexBar Cache — if so: aiuse trust fix-codexbar-cache)"
            )
            runner = run_fn if run_fn is not None else subprocess.run
            try:
                proc = runner(
                    [codex, "usage", "--provider", "codex", "--json-only"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                if proc.stdout:
                    out(proc.stdout.rstrip()[:2000])
                if proc.returncode != 0 and proc.stderr:
                    out(proc.stderr.rstrip()[:1500])
                out(f"codexbar exit: {proc.returncode}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                out(f"codexbar probe failed: {exc}")
        else:
            out("codexbar not on PATH — skip")
        out("")
        out("If dialogs appeared, prefer Always Allow. See: aiuse trust grant-guide")
        return 0

    if cmd == "fix-codexbar-cache":
        rest = args[1:]
        dry_run = False
        selected: list[str] = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("--dry-run", "-n"):
                dry_run = True
                i += 1
            elif tok == "--account" and i + 1 < len(rest):
                selected.append(rest[i + 1])
                i += 2
            elif tok in ("-h", "--help"):
                out(
                    "Usage: aiuse trust fix-codexbar-cache [--dry-run] [--account NAME]...\n"
                    "\n"
                    "Rewrite com.steipete.codexbar.cache items so CodexBar.app and\n"
                    "CodexBarCLI are both trusted (steipete/CodexBar#679).\n"
                    "\n"
                    "  --dry-run           Show plan without writing\n"
                    "  --account NAME      Only this account (repeatable); default: all found\n"
                    "\n"
                    "Optional: AIUSE_KEYCHAIN_PASSWORD for partition-list step, or you will\n"
                    "be prompted (TTY). Secrets are never printed.\n"
                )
                return 0
            else:
                out(f"error: unknown argument {tok!r}")
                out("Try: aiuse trust fix-codexbar-cache --help")
                return 2

        password: str | None = None
        if not dry_run:
            password = (os.environ.get(ENV_KEYCHAIN_PASSWORD) or "").strip() or None
            if password is None and sys.stdin.isatty():
                try:
                    password = getpass.getpass("Login keychain password (for partition-list; empty to skip): ")
                    if password == "":
                        password = None
                except (EOFError, KeyboardInterrupt):
                    out("aborted")
                    return 1

        failures, lines = fix_codexbar_cache_all(
            accounts=selected or None,
            dry_run=dry_run,
            keychain_password=password,
            run_fn=run_fn,
            which_fn=which_fn,
        )
        out("\n".join(lines))
        return 1 if failures else 0

    out(f"error: unknown trust command {cmd!r}")
    out(_TRUST_HELP)
    return 2


_TRUST_HELP = """\
Usage: aiuse trust [COMMAND]

Manage macOS codesigning and Keychain trust for collectors.

macOS remembers Keychain "Always Allow" by code identity. Cargo-installed
tools (caut) are adhoc-signed and lose grants on every reinstall. This
signs caut with a stable local Code Signing identity.

With no COMMAND, runs: status

Commands:
  status               Codesign status of caut + CodexBar Cache accounts
  setup                Guided caut flow: identity → sign → grant-guide
  ensure-identity      Create self-signed Code Signing cert steps
  sign-caut            Force-sign real caut binary
  grant-guide          Keychain steps (caut + CodexBar Cache)
  probe                Interactive caut + light codexbar (Always Allow)
  fix-codexbar-cache   Rewrite CodexBar Cache ACLs for CodexBarCLI (#679)
                       Options: --dry-run  --account NAME

Environment / config:
  AIUSE_CODESIGN_IDENTITY     Preferred identity name (default: aiuse-local-codesign)
  config.toml [macos]
    codesign_identity = "aiuse-local-codesign"
  AIUSE_AUTOSIGN_CAUT=1       Opt-in: install-deps may call sign-caut after cargo install
  AIUSE_KEYCHAIN_PASSWORD     Optional login keychain password for partition-list

Typical first-time flow:
  aiuse trust setup && aiuse trust probe
  aiuse trust fix-codexbar-cache --dry-run
  aiuse trust fix-codexbar-cache

After every cargo install / install-deps:
  aiuse trust sign-caut

See docs/macos-keychain-trust.md
"""
