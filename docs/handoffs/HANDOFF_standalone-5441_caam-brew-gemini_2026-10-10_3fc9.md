---
schema_version: 1
handoff_id: 3fc9
parent_handoff_ids: []
lineage: none
chain: [standalone-5441]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 8b8e3fa59c375134201dfaa5a5cdd61311e7eba4
created_at: 2026-10-10T08:33:17-0400
writer: grok
---

# Handoff — caam collector, brew formula, pipx selector

## The Goal

Get caam (coding_agent_account_manager 0.1.23) installed and vaulted for the
vendors it can actually read, teach aiuse to collect those vault quotas, and
make the command on PATH include that collector. Published Homebrew and PyPI
aiuse are still 3.3.7, which do not contain `aiuse.collectors.caam`.

## Where We Are

PATH `aiuse` and `ai` are `packaging/aiuse-path-select.sh` (commit `8b8e3fa`).
The script execs Homebrew only when that prefix contains
`collectors/caam.py`. It does not. It therefore execs the editable pipx
install `~/.local/bin/aiuse-src` / `ai-src`, whose `aiuse.collectors.caam`
loads from this checkout. `aiuse --version` still prints `aiuse 3.3.7`
because `pyproject.toml` was not bumped. `/opt/homebrew/bin/aiuse` is the
3.3.7 formula and has no caam module. A trace of the selector showed
`exec ~/.local/bin/aiuse-src`. shellcheck passed. The commit's pre-commit
hooks passed. No pytest run for this change.

Git: `main` at `8b8e3fa59c375134201dfaa5a5cdd61311e7eba4`, in sync with
`origin/main`, clean except this handoff file. site-private `master` at
`542e3ce` (the path note), in sync. Homebrew tap `~/src/homebrew-aiuse` at
`4a67896`, clean. That tap has the browser-cookie3 / lz4 / pycryptodomex
resources and still urls the v3.3.7 tarball.

The caam collector itself landed earlier as `075f656` (`collect_caam`,
priority immediately before `acp` for claude, codex, grok, and cursor).
`packaging/release.py` no longer runs `pipx upgrade` (`2d5b13d`). The
formula resources for Chrome cookie refresh are `cdc9446`. No `just release`
was cut.

caam 0.1.23 is `~/.local/bin/caam`. Six vault profiles (claude, codex, grok,
gemini, cursor, opencode). No agy profile: the antigravity oauth file is
absent. The daemon is not running. Live `caam limits` and a live `aiuse`
collect were not run.

The zcode collector dispatch is finished. Report:
`~/.local/state/bigteam/caam-aiuse/collector-report.md` (`.done` exists,
status done, exit 0). Owner is empty. Do not re-dispatch it.

## What We Tried

A non-editable pipx install from this checkout, frozen at 3.3.7, sat ahead of
Homebrew on PATH and had no `aiuse.collectors.caam`. Uninstalling it and
symlinking `~/.local/bin/aiuse` at the Homebrew binary made formula tests
honest and still left PATH without the collector, because the 3.3.7 cellar
has no `collectors/caam.py`. Delegating pipx at that binary cannot satisfy
the collector requirement.

`pipx upgrade` during release was removed so a release cannot put that stale
copy back. Putting browser-cookie3 in the formula as resources (no bottle)
kept Chrome refresh on the Homebrew install without returning the whole
install to pipx.

## Key Decisions

Chosen:

- pipx includes the caam collector, because the installed Homebrew aiuse
  cannot. Implementation: editable `pipx install --editable --suffix=-src`
  of this checkout, plus `pipx inject aiuse-src 'browser-cookie3>=0.19'`.
  `packaging/aiuse-path-select.sh` uses that pipx install until Homebrew's
  site-packages contain `collectors/caam.py`, then switches by itself.
- PATH stays the name the LaunchAgent and site_agents already call
  (`~/.local/bin/aiuse`). The selector does not call `brew`.
- Do not `pipx upgrade` the `aiuse-src` venv. That would replace the
  checkout with PyPI 3.3.7 and drop the collector.
- Do not cut a release unless the operator says `just release`.
- caut stays. caam is an extra lower-priority source, not a replacement.
- Gemini: one live oauth refresh, then the same JSON copied onto the vault
  profile. Filed
  https://github.com/Dicklesworthstone/coding_agent_account_manager/issues/123
  . Do not refresh either side again. The vault and the live file share one
  refresh token.
- browser-cookie3 stays a Homebrew formula resource.
- Do not add caam to `djbclark-ade/docs/apply-toolchain-prompt.md`.

Rejected:

- Leaving PATH as a symlink to Homebrew 3.3.7. That binary has no collector.
- Reinstalling non-editable pipx 3.3.7 from PyPI. Same gap.
- A `brew --prefix` call inside the selector. A Homebrew lock would stall
  the sampler. The script globs the opt prefix instead.
- Starting the caam daemon, or running caam refresh / activate / logout /
  next / run.

