"""CLI entrypoint for the `aiuse` command.

Default output is a pretty human-readable report on stdout.
Use --json / --format json for machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from aiuse.__init__ import __version__
from aiuse.analysis.history import history_insights, save_snapshot, should_persist_snapshots
from aiuse.analysis.local_runtimes import maybe_local_runtime_alerts
from aiuse.analysis.suggest import format_suggestion_line, pick_suggestion, suggestion_to_dict
from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.collectors.base import which
from aiuse.collectors.runner import run_collectors
from aiuse.config import (
    DEFAULT_SUBPROCESS_TIMEOUT,
    collector_health_url,
    default_config_dir,
    default_config_path,
    generate_user_config,
    legacy_services_config_path,
    load_config,
    timeout_for,
    validate_config,
)
from aiuse.models import Snapshot, Urgency, UseOrLoseAlert, provider_display_name
from aiuse.report import render_report, render_status_line, render_stderr_meta
from aiuse.tty import restore_stdin_tty, save_stdin_tty

# External CLIs this project shells out to (must already be installed/auth'd).
# Version argv is a light probe only (no usage/auth API).
_EXTERNAL_TOOLS: tuple[tuple[str, str, list[str]], ...] = (
    ("cswap", "cswap", ["--version"]),
    ("codexbar", "codexbar", ["-V"]),
    ("caut", "caut", ["--version"]),
    ("openusage_ai", "openusage", ["--help"]),
    ("openusage_sh", "openusage-sh", ["version"]),
    ("tokscale", "tokscale", ["--version"]),
)
_PROBE_TIMEOUT_S = 5.0
_COMPLETIONS_DIR = Path(__file__).resolve().parents[2] / "completions"

# Exit codes for collect runs (doctor / generate-config use their own rules).
# 0 = ok, no actionable alerts · 1 = hard failure · 2 = ok, actionable alerts
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ALERTS = 2

_HELP_EPILOG = f"""\
config & setup:
  aiuse --generate-config     write defaults under ~/.config/aiuse/ (never overwrites)
  aiuse --show-config-path    print config.toml and legacy YAML paths
  aiuse doctor                PATH tools, version probe, config validation, timeouts
  aiuse trust …               macOS codesign / Keychain trust for caut (docs/macos-keychain-trust.md)
  aiuse status / prompt       one-line status for shell prompts / status bars
  aiuse suggest               single best pool to burn next (or nothing urgent)
  aiuse serve                 loopback HTTP API for agents (127.0.0.1 only)
  aiuse -t / --timeout SEC    force subprocess timeout for all tools this run
                           (default {DEFAULT_SUBPROCESS_TIMEOUT:g}s; also [timeouts] in config.toml)
  aiuse -q / --quiet          no progress on stderr (JSON stdout stays clean either way)
  aiuse --brief               alias of default priority-ladder report
  aiuse --full                long pretty report (per-provider, tips, detailed plan)
  aiuse --no-tui              classic plain-text report (skip Rich styling)
  aiuse --print-completion bash|zsh   shell completion script to stdout

exit codes (collect runs):
  0  success, no burn/conserve alerts
  1  hard failure (collectors failed and no accounts)
  2  success, but at least one burn/conserve alert

