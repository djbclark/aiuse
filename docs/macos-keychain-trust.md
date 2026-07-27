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
# 1) Guided setup (prints cert steps, signs if identity exists, grant guide)
aiuse trust setup

# 2) Create the cert once in Keychain Access when prompted by setup:
#    Certificate Assistant → Create a Certificate…
#    Name: aiuse-local-codesign
#    Identity Type: Self Signed Root
#    Certificate Type: Code Signing
#    Optional: Trust → Code Signing → Always Trust

# 3) Sign caut (also after every cargo install)
aiuse trust sign-caut
# or: just macos-sign-caut

# 4) Optional interactive probe — click Always Allow on each dialog
aiuse trust probe

# Status / docs helpers
aiuse trust status
aiuse trust grant-guide
aiuse doctor   # warns if caut is enabled and still adhoc
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

If CodexBar re-prompts:

- Use CodexBar **Settings** (Avoid Keychain prompts / equivalent).
- `aiuse trust status` reports keychain-related prefs when readable
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
| `aiuse trust status` | Codesign status for caut + CodexBar |
| `aiuse trust setup` | Identity guide → sign if possible → grant-guide |
| `aiuse trust ensure-identity` | Cert creation steps only |
| `aiuse trust sign-caut` | `codesign --force --sign …` on realpath of caut |
| `aiuse trust grant-guide` | Keychain Access steps + item names |
| `aiuse trust probe` | Interactive light run (optional) |

just recipes: `macos-trust`, `macos-trust-status`, `macos-sign-caut`,
`macos-trust-guide`.