## Evidence & Data

- Selector trace, 2026-10-10: Homebrew prefix is executable, zero
  `collectors/caam.py` matches, exec is `~/.local/bin/aiuse-src`.
- pipx venv Python 3.14.8. `aiuse.collectors.caam.__file__` is
  `/Users/djbclark/src/aiuse/src/aiuse/collectors/caam.py`.
  `browser_cookie3`, `lz4`, and `Cryptodome` import in that venv.
- Cellar Python for 3.3.7: `importlib.util.find_spec("aiuse.collectors.caam")`
  is None.
- Gemini refresh was one HTTP 200 against the Google token endpoint around
  07:40 EDT, `expires_in` 3599, refresh token not rotated. By this handoff
  (~08:33 EDT) that access token should be treated as expired. Nothing on
  PATH refreshes it. `command -v gemini` is empty.
- Codex access was refreshed once earlier (about 10 days of access at
  10:48Z). Do not repeat it. The Pi agent auth file is a stale copy of the
  same account. Do not refresh it.
- Collector count in the tree is 18. JSON schema was not bumped.
- Tests this session: `tests/test_release_script.py` 19 passed earlier
  (release script no longer pipx-upgrades). Caam unit tests passed before
  `075f656`. Full `just ci` was not run. This selector change: shellcheck
  clean, pre-commit on the three staged files passed (typos, prettier,
  markdownlint, semgrep, gitleaks).

## Operator Feedback

- PATH must exercise the Homebrew formula when that formula can do the job.
  It must not be a stale pipx copy of 3.3.7.
- pipx should include the caam collector unless it can use the installed
  Homebrew aiuse. Homebrew 3.3.7 cannot, so the selector uses pipx.
- Drop `pipx upgrade` from the release script.
- browser-cookie3 must survive installs. Formula resources, not a one-off
  cellar pip install, and not a return to pipx for the whole app.
- File the Gemini health bug and fix the profile once. Both done. Do not
  refresh again.
- Leave `apply-toolchain-prompt.md` unchanged.
- Releases only when explicitly requested.

## Where We're Going

1. Do not release and do not refresh caam or Gemini. PATH already runs the
   caam collector via `~/.local/bin/aiuse-src`. When the operator asks,
   from `/Users/djbclark/src/aiuse` run `just release` for the next patch
   so the Homebrew formula gains `collectors/caam.py`. The selector then
   switches to `/opt/homebrew/opt/aiuse` with no further PATH edit.
2. Before that release, run `~/ops/site-private/bin/bg just ci` in
   `/Users/djbclark/src/aiuse`. Full pytest has not run this session.
3. Leave the Gemini access token alone. Issue:
   https://github.com/Dicklesworthstone/coding_agent_account_manager/issues/123
   . There is no `gemini` binary. The vault profile and the live oauth file
   share one refresh token.
4. Do not run `caam refresh`, `keepalive`, `pool refresh`, `activate`,
   `clear`, `logout`, `next`, or `run`, and do not start the daemon.
   `~/ops/site-private/memory/reference_caam_vault_shares_refresh_family_2026-10-10.md`.
5. Do not `pipx upgrade aiuse` or `pipx uninstall aiuse-src`.
   `docs/packaging.md` and
   `~/ops/site-private/memory/reference_aiuse_path_pipx_lacks_caam_collector_2026-10-10.md`.
6. Do not add caam to `~/src/djbclark-ade/docs/apply-toolchain-prompt.md`.
7. `packaging/install-deps.sh` `install_caam` was not executed live. caam
   0.1.23 is already installed from the checksum tarball.
8. Do not refresh `~/.pi/agent/auth.json`.

## Detached jobs

none owned by this session.

The caam collector slice is already finished and was reviewed before this
handoff. Job file
`~/.local/state/bigteam/caam-aiuse/jobs/collector.json` (status done, exit 0,
owner empty, no `closed` key). Report
`~/.local/state/bigteam/caam-aiuse/collector-report.md`. Done marker
`~/.local/state/bigteam/caam-aiuse/collector-report.md.done` exists. Do not
re-dispatch. Other bigteam jobs under that state directory belong to other
sessions.

## Quick Start

```bash
cd /Users/djbclark/src/aiuse
git rev-parse HEAD    # expect 8b8e3fa or a child of it
~/.local/bin/aiuse --version
# selector must exec aiuse-src until this file exists:
# /opt/homebrew/opt/aiuse/libexec/lib/python*/site-packages/aiuse/collectors/caam.py
ls /opt/homebrew/opt/aiuse/libexec/lib/python*/site-packages/aiuse/collectors/caam.py
~/.local/pipx/venvs/aiuse-src/bin/python -c 'import aiuse.collectors.caam as c; print(c.__file__)'
```

Do not run `aiuse` collect or `caam limits` just to orient. Both present
vault access tokens. Read `docs/caam.md` and `src/aiuse/collectors/caam.py`.
