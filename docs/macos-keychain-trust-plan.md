# Plan: macOS collector trust (`aiuse trust`)

**Date:** 2026-07-27  
**Status:** Implemented (see [`macos-keychain-trust.md`](macos-keychain-trust.md)).  
**Inputs:** Operator caut keychain spam; dual second-opinion reviews (trim +
Python-first critique); local research on codesign/Keychain and CodexBar prefs.  
**Related:** [`collectors-caut-openusage.md`](collectors-caut-openusage.md),
[`packaging.md`](packaging.md), site-private
`memory/reference_macos_keychain_app_access.md` (operator Mac).

---

## Verdict (reconciled)

Ship a **trimmed operator QoL feature**, not a product surface. Diagnosis is
correct; non-goals are firm. Two reviews disagreed on **shell vs Python** and
on **auto-sign / CodexBar depth** — this plan picks:

| Decision | Choice | Why |
| -------- | ------ | --- |
| Primary UX | **`aiuse trust …` (Python)** | Doctor needs the same codesign parse; CLI already has synonym subcommands (`doctor`, `status`, …). Avoids brittle bash parsers of `codesign -d` stderr. |
| Packaging | One-line **hint** after caut install; **opt-in** autosign only | codesign itself can pop keychain dialogs for the **signing key**; install must stay non-interactive by default. |
| Identity creation | **Guided GUI only** (v1) | Certificate Assistant sets Code Signing EKU correctly; `security` certgen is fragile across macOS versions. |
| caut when adhoc | **Warn in doctor; do not auto-disable** | Behavior change would surprise operators who tolerate prompts. |
| CodexBar | **Status + docs + read-only prefs peek** | Different failure mode (Team-signed). No codesign “fix.” Detect known keys if present; never mutate. |
| State file | **None for v1** | Identity name lives in env / `config.toml` only. |
| Operator `services.yaml` | **Out of scope** | Re-enable caut locally only after trust is set up — not part of this PR. |

This is **operator tooling** that lives in-tree so install/docs/doctor stay
aligned — not a ranking feature.

---

## Problem (one paragraph)

macOS Keychain “Always Allow” binds to a binary’s **code identity** (Designated
Requirement / signing identity / CDHash), not a path alone. **caut** from
`cargo install` is **adhoc / linker-signed**; each reinstall gets a new CDHash,
so grants do not stick and hourly LaunchAgent runs re-prompt. **CodexBar** is
properly Team-signed; its dialogs are about item ACLs / OAuth keychain strategy
(upstream “Avoid Keychain prompts”), not missing signatures. There is **no**
global “trust this app for all keychain items.”

---

## Goals / non-goals

### Goals

1. Make a **stable local codesign identity** for caut easy to create (guided)
   and apply (`sign-caut`) so Always Allow can survive reinstalls.
2. Surface adhoc caut loudly in **`aiuse doctor`** when the collector is enabled.
3. Give CodexBar **status + educational grant-guide** (and best-effort prefs
   readout), without pretending codesign fixes CodexBar.
4. Linux / non-Darwin: no-op with a clear message; CI stays green.

### Non-goals

- Secrets in aiuse config; SIP/Recovery hacks; default “Allow all applications.”
- Fixing flaky caut Claude auth / incomplete provider strategies.
- Automated Keychain ACL surgery (`security dump-keychain -i` scripting).
- Non-interactive cert generation (`--create`) in v1.
- Changing default collector enablement or the operator’s local
  `services.yaml`.
- Signing the LaunchAgent plist (signing the **binary** `aiuse` invokes is
  enough; document that).

---

## Architecture

```
aiuse trust status|setup|sign-caut|grant-guide|probe   (macOS-focused)
        │
        ▼
src/aiuse/macos_trust.py   # pure helpers: resolve paths, parse codesign, sign
        │
        ├── used by cli trust commands
        └── used by doctor (Darwin section)

packaging/install-deps.sh   # after install_caut: hint, or opt-in autosign
justfile                    # thin wrappers for muscle memory
docs/macos-keychain-trust.md  # operator guide (GUI steps, Always Allow, CodexBar)
```

