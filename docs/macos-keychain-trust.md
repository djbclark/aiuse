# macOS Keychain trust for caut (and CodexBar status)

**Status:** Implemented (`aiuse trust`).  
**Plan:** [`macos-keychain-trust-plan.md`](macos-keychain-trust-plan.md).

## Why “Always Allow” fails for caut

macOS Keychain **Always Allow** is bound to a binary’s **code identity**
(Designated Requirement / signing identity / CDHash), not just a path on disk.

| Binary | Typical install | Signature | Always Allow |
| ------ | --------------- | --------- | ------------ |
| **caut** | `cargo install` | **adhoc / linker-signed** | Breaks on every reinstall (new CDHash) |
| **CodexBar** | Homebrew cask app | Team-signed | Stable; different failure mode (item ACLs / OAuth strategy) |

There is **no** System Setting of the form “allow this app all keychain items
forever.” Grants are **per keychain item**.

Hourly LaunchAgent runs that shell out to adhoc caut will re-prompt until caut
is signed with a **stable** identity and you click Always Allow once per item.

## Quick start

```bash
# From a source checkout (recommended while developing):
cd ~/src/aiuse
AIUSE=.venv/bin/aiuse

# 0) Status (same as bare: aiuse trust)
$AIUSE trust
# or: $AIUSE trust status

# 1) Guided setup (opens Keychain Access if cert missing; signs if identity exists)
$AIUSE trust setup

# 2) Create the cert once in Keychain Access when prompted by setup:
#    Certificate Assistant → Create a Certificate…
#    Name: aiuse-local-codesign
#    Identity Type: Self Signed Root
#    Certificate Type: Code Signing
#    Optional: Trust → Code Signing → Always Trust

# 3) Sign caut (also after every cargo install)
$AIUSE trust sign-caut
# or: PATH="$PWD/.venv/bin:$PATH" just macos-sign-caut

# 4) Interactive probe — click Always Allow on each dialog
$AIUSE trust probe

# Status / docs helpers
$AIUSE trust grant-guide
$AIUSE doctor   # warns if caut is enabled and still adhoc
```

### After every `cargo install` / `install-deps`

```bash
aiuse trust sign-caut
```

`packaging/install-deps.sh` prints a one-line hint on Darwin. To autosign when
an identity already exists:

```bash
AIUSE_AUTOSIGN_CAUT=1 ./packaging/install-deps.sh
```

Default install stays non-interactive (codesign can prompt for the **signing
key**).

## Configure the identity name

Default name: `aiuse-local-codesign`.

```bash
export AIUSE_CODESIGN_IDENTITY="aiuse-local-codesign"
```

Or `~/.config/aiuse/config.toml`:

```toml
[macos]
codesign_identity = "aiuse-local-codesign"
```

Env wins over toml. This is a **name**, not a secret.

## Always Allow (once per item)

After a stable sign:

1. Run `aiuse trust probe` or `caut usage --provider claude --json`.
2. For each dialog, click **Always Allow** (not Allow Once).

Common item names (not exhaustive):

- `Claude Code-credentials`
- `Claude Safe Storage`
- `Cursor Safe Storage` / other Electron “Safe Storage” keys
- CodexBar cache items (`com.steipete.codexbar.cache`)

Or edit **Keychain Access → item → Access Control → +** and add the **real**
caut path from `aiuse trust status` (usually `~/.cargo/bin/caut`, not only the
symlink).

**Avoid** “Allow all applications to access this item” unless you accept any
local process reading that secret.

## CodexBar

CodexBar.app is Team-signed — **do not re-sign** it with a local cert.

### Two different CodexBar prompt classes

| Dialog mentions | Cause | Fix |
| --------------- | ----- | --- |
| **CodexBar Cache** / `com.steipete.codexbar.cache` | ACL trusts only the .app, not **CodexBarCLI** ([#679](https://github.com/steipete/CodexBar/issues/679)) | `aiuse trust fix-codexbar-cache` |
| **Claude Code-credentials** | Foreign item / XARA / OAuth prefs | CodexBar Settings (Avoid Keychain prompts); prefs already often `promptMode=never` |

### Fix CodexBar Cache ACLs (#679)

aiuse (and hourly LaunchAgent) invoke `codexbar` →  
`/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI`. Cache items must
list **both** the app and the CLI as trusted apps.

```bash
# Plan only (no writes)
aiuse trust fix-codexbar-cache --dry-run

# Rewrite all found accounts (prompts for login keychain password for partition-list;
# press Enter to skip partition-list — ACL -T alone is often enough)
aiuse trust fix-codexbar-cache

# One account only
aiuse trust fix-codexbar-cache --account cookie.codex
```

Optional non-interactive partition-list password (sensitive — avoid in shell history):

```bash
# prefer: type at getpass prompt instead
AIUSE_KEYCHAIN_PASSWORD='…' aiuse trust fix-codexbar-cache
```

Secrets are **never** printed. After a successful fix, verify:

```bash
codexbar usage --provider codex --json-only
```

**Caveat:** older CodexBar builds may rewrite cache items and drop CLI from the
ACL again. Upstream fixed new writes on main (`c66ea426`); upgrade when
available, re-run `fix-codexbar-cache` if prompts return.

### Claude OAuth prefs (read-only status)

`aiuse trust status` reports keychain-related prefs when readable
(`claudeOAuthKeychainPromptMode`, `claudeOAuthKeychainReadStrategy`, …)
from `~/Library/Preferences/com.steipete.codexbar.plist` (**read-only**).

## Doctor

On Darwin, when the **caut collector is enabled** and the binary is
adhoc/unsigned, `aiuse doctor` prints a **WARN** (soft — does not force exit 1
by itself). When caut is disabled in config, doctor stays quiet about codesign.

## Security notes

- Self-signed Code Signing is enough for **local** Always Allow; it is not
  Apple Developer ID / notarization.
- Re-sign after every rebuild; forgetting reverts to adhoc spam.
- Signing the **binary** `aiuse` / LaunchAgent invoke is sufficient; you do not
  need to codesign the LaunchAgent plist.
- This does **not** fix flaky caut Claude auth or incomplete provider strategies
  — only identity/dialog stickiness. See
  [`collectors-caut-openusage.md`](collectors-caut-openusage.md).

## Commands reference

| Command | Role |
| ------- | ---- |
| `aiuse trust` / `status` | Codesign status for caut + CodexBar Cache accounts |
| `aiuse trust setup` | Identity guide → sign if possible → grant-guide |
| `aiuse trust ensure-identity` | Cert creation steps (opens Keychain Access if needed) |
| `aiuse trust sign-caut` | `codesign --force --sign …` on realpath of caut |
| `aiuse trust grant-guide` | Keychain Access steps + CodexBar Cache notes |
| `aiuse trust probe` | Interactive caut (`both`) + light codexbar |
| `aiuse trust fix-codexbar-cache` | #679: trust CodexBarCLI on cache items (`--dry-run`, `--account`) |

just recipes: `macos-trust`, `macos-trust-status`, `macos-sign-caut`,
`macos-trust-guide`, `macos-fix-codexbar-cache`, `macos-fix-codexbar-cache-dry`.
