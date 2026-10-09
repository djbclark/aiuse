"""Shared helpers for the macOS ``security`` CLI (issue #30).

1. Exit classification: ``security`` exits with the low byte of the OSStatus,
   so a locked keychain (152, ``-60008``) is not a missing item (44,
   ``-25300``) and neither is a bad credential. A timeout means a SecurityAgent
   prompt was raised and nobody answered it.
2. Secrets off argv: :func:`run_security_stdin` feeds one command to
   ``security -i`` on stdin, so ``-w <secret>`` and ``-k <password>`` never
   appear in ``ps`` output.

See docs/research/macos-keychain-access.md (branch claudehelm/keychain-research).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

RunFn = Callable[..., "subprocess.CompletedProcess[str]"]

# Classified outcomes of one ``security`` call.
OK = "ok"
MISSING = "missing"  # errSecItemNotFound: the item does not exist
LOCKED = "locked"  # keychain locked and no UI to unlock it (screen locked, ssh, LaunchAgent)
PROMPT = "prompt"  # the call blocked on a SecurityAgent dialog and timed out
DENIED = "denied"  # the user cancelled a dialog, or the keychain password was wrong
UNAVAILABLE = "unavailable"  # no ``security`` binary (not macOS)
ERROR = "error"  # anything else

# ``security`` exit status = OSStatus & 0xff.
EXIT_ITEM_NOT_FOUND = 44  # -25300 errSecItemNotFound
EXIT_DUPLICATE_ITEM = 45  # -25299 errSecDuplicateItem
_LOCKED_EXITS = {
    152: "-60008 errAuthorizationInternal",  # keychain locked, SecurityAgent could not ask
    36: "-25308 errSecInteractionNotAllowed",  # keychain locked, UI not allowed
}
_DENIED_EXITS = {
    128: "-128 userCanceled",
    51: "-25293 errSecAuthFailed",
}
_LOCKED_MARKERS = ("-60008", "-25308", "User interaction is not allowed")
_MISSING_MARKERS = ("-25300", "could not be found in the keychain")
_DENIED_MARKERS = ("-25293", "-128", "User canceled")

# ``security -i`` reads one line into a 4096-byte buffer and runs the rest of an
# over-long line as a *second* command, so stay well under it.
STDIN_LINE_MAX = 4000


def classify_exit(returncode: int | None, stderr: str = "", *, timed_out: bool = False) -> str:
    """Map one ``security`` result to OK / MISSING / LOCKED / PROMPT / DENIED / ERROR."""
    if timed_out:
        return PROMPT
    if returncode == 0:
        return OK
    text = stderr or ""
    if returncode in _LOCKED_EXITS or any(marker in text for marker in _LOCKED_MARKERS):
        return LOCKED
    if returncode == EXIT_ITEM_NOT_FOUND or any(marker in text for marker in _MISSING_MARKERS):
        return MISSING
    if returncode in _DENIED_EXITS or any(marker in text for marker in _DENIED_MARKERS):
        return DENIED
    return ERROR


_ACTIONS = {
    OK: "",
    MISSING: "the keychain item does not exist; sign in to the provider again",
    LOCKED: "the login keychain is locked (screen locked or no GUI session); not a credential problem, retry after unlock",
    PROMPT: "reading the item raised a Keychain prompt that nobody answered; run `aiuse trust audit`, not retried",
    DENIED: "keychain access was denied (dialog cancelled or wrong keychain password)",
    UNAVAILABLE: "the macOS `security` tool is not available here",
    ERROR: "unexpected `security` failure",
}


@dataclass(frozen=True)
class KeychainResult:
    """Classified outcome of one ``security`` call. Never holds a secret."""

    status: str
    returncode: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK

    def code_text(self) -> str:
        if self.status == PROMPT:
            return "timed out"
        if self.returncode is None:
            return "no exit status"
        meaning = _LOCKED_EXITS.get(self.returncode) or _DENIED_EXITS.get(self.returncode)
        if self.returncode == EXIT_ITEM_NOT_FOUND:
            meaning = "-25300 errSecItemNotFound"
        return f"security exit {self.returncode}" + (f", {meaning}" if meaning else "")

    def action(self) -> str:
        return _ACTIONS.get(self.status, _ACTIONS[ERROR])

    def message(self, item: str) -> str:
        """One actionable line, e.g. for a row note or a CLI error."""
        if self.ok:
            return f"{item}: ok"
        return f"{item}: keychain {self.status} ({self.code_text()}): {self.action()}"

    def to_dict(self, *, item: str, source: str = "keychain") -> dict[str, Any]:
        return {
            "source": source,
            "item": item,
            "status": self.status,
            "exit_code": self.returncode,
            "action": self.action(),
        }


def result_from_process(proc: subprocess.CompletedProcess[str]) -> KeychainResult:
    stderr = (proc.stderr or "").strip()
    status = classify_exit(proc.returncode, stderr)
    return KeychainResult(status=status, returncode=proc.returncode, detail=stderr[:200])


def read_generic_password(
    service: str,
    account: str | None = None,
    *,
    keychain: Path | None = None,
    timeout: float = 5.0,
    run_fn: RunFn | None = None,
) -> tuple[KeychainResult, str | None]:
    """``security find-generic-password -w``: (classified result, secret or None).

    The secret is returned to the caller only; it is never part of the result.
    """
    argv = ["security", "find-generic-password", "-s", service]
    if account is not None:
        argv += ["-a", account]
    argv.append("-w")
    if keychain is not None:
        argv.append(str(keychain))
    runner = run_fn if run_fn is not None else subprocess.run
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return KeychainResult(status=PROMPT, returncode=None, detail=f"timed out after {timeout:g}s"), None
    except FileNotFoundError:
        return KeychainResult(status=UNAVAILABLE), None
    except OSError as exc:
        return KeychainResult(status=ERROR, detail=exc.__class__.__name__), None
    result = result_from_process(proc)
    if not result.ok:
        return result, None
    return result, (proc.stdout or "").rstrip("\n")


def quote_stdin_arg(value: str) -> str:
    """Quote one argument for ``security -i``'s line splitter.

    The splitter (SecurityTool ``split_line``) accepts a double-quoted argument
    with backslash escapes, and has no way to carry a newline or NUL.
    """
    if any(ch in value for ch in ("\n", "\r", "\0")):
        raise ValueError("security -i cannot carry newline or NUL characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def stdin_command_line(args: Sequence[str]) -> str:
    """One ``security -i`` input line (with trailing newline) for ``args``."""
    if not args:
        raise ValueError("empty security command")
    line = " ".join(quote_stdin_arg(str(arg)) for arg in args) + "\n"
    if len(line.encode("utf-8")) > STDIN_LINE_MAX:
        raise ValueError(f"security -i command too long ({len(line.encode('utf-8'))} bytes > {STDIN_LINE_MAX})")
    return line


def run_security_stdin(
    args: Sequence[str],
    *,
    timeout: float = 30.0,
    run_fn: RunFn | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one ``security`` subcommand through ``security -i`` on stdin.

    ``args`` excludes the leading ``security`` (e.g. ``["add-generic-password",
    "-s", svc, ..., "-w", secret]``). argv is only ``security -i``, so nothing
    secret is visible in ``ps``. The exit status is that of the one command.
    Raises ValueError when the command cannot be carried on one line.
    """
    line = stdin_command_line(args)
    runner = run_fn if run_fn is not None else subprocess.run
    return runner(
        ["security", "-i"],
        input=line,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