**Why Python not a standalone bash script:** both reviews agree doctor must
warn on adhoc caut. Review 2 is right that parsing `codesign -dv` twice (bash +
Python) is waste. Review 1’s packaging style is preserved via just + install-deps
hooks that call `aiuse trust …` when the installed CLI is available.

**Fallback if `aiuse` is not on PATH during install-deps:** print the same
one-line hint (do not hard-fail install).

---

## CLI surface (v1)

Synonym style consistent with `aiuse doctor`:

```text
Usage: aiuse trust [COMMAND]

Manage macOS codesigning and Keychain trust for collectors.

macOS remembers Keychain “Always Allow” by code identity. Cargo-installed
tools (caut) are adhoc-signed and lose grants on every reinstall. This
signs caut with a stable local Code Signing identity.

Commands:
  status       Codesign status of caut + CodexBar (exit 0 always)
  setup        Guided flow: ensure-identity steps → sign-caut if possible → grant-guide
  sign-caut    Force-sign the real caut binary with the configured identity
  grant-guide  Keychain Access steps + known item names (caut / CodexBar)
  probe        Optional: minimal caut/CodexBar run to surface Always Allow dialogs
               (interactive only — never from install-deps or LaunchAgent)

Environment / config:
  AIUSE_CODESIGN_IDENTITY     Preferred identity name
  config.toml [macos]
    codesign_identity = "aiuse-local-codesign"   # optional
  AIUSE_AUTOSIGN_CAUT=1       Opt-in: install-deps may call sign-caut after cargo install

Defaults:
  identity name: aiuse-local-codesign
```

On non-Darwin: print `macOS only` (or skip quietly under doctor) and exit **0**.

### Behavior details

| Command | Behavior |
| ------- | -------- |
| `status` | Resolve `caut` via PATH then `~/.cargo/bin/caut` / `~/.local/bin/caut` (realpath). Run `codesign -dv --verbose=4`; classify **adhoc / signed / missing**. Resolve CodexBar.app / `codexbar` CLI; report Team ID if any. Report configured identity name and whether `security find-identity -v -p codesigning` lists it. Optional read-only CodexBar prefs (below). Exit 0. |
| `setup` | Print identity steps if missing → if identity present, `sign-caut` → always `grant-guide`. **Does not** run `probe` (interactive education is separate). |
| `sign-caut` | Require Darwin + identity + binary. `codesign --force --sign "$IDENTITY" --timestamp=none` on **realpath** of binary (never sign a symlink inode as the target of trust without resolving). Print before/after summary. Non-zero on failure. |
| `grant-guide` | Static (versioned) list of item names + Keychain Access UI steps + Always Allow vs “Allow all applications” warning. |
| `probe` | Explicit only. Preamble + `caut usage --provider claude --json` and/or light codexbar probe. Not part of `setup` default path. |

### Doctor (Darwin section)

When platform is Darwin and **caut is enabled** and binary is present:

```text
[WARN] caut: Binary is ad-hoc signed (no stable identity).
       macOS Keychain "Always Allow" grants will not survive the next update.
       Fix: Run `aiuse trust setup` then re-sign after cargo install (`aiuse trust sign-caut`).
```

When signed with a non-adhoc identity: quiet **ok** line or omit.  
When caut **disabled**: no warn (optional dim note only if noisy — prefer silence).  
CodexBar: if enabled, optional info when prefs show aggressive keychain denial
(see below) — never exit 1 solely for CodexBar keychain prefs.

Doctor stays **non-interactive** (no probe, no codesign that needs key unlock
beyond read-only `codesign -d`).

---

## Codesign helpers (`macos_trust.py`)

Pure functions, unit-testable with fixtures (captured `codesign -dv` stderr):