Credentials stay with cswap / CodexBar / caut / OpenUsage / tokscale — this CLI never stores tokens.
See docs/json-contract.md for machine-readable JSON field stability.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aiuse",
        description=(
            "Aggregate live AI subscription and API usage from cswap, "
            "codexbar, caut, OpenUsage, and tokscale; flag allotments that will reset unused. "
            "Default output is a pretty human-readable report; pass --json "
            "for machine-readable JSON."
        ),
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config",
        "-c",
        help=("Path to TOML/YAML/JSON config (default: $XDG_CONFIG_HOME/aiuse/config.toml)"),
    )
    p.add_argument(
        "--show-config-path",
        action="store_true",
        help="Print canonical config.toml and retired services.yaml paths, then exit",
    )
    p.add_argument(
        "--generate-config",
        action="store_true",
        help=(
            "Create default config files under ~/.config/aiuse/ (or $XDG_CONFIG_HOME/aiuse/). "
            "Creates missing directories; refuses to overwrite existing files"
        ),
    )
    p.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "Check tools on PATH (plus light --version probe), config validation, "
            "and timeouts; no usage collection (also: aiuse doctor)"
        ),
    )
    p.add_argument(
        "--status",
        action="store_true",
        help=("Print one-line status for prompts/status bars and exit (also: aiuse status / aiuse prompt)"),
    )
    p.add_argument(
        "--suggest",
        action="store_true",
        help=(
            "Print the single best burn recommendation (or nothing urgent); "
            "with --json includes top-level suggestion (also: aiuse suggest)"
        ),
    )
    p.add_argument(
        "--serve",
        action="store_true",
        help="Run loopback HTTP API for agents (also: aiuse serve). See docs/agent-api.md",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8787,
        help="Port for aiuse serve (default 8787, 127.0.0.1 only)",
    )
    p.add_argument(
        "--max-age",
        type=float,
        default=3600.0,
        metavar="SECONDS",
        help="Max age of cached snapshot for serve without ?refresh=1 (default 3600)",
    )
    p.add_argument(
        "--print-completion",
        choices=("bash", "zsh"),
        metavar="SHELL",
        help="Print shell completion script for bash or zsh and exit",
    )
    p.add_argument(
        "-t",
        "--timeout",
        type=float,
        metavar="SECONDS",
        help=(
            f"Default subprocess timeout in seconds for external tools "
            f"(default: {DEFAULT_SUBPROCESS_TIMEOUT:g}; also set in config.toml [timeouts])"
        ),
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--format",
        choices=("pretty", "json"),
        default="pretty",
        help="Output format (default: pretty human-readable report)",
    )
    fmt.add_argument(
        "--json",
        action="store_true",
        help="Shorthand for --format json (full snapshot + alerts)",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in pretty output (classic string path)",
    )
    p.add_argument(
        "--no-tui",
        action="store_true",
        help="Force classic plain-text pretty report instead of Rich styling",
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress messages on stderr (collecting…, snapshot saved, wrote path)",
    )
    p.add_argument(
        "--alerts-only",
        action="store_true",
        help="Only print use-or-lose recommendations (pretty text, unless --json)",
    )
    detail = p.add_mutually_exclusive_group()
    detail.add_argument(
        "--full",
        action="store_true",
        help=(
            "Pretty report: per-provider detail, cross-checks, tips, and detailed action plan (default is glance-first)"
        ),
    )
    detail.add_argument(
        "--brief",
        action="store_true",
        help="Alias of the default priority-ladder pretty report (kept for compatibility)",
    )
    p.add_argument(
        "--no-tokscale",
        action="store_true",
        help="Skip tokscale collector",
    )
    p.add_argument(
        "--no-cswap",
        action="store_true",
        help="Skip cswap collector",
    )
    p.add_argument(
        "--no-codexbar",
        action="store_true",
        help="Skip codexbar collector",
    )
    p.add_argument(
        "--no-caut",
        action="store_true",
        help="Skip caut collector",
    )
    p.add_argument(
        "--no-openusage-ai",
        "--no-openusage",
        action="store_true",
        help="Skip OpenUsage.ai collector (legacy --no-openusage alias)",
    )
    p.add_argument(
        "--no-openusage-sh",
        action="store_true",
        help="Skip OpenUsage.sh collector",
    )
    p.add_argument(
        "--providers",
        help=(
            "CodexBar providers: 'enabled' (default), 'all', or a comma-separated list queried one provider at a time"
        ),
    )
    p.add_argument(
        "--min-remaining",
        type=float,
        help="Override min remaining %% to flag (default 40)",
    )
    p.add_argument(
        "--max-days",
        type=float,
        help="Override max days-until-reset to flag (default 14)",
    )
    p.add_argument(
        "--save",
        metavar="PATH",
        help="Also write JSON snapshot to PATH (independent of stdout format)",
    )
    p.add_argument(
        "--traditional-summary",
        action="store_true",
        help="Use legacy flat summary format instead of the unified action plan",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    """Allow bare subcommand synonyms (``doctor``, ``status``, ``prompt``, …)."""
    if argv is None:
        # Mutate a copy of sys.argv[1:] so argparse still sees full process argv
        # only through parse_args; we pass an explicit list instead.
        raw = sys.argv[1:]
    else:
        raw = list(argv)
    if not raw:
        return raw if argv is not None else raw
    head = raw[0]
    if head == "doctor":
        return ["--doctor", *raw[1:]]
    if head in ("status", "prompt"):
        return ["--status", *raw[1:]]
    if head == "suggest":
        return ["--suggest", *raw[1:]]
    if head == "serve":
        return ["--serve", *raw[1:]]
    return raw if argv is not None else raw


def main(argv: list[str] | None = None) -> int:
    # Collectors may leave stdin without echo if a child TTY-mutates and dies;
    # always restore attrs we observed at entry (see aiuse.tty / run_json).
    saved_tty = save_stdin_tty()
    try:
        return _main_inner(argv)
    finally:
        restore_stdin_tty(saved_tty)


def _main_inner(argv: list[str] | None = None) -> int:
    # ``aiuse trust …`` has its own subcommands; handle before argparse.
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] == "trust":
        return _run_trust(raw[1:], config_path=None)

    args = build_parser().parse_args(_normalize_argv(argv))
    if args.show_config_path:
        print(f"config: {default_config_path()}")
        print(f"legacy services: {legacy_services_config_path()}")
        return 0
    if args.generate_config:
        return _run_generate_config()
    if args.print_completion:
        return _print_completion(args.print_completion)
    if args.doctor:
        return _run_doctor(config_path=args.config, timeout_override=args.timeout)
    if getattr(args, "serve", False):
        from aiuse.serve import run_serve

        return run_serve(
            port=int(args.port),
            config_path=args.config,
            max_age_seconds=float(args.max_age),
        )
    config = load_config(args.config)
    _apply_cli_overrides(config, args)

    as_json = bool(args.json) or args.format == "json"
    status_mode = bool(getattr(args, "status", False))
    suggest_mode = bool(getattr(args, "suggest", False))
    quiet = bool(args.quiet) or status_mode or suggest_mode

    def _progress(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr)

    # Progress stays on stderr so --json stdout is clean for piping
    _progress("Collecting usage from local tools…")
    snapshot = run_collectors(config)
    alerts = analyze_use_or_lose(snapshot, config)
    alerts.extend(maybe_local_runtime_alerts(snapshot, config=config))

    analysis_cfg = config.get("analysis") if isinstance(config.get("analysis"), dict) else {}
    if should_persist_snapshots(analysis_cfg):
        try:
            snapshot_path = save_snapshot(snapshot, alerts)
            _progress(f"Saved snapshot to {snapshot_path}")
        except OSError as exc:
            # Errors still surface even in quiet mode
            print(f"Warning: could not save snapshot: {exc}", file=sys.stderr)

    suggestion_alert = pick_suggestion(alerts)
    suggestion = suggestion_to_dict(suggestion_alert)
    insights = history_insights(snapshot, analysis_cfg=analysis_cfg)
    payload = {
        "snapshot": snapshot.to_dict(),
        "alerts": [a.to_dict() for a in alerts],
        "suggestion": suggestion,
        "history": insights,
    }
    cross_check_warnings = [check.to_dict() for check in snapshot.cross_checks if check.status == "warning"]

    if args.save:
        path = Path(args.save).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        _progress(f"Wrote {path}")

    exit_code = collect_exit_code(snapshot, alerts)

    if status_mode:
        print(render_status_line(snapshot, alerts))
        return exit_code

    if suggest_mode and not as_json:
        print(format_suggestion_line(suggestion_alert))
        return exit_code

    if as_json:
        if args.alerts_only:
            print(
                json.dumps(
                    {
                        "alerts": payload["alerts"],
                        "cross_check_warnings": cross_check_warnings,
                        "suggestion": suggestion,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            print(json.dumps(payload, indent=2, default=str))
        return exit_code

    # Pretty human-readable (default)
    color = False if args.no_color else None
    if args.alerts_only:
        for warning in cross_check_warnings:
            account = f" · account={warning['account']}" if warning["account"] else ""
            sources = " versus ".join(warning["sources"])
            print(
                f"[cross-check] {provider_display_name(str(warning['provider']))}"
                f"{account} · "
                f"{sources}: {warning['message']}"
            )
        for a in alerts:
            print(f"[{a.urgency.value}] {a.message}")
        if not alerts and not cross_check_warnings:
            print("No use-or-lose alerts or cross-check notes.")
        return exit_code

    from aiuse.tui import run_inline_report, should_use_tui

    if should_use_tui(
        as_json=False,
        alerts_only=False,
        no_tui=bool(args.no_tui),
    ):
        try:
            run_inline_report(
                snapshot,
                alerts,
                config=config,
                full=bool(args.full),
                brief=bool(args.brief),
                traditional_summary=args.traditional_summary,
                quiet=bool(args.quiet),
                color=color,
            )
            return exit_code
        except Exception as exc:  # noqa: BLE001 — fall back to classic text
            if not args.quiet:
                print(f"Warning: styled display failed ({exc}); using plain text.", file=sys.stderr)

    if not args.full and not args.quiet:
        print(
            render_stderr_meta(snapshot, alerts, color=color),
            file=sys.stderr,
        )
    print(
        render_report(
            snapshot,
            alerts,
            config=config,
            color=color,
            traditional_summary=args.traditional_summary,
            full=bool(args.full),
            brief=bool(args.brief),
        )
    )
    return exit_code


def collect_exit_code(snapshot: Snapshot, alerts: list[UseOrLoseAlert]) -> int:
    """Exit code for a completed collect + analyze run.

    * **0** — data collected (or empty without errors); no actionable alerts
    * **1** — hard failure: collectors reported errors and produced no accounts
    * **2** — success with at least one burn/conserve alert (not INFO/NONE)

    Cross-check disagreements alone do **not** change the exit code.
    """
    if snapshot.collector_errors and not snapshot.accounts:
        return EXIT_FAILURE
    if any(a.urgency not in (Urgency.INFO, Urgency.NONE) for a in alerts):
        return EXIT_ALERTS
    return EXIT_OK


def _run_generate_config() -> int:
    """Write default configs; never overwrite. Exit 1 if any path was skipped or errored."""
    result = generate_user_config()
    for path in result["created"]:
        print(f"created: {path}")
    for path in result["skipped"]:
        print(f"exists (not overwritten): {path}", file=sys.stderr)
    for msg in result["errors"]:
        print(f"error: {msg}", file=sys.stderr)

    if result["created"] and not result["skipped"] and not result["errors"]:
        print(
            f"Config directory ready: {default_config_path().parent}",
            file=sys.stderr,
        )
        return 0
    if result["created"] and result["skipped"] and not result["errors"]:
        print(
            "Some files already existed and were left unchanged. Remove or rename them if you want fresh defaults.",
            file=sys.stderr,
        )
        return 1
    if result["skipped"] and not result["created"] and not result["errors"]:
        print(
            "All default config files already exist; nothing written.",
            file=sys.stderr,
        )
        return 1
    if result["errors"]:
        return 1
    # No files defined edge case
    return 0


def _collector_enabled(config: dict[str, Any], name: str) -> bool:
    """Whether a collector is enabled (default True if omitted)."""
    collectors = config.get("collectors")
    if not isinstance(collectors, dict):
        return True
    entry = collectors.get(name)
    if entry is None:
        return True
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        return bool(entry.get("enabled", True))
    return True


def _path_status(path: Path) -> str:
    if path.is_file():
        return "present"
    if path.exists():
        return "exists but is not a regular file"
    return "missing (built-in defaults apply)"


def _http_probe_ok(url: str, *, timeout: float = 2.0) -> bool:
    """True when GET url returns 2xx (doctor / health_path preflight)."""
    try:
        import urllib.parse
        import urllib.request

        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        req = urllib.request.Request(url, headers={"Accept": "application/json, */*"})
        # The scheme and host are validated immediately above.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # nosemgrep
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:  # noqa: BLE001 — doctor probe only
        return False


def _openusage_http_ok(
    *,
    timeout: float = 2.0,
    config: dict[str, Any] | None = None,
) -> bool:
    """True when OpenUsage health probe (or default /v1/limits) answers."""
    url = collector_health_url(config, "openusage_ai") or "http://127.0.0.1:6736/v1/limits"
    return _http_probe_ok(url, timeout=timeout)


def probe_tool_version(
    cmd: str,
    version_argv: list[str],
    *,
    timeout: float = _PROBE_TIMEOUT_S,
    run_fn: Callable[..., Any] | None = None,
) -> tuple[bool, str]:
    """Run a light non-usage version probe. Returns (ok, summary)."""
    run = run_fn or subprocess.run
    try:
        proc = run(
            [cmd, *version_argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "not found"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:g}s"
    except OSError as exc:
        return False, str(exc)

    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    first = text.splitlines()[0].strip() if text else ""
    if proc.returncode != 0 and not first:
        return False, f"exit {proc.returncode}"
    if not first:
        first = f"exit {proc.returncode}"
    # Truncate noisy banners
    if len(first) > 80:
        first = first[:77] + "..."
    ok = proc.returncode == 0 or bool(text)
    return ok, first


def diagnose(
    config: dict[str, Any],
    *,
    which_fn=None,
    probe: bool = True,
    run_fn: Callable[..., Any] | None = None,
) -> tuple[int, list[str]]:
    """Build doctor report lines and exit code (0 ok, 1 problems).

    Pure enough for tests: pass ``which_fn`` / ``run_fn`` to stub PATH and probes.
    Does not collect usage. Version probe is optional and non-auth.
    """
    lookup = which_fn if which_fn is not None else which
    lines: list[str] = [f"aiuse doctor  (v{__version__})", ""]
    problems = 0

    config_path = default_config_path()
    legacy_path = legacy_services_config_path()
    lines.append("Config files")
    lines.append(f"  directory: {default_config_dir()}")
    lines.append(f"  config.toml:      {_path_status(config_path)} — {config_path}")
    lines.append(f"  services.yaml:    {_path_status(legacy_path)} — {legacy_path} (legacy; remove after migration)")
    lines.append("")

    issues = validate_config(config)
    lines.append("Config validation")
    if not issues:
        lines.append("  ok — no unknown keys or invalid timeouts")
    else:
        for issue in issues:
            lines.append(f"  {issue}")
            if issue.startswith("error:"):
                problems += 1
    lines.append("")

    lines.append("Timeouts (seconds)")
    timeouts_value = config.get("timeouts")
    timeouts = timeouts_value if isinstance(timeouts_value, dict) else {}
    force = timeouts.get("force")
    lines.append(f"  default: {timeout_for(config, 'default'):g}")
    lines.append(f"  force:   {force if force is not None else '(none)'}")
    for tool_key, _cmd, _va in _EXTERNAL_TOOLS:
        lines.append(f"  {tool_key}: {timeout_for(config, tool_key):g}")
    lines.append("")

    lines.append("External tools (PATH + light version probe; no usage/auth)")
    for collector_key, cmd, version_argv in _EXTERNAL_TOOLS:
        enabled = _collector_enabled(config, collector_key)
        path = lookup(cmd)
        if path:
            status = "ok"
            detail = path
            # openusage CLI --help can be heavy; skip version probe for it.
            if probe and collector_key != "openusage_ai":
                ok, summary = probe_tool_version(cmd, version_argv, timeout=_PROBE_TIMEOUT_S, run_fn=run_fn)
                if ok:
                    detail = f"{path} · {summary}"
                else:
                    detail = f"{path} · probe failed: {summary}"
                    # Probe failure is a warning, not a hard PATH problem
            # Optional health_path / probe_url when CLI is present too.
            health = collector_health_url(config, collector_key)
            if health and probe:
                if _http_probe_ok(health, timeout=_PROBE_TIMEOUT_S):
                    detail = f"{detail} · health ok ({health})"
                else:
                    detail = f"{detail} · health failed ({health})"
        else:
            status = "MISSING"
            detail = "not found on PATH"
            # OpenUsage can serve loopback HTTP without a PATH CLI.
            if collector_key == "openusage_ai" and enabled:
                health = collector_health_url(config, "openusage_ai") or "http://127.0.0.1:6736/v1/limits"
                if _openusage_http_ok(config=config):
                    status = "ok"
                    detail = f"CLI missing; HTTP probe ok ({health})"
                else:
                    detail = (
                        f"CLI not on PATH and HTTP probe failed ({health}) "
                        "(install OpenUsage.app + Settings→Command Line, or leave app running)"
                    )
                    problems += 1
            elif enabled:
                problems += 1
        flag = "enabled" if enabled else "disabled in config"
        lines.append(f"  {collector_key:<14} {status:<8} {detail}  [{flag}]")
    lines.append("")

    # macOS codesign: warn when enabled caut is adhoc (Keychain Always Allow).
    # Soft warning only — does not increment problems / exit 1.
    if sys.platform == "darwin":
        try:
            from aiuse.macos_trust import doctor_caut_codesign_lines

            trust_lines = doctor_caut_codesign_lines(
                config,
                which_fn=lookup,
                run_fn=run_fn,
                collector_enabled=_collector_enabled(config, "caut"),
            )
            if trust_lines:
                lines.append("")
                lines.extend(trust_lines)
        except Exception:  # noqa: BLE001 — doctor must not crash on trust helpers
            lines.append("")
            lines.append("macOS codesign (caut)")
            lines.append("  note     could not inspect codesign status")

    if problems:
        lines.append(f"Problems: {problems} issue(s) (missing tools and/or config errors).")
        lines.append("Install/authenticate tools, fix timeouts, or disable collectors in config.toml.")
        exit_code = 1
    else:
        lines.append("No hard problems detected for enabled collectors.")
        exit_code = 0

    lines.append("")
    lines.append("Hints")
    lines.append("  aiuse --generate-config   # create ~/.config/aiuse defaults (no overwrite)")
    lines.append("  aiuse --show-config-path  # print config file paths")
    lines.append("  aiuse trust setup         # macOS: stable codesign for caut / Keychain Always Allow")
    lines.append("  aiuse --full              # long report (per-provider + detailed plan)")
    lines.append("  aiuse --brief             # same as default glance-first report")
    lines.append("  aiuse --no-tui            # classic plain-text pretty report")
    lines.append("  aiuse -t 45               # force all tool timeouts for one run")
    lines.append("  aiuse --help              # full flag list + setup epilog")
    lines.append("  docs/json-contract.md  # stable JSON fields for scripts")
    return exit_code, lines


def _run_trust(trust_argv: list[str], *, config_path: str | None) -> int:
    """Dispatch ``aiuse trust`` subcommands (macOS collector codesign helpers)."""
    from aiuse.macos_trust import run_trust_command

    try:
        config = load_config(config_path)
    except SystemExit:
        config = {}
    return run_trust_command(trust_argv, config=config)


def _print_completion(shell: str) -> int:
    path = _COMPLETIONS_DIR / f"aiuse.{shell}"
    if not path.is_file():
        print(f"error: completion file not found: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def _run_doctor(*, config_path: str | None, timeout_override: float | None) -> int:
    """Print environment diagnosis; do not collect usage."""
    config = load_config(config_path)
    if timeout_override is not None:
        if timeout_override <= 0:
            print("--timeout / -t must be a positive number of seconds", file=sys.stderr)
            return 2
        timeouts = config.setdefault("timeouts", {})
        timeouts["force"] = float(timeout_override)
        timeouts["default"] = float(timeout_override)
    code, lines = diagnose(config)
    print("\n".join(lines))
    return code


def _apply_cli_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    collectors = config.setdefault("collectors", {})
    if args.no_tokscale:
        collectors["tokscale"] = {"enabled": False}
    if args.no_cswap:
        collectors["cswap"] = {"enabled": False}
    if args.no_codexbar:
        collectors["codexbar"] = {"enabled": False}
    if getattr(args, "no_caut", False):
        collectors["caut"] = {"enabled": False}
    if getattr(args, "no_openusage_ai", False):
        collectors["openusage_ai"] = {"enabled": False}
    if getattr(args, "no_openusage_sh", False):
        collectors["openusage_sh"] = {"enabled": False}
    if args.providers:
        collectors.setdefault("codexbar", {})["providers"] = args.providers
    analysis = config.setdefault("analysis", {})
    if args.min_remaining is not None:
        analysis["min_remaining_percent"] = args.min_remaining
    if args.max_days is not None:
        analysis["max_days_until_reset"] = args.max_days
    if getattr(args, "timeout", None) is not None:
        if args.timeout <= 0:
            raise SystemExit("--timeout / -t must be a positive number of seconds")
        timeouts = config.setdefault("timeouts", {})
        # CLI wins over config.toml per-tool keys (see timeout_for precedence).
        timeouts["force"] = float(args.timeout)
        timeouts["default"] = float(args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