- `is_darwin() -> bool`
- `resolve_caut_binary() -> Path | None` — PATH + known locations; **`Path.resolve()`**
- `resolve_codexbar_app() -> Path | None` — `/Applications/CodexBar.app`, etc.
- `codesign_display(path) -> CodesignInfo` — parse Authority, TeamIdentifier,
  flags/adhoc, Identifier from `codesign -dv --verbose=4` (stderr)
- `list_codesigning_identities() -> list[str]` — parse
  `security find-identity -v -p codesigning`
- `configured_identity(config) -> str` — env overrides toml overrides default
- `sign_caut(identity, path) -> None` — subprocess codesign
- `codexbar_keychain_prefs() -> dict | None` — best-effort, see below

**Symlinks:** always sign and report the **resolved** path
(`~/.cargo/bin/caut`), not the symlink path alone. Document that
`~/.local/bin/caut` should remain a symlink to that binary.

---

## Config

Optional only; missing = use default identity **name** for messaging, not
auto-create.

```toml
# ~/.config/aiuse/config.toml
[macos]
codesign_identity = "aiuse-local-codesign"
```

- No secrets.
- `validate_config` / known keys: add `macos` / `codesign_identity` so doctor
  does not warn unknown.
- `AIUSE_CODESIGN_IDENTITY` wins over toml when set.

---

## install-deps + just

**`install_caut()` after successful install:**

```text
note: macOS Keychain: after a stable Code Signing cert exists, run:
      aiuse trust sign-caut   # or: just macos-sign-caut
      First time: aiuse trust setup
```

If `AIUSE_AUTOSIGN_CAUT=1` **and** Darwin **and** identity appears in
`find-identity` **and** `aiuse` is runnable: call `aiuse trust sign-caut` and
surface failure as a **note**, not a hard install failure.

**just recipes** (thin):

```just
macos-trust:
    aiuse trust setup

macos-trust-status:
    aiuse trust status

macos-sign-caut:
    aiuse trust sign-caut

macos-trust-guide:
    aiuse trust grant-guide
```

---

## CodexBar (read-only)

Do **not** re-sign CodexBar.app. Status only + docs.

### Prefs research (this operator Mac, 2026-07)

Domain: `com.steipete.codexbar`  
File: `~/Library/Preferences/com.steipete.codexbar.plist`

Relevant keys observed (names only; values may change across app versions):

| Key | Example meaning (heuristic) |
| --- | --------------------------- |
| `claudeOAuthKeychainPromptMode` | e.g. `"never"` — reduced prompting |
| `claudeOAuthKeychainReadStrategy` | e.g. `"securityFramework"` vs CLI `security` |
| `debugDisableKeychainAccess` | bool kill-switch |
| `claudeOAuthKeychainDeniedUntil` | backoff timestamp after Deny |

**v1 behavior:** in `aiuse trust status` and optionally doctor, if plist is
readable, print a short **info** line for these keys (no secrets, no long
blobs). If keys are missing (older/newer app), skip silently. **Never write**
prefs.

Docs should still say: use CodexBar Settings for “Avoid Keychain prompts” /
equivalent; aiuse only reports.

---

## Docs to add/update

| File | Content |
| ---- | ------- |
| **`docs/macos-keychain-trust.md`** (operator guide, not this plan) | Why Always Allow fails for adhoc; create self-signed Code Signing cert (Certificate Assistant steps); `aiuse trust setup` / `sign-caut`; Always Allow once per item; re-sign after cargo install; CodexBar differences + Settings; no global app trust; security tradeoffs of “Allow all applications.” |
| **`docs/collectors-caut-openusage.md`** | Link to guide; note doctor warning; do not rehash full steps. |
| **`docs/macos-keychain-trust-plan.md`** | This plan (implementation checklist). |
| **README / AGENTS.md** | One line under setup + docs table row. |
| **`docs/next-options.md`** | Optional polish row: macOS trust helper (this). |

### Identity creation copy (ensure-identity / setup)

Print something like:

```text
Create a stable Code Signing identity (once per Mac):

  1. Open Keychain Access
     open -a "Keychain Access"
  2. Menu: Keychain Access → Certificate Assistant → Create a Certificate…
  3. Name:  aiuse-local-codesign   (or set AIUSE_CODESIGN_IDENTITY / config.toml)
     Identity Type: Self Signed Root
     Certificate Type: Code Signing
  4. Optional: double-click cert → Trust → Code Signing → Always Trust
  5. Optional: private key → Access Control → allow /usr/bin/codesign
  6. Verify:
     security find-identity -v -p codesigning

Then:
  aiuse trust sign-caut
  aiuse trust probe          # optional, interactive Always Allow clicks
```

### grant-guide item list (starting set)

Document as “commonly touched; not exhaustive”:

- `Claude Code-credentials`
- `Claude Safe Storage`
- `Cursor Safe Storage` / other Electron Safe Storage (if probing those providers)
- CodexBar-related cache items under `com.steipete.codexbar.cache` (app’s own)

---

## Implementation checklist (PR order)

1. [x] `src/aiuse/macos_trust.py` + unit tests with fixture `codesign` output  
2. [x] CLI: `trust` subcommands; non-Darwin exit 0  
3. [x] Config: optional `[macos] codesign_identity`; known-key validation  
4. [x] Doctor Darwin warn when caut enabled + adhoc  
5. [x] `docs/macos-keychain-trust.md` operator guide  
6. [x] `install-deps.sh` hint + opt-in `AIUSE_AUTOSIGN_CAUT`  
7. [x] just recipes  
8. [x] README + AGENTS.md + collectors doc links; next-options row  
9. [x] Full pytest (284); Darwin smoke: `aiuse trust status` / help  

No release cut unless operator asks (standing packaging policy).

---

## Testing strategy

| Layer | What |
| ----- | ---- |
| Unit | Parse adhoc vs Authority lines; resolve path with symlink tmp_path; configured identity precedence env > toml > default |
| CLI | `aiuse trust status` exit 0 on Linux (monkeypatch platform or skip body); doctor does not crash |
| Manual (Darwin) | Create cert once; sign caut; `codesign -dv` shows Authority; doctor clean; cargo reinstall + sign-caut; dialogs stop for granted items |

Do not run real `codesign --sign` in CI without an identity.

---

## Success criteria

1. First-time: `aiuse trust setup` + GUI cert + Always Allow on next caut run → hourly agent no longer spams for granted items.  
2. After `cargo install`: `aiuse trust sign-caut` (or opt-in autosign) restores identity without recreating the cert.  
3. Doctor warns only when caut is enabled and still adhoc.  
4. CodexBar: status/docs/prefs info; no false “re-sign CodexBar” advice.  
5. Linux CI unchanged (green, no macOS requirement).

---

## Explicitly deferred (v2+)

- Non-interactive cert create  
- Default autosign without `AIUSE_AUTOSIGN_CAUT`  
- Writing CodexBar prefs / enabling Avoid Keychain prompts programmatically  
- `security` ACL automation for Safe Storage items  
- Signing other cargo/go collector binaries if added later  
- State file of CDHash history  
- Re-enabling operator’s local `caut.enabled`

---

## Open residual risks

| Risk | Mitigation |
| ---- | ---------- |
| codesign prompts for access to the **signing private key** | Document Allow Always on that key for `/usr/bin/codesign`; never default autosign during install |
| Self-signed identity not trusted for codesign | Doc: Trust → Code Signing → Always Trust |
| Operator grants Always Allow then rebuilds **without** re-sign | Doctor warn returns; install-deps hint |
| CodexBar key names change | Best-effort prefs; never fail status |
| Probe from automation reintroduces dialogs | Probe is opt-in only; setup excludes it |

---

## Summary for implementers

Build **`aiuse trust`** in Python with shared **`macos_trust`** helpers used by
doctor. Guided cert creation, explicit `sign-caut`, grant-guide, optional probe.
Install path **hints** and only autosigns when `AIUSE_AUTOSIGN_CAUT=1`. Do not
disable caut automatically. Document that there is no blanket keychain trust
for an app — only stable identity + per-item Always Allow.
