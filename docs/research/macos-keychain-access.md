# macOS Keychain access patterns for version-resilient helpers

**Issue:** [#30](https://github.com/djbclark/aiuse/issues/30)
**Researched:** 2026-10-09, on macOS 27.0.1 (build 26A434, arm64).
**Companion docs:** [`../macos-keychain-trust.md`](../macos-keychain-trust.md)
(what `aiuse trust` does today) and
[`../macos-keychain-trust-plan.md`](../macos-keychain-trust-plan.md).
**Repro script:** [`macos-keychain-acl-repro.sh`](macos-keychain-acl-repro.sh)
(throwaway keychain only; never touches the login keychain).

## Summary

1. Two independent gates decide whether a read prompts: the item's
   **trusted-application list** (each entry is a path plus a code requirement)
   and its **partition list** (`apple-tool:`, `apple:`, `teamid:X`, `cdhash:X`).
   "Always Allow" only edits the first, so it cannot fix a partition miss.
2. A Developer ID build with a stable bundle identifier and team survives
   updates: the stored requirement is `identifier + team`, and the path is
   only a hint. Ad-hoc and linker-signed binaries (cargo, Homebrew-built
   formulae, local `.dev` builds) store a `cdhash` requirement and break on
   every rebuild.
3. Every item that trusts `/usr/bin/security` (what `gh`, Claude Code,
   go-keyring and `security add-generic-password` create by default) is
   readable without a prompt by any same-user process that runs the CLI.
   Adding `security` to an ACL is a broadening, not a fix.
4. The data protection keychain avoids ACL prompts but needs entitlements
   backed by a provisioning profile in an app-like bundle, and the `security`
   CLI cannot target it. That is out of reach for a Python CLI.
5. Recommendation: **enhance** `aiuse trust`, do not replace it. Add a
   read-only ACL audit before any read, repair partitions in place without
   touching secrets, keep secrets and passwords off argv, and snapshot the
   ACL before any recreate so it can be rolled back.

## 1. How the keychain decides

### 1.1 Two implementations, three APIs

Apple's [TN3137](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains)
(first published 2021-12-10, revised 2026-09-24) is the authority here:

| Implementation           | Access model                                                          | Reached by                                                                            |
| ------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| File-based (`login`)     | Per-item ACLs (`SecAccess`), trusted apps and partition IDs           | `SecKeychain*`, SecItem by default, the `security` CLI                                |
| Data protection keychain | Keychain access groups from entitlements, optional `SecAccessControl` | SecItem with `kSecUseDataProtectionKeychain`; Keychain Access shows it as Local Items |

Points from TN3137 that matter for aiuse:

1. The file-based keychain is "on the road to deprecation" but not deprecated.
   `SecKeychainCreate` was deprecated in the macOS 12 SDK.
2. SecItem against the file-based keychain runs through a shim with known
   limitations and bugs.
3. Data protection access groups come from entitlements that "must be
   authorized by a provisioning profile", so a command-line tool needs an
   app-like wrapper bundle. It only works in a user login context, never
   from a `launchd` daemon. A LaunchAgent runs in a user context.
4. Biometric protection (Touch ID) requires the data protection keychain.
   File-based ACL prompts ask for the keychain password instead. Apple's
   [access control lists](https://developer.apple.com/documentation/security/access-control-lists)
   page says that since macOS 10.13.1 the system ignores `promptSelector`
   and always asks for the keychain password before adding a trusted app.
5. "The keychain support in the `security` command-line tool is primarily
   focused on the file-based keychain."
6. **New in macOS 26.4:** a keychain file may depend on a protected entropy
   file in `/var/db/SystemKeys`. A copy of `login.keychain-db` alone may no
   longer unlock, which matters for backup and recovery (section 6).

### 1.2 Trusted applications: path plus requirement

Real metadata from this Mac's login keychain (`security dump-keychain -a`,
which prints ACLs and never secrets) shows what each ACL entry stores:

| Item (service)                | Trusted app as stored                                                                         | Stored requirement                                                                       | Partition list                                         |
| ----------------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `Codex MCP Credentials`       | `/opt/homebrew/Caskroom/codex/0.146.0/bin/codex` (missing)                                    | `identifier codex and anchor apple generic and … OU = "2DC432GLL2"`                      | `teamid:2DC432GLL2`                                    |
| `ai.meta.dev.credentials`     | `/usr/bin/security`, `~/.local/bin/muse-bin-1.4.2-R4684.1` (missing)                          | `com.apple.security`; `identifier "muse-arm64"` plus team                                | `teamid:V9WTTPBFK9, apple-tool:`                       |
| `Claude Code-credentials`     | `/usr/bin/security`                                                                           | `identifier "com.apple.security" and anchor apple`                                       | `apple-tool:`                                          |
| `gh:github.com`               | `/usr/bin/security`                                                                           | same                                                                                     | `apple-tool:`                                          |
| `com.steipete.codexbar.cache` | source-built `CodexBar.app`, `/Applications/CodexBar.app`, `CodexBarCLI`, `/usr/bin/security` | `cdhash H"8e5d…"` (fails: `CSSMERR_CSP_VERIFY_FAILED`); app and CLI by identifier + team | `apple-tool:, apple:, teamid:Y5PE65HELJ, cdhash:8e5d…` |
| `Cursor Safe Storage`         | `/usr/bin/security`, `/Applications/Cursor.app`                                               | team `VDXQ22DGB9` requirement                                                            | `teamid:VDXQ22DGB9, apple-tool:`                       |

What this shows:

1. **The path is a hint, the requirement is the identity.** The Codex and
   Muse entries point at versioned paths that no longer exist
   (status `-67068`, "cannot find code object on disk"). Newer builds signed by the same team still match the requirement.
   Apple DTS described this in 2018: the item is tagged with the creating
   app's designated requirement (DR), and "any future versions of that app
   must be able to satisfy that requirement"
   ([forum 98484](https://developer.apple.com/forums/thread/98484), Mar 2018).
2. **Ad-hoc builds store a cdhash.** The source-built CodexBar entry stores
   `cdhash H"…"`, which no other build can satisfy. The repro script shows
   the same for Homebrew's linker-signed `gh` (T2). Each "Always Allow" on an
   ad-hoc binary adds one more dead cdhash entry, as
   [hermes-agent #91115](https://github.com/NousResearch/hermes-agent/issues/91115)
   (Aug 2026) found for Electron `safeStorage` items.
3. **The partition list is a separate gate.** Apple DTS
   ([forum 746931](https://developer.apple.com/forums/thread/746931), Feb 2024):
   "Keychain partitioning, introduced in 10.12, prevents code from team A
   from accessing items created by code from team B." Items an app creates
   get `teamid:<its team>`. Items created by `/usr/bin/security` get
   `apple-tool:`. Changing a partition list needs the keychain password
   (`security set-generic-password-partition-list … -k`).

### 1.3 Which identity fields survive a normal signed update

| Change between versions                                               | Trusted-app match                         | Partition match  | Prompt?                                |
| --------------------------------------------------------------------- | ----------------------------------------- | ---------------- | -------------------------------------- |
| New version, same Developer ID team, same bundle identifier, new path | yes (DR)                                  | yes (`teamid:`)  | no                                     |
| Same path, new ad-hoc / linker-signed build                           | no (cdhash)                               | depends          | yes, every build                       |
| Bundle identifier changes (for example a `.dev` suffix)               | no                                        | yes if same team | yes                                    |
| Team changes (new owner, re-signed by a third party)                  | no                                        | no               | yes, and "Always Allow" does not stick |
| Another app rewrites the item's partition list                        | yes                                       | no               | yes, and "Always Allow" does not stick |
| Self-signed (non-Apple CA) stable cert, same identifier               | likely yes (DR is identifier + cert hash) | unverified       | see QUEUED item in section 7           |

The last two rows are live on this OS version:

1. [nextcloud/desktop #10976](https://github.com/nextcloud/desktop/issues/10976)
   (opened 2026-10-01, macOS 27.0.1, still open) reports the Nextcloud client
   rewriting `Claude Code-credentials` from `apple-tool:` to
   `teamid:NKUJUXUJ3B` at every start, apparently matching on account name.
   Claude Code reads through `/usr/bin/security`, so it then prompts in a
   loop. Nextcloud is not installed on this Mac.
2. A Claude Code report
   ([claudeissues 62361](https://claudeissues.com/issue/62361-bug-keychain-credential-partition-id-silently-resets-to-apple-tool-alone-on-5min),
   May 2026, macOS 26.4.1 and 26.5) saw a credential's partition list reset
   to `apple-tool:` while its trusted-app list survived. The reporter's
   workaround was a LaunchAgent re-applying the list with a password file,
   which trades one risk for another.
3. A self-signed certificate is what `aiuse trust setup` creates for caut.
   Apple DTS said in 2018 it had "no idea how well it works when you use a
   non-Apple CA". A self-signed cert has no team ID, so the partition token
   such a binary gets is not documented. This needs an on-device check.

## 2. How common helpers read and write

| Helper                    | Mechanism                                                                                                                                                                                                                                                     | Resulting ACL                                          | Version resilience                                                                                                                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `security` CLI            | `find-generic-password -w`, `add-generic-password`                                                                                                                                                                                                            | Creator `/usr/bin/security`, partition `apple-tool:`   | Apple-signed, so stable across OS updates; readable by any same-user caller                                                                                                                          |
| zalando/go-keyring (`gh`) | Shells out to `/usr/bin/security`. Writes go through `security -i` on stdin with `add-generic-password -U` and a base64 value ([source](https://github.com/zalando/go-keyring/blob/master/keyring_darwin.go))                                                 | Same as `security`                                     | Stable, because the caller is always `security`                                                                                                                                                      |
| jaraco `keyring` (Python) | ctypes `SecItemAdd` / `SecItemCopyMatching` without `kSecUseDataProtectionKeychain`, so file-based; set is delete then add ([source](https://github.com/jaraco/keyring/blob/main/keyring/backends/macOS/api.py))                                              | Creator is the Python interpreter binary               | Breaks when the interpreter is upgraded or rebuilt (uv, Homebrew); trusting it trusts every script it runs                                                                                           |
| Claude Code               | `Claude Code-credentials` via `/usr/bin/security` (this Mac)                                                                                                                                                                                                  | `/usr/bin/security`, `apple-tool:`                     | Stable unless another app rewrites the partition                                                                                                                                                     |
| Codex CLI                 | Native Security.framework (`Codex MCP Credentials`)                                                                                                                                                                                                           | `codex` binary by team DR, `teamid:2DC432GLL2`         | Survives official updates; the npm build under `node` or a re-signed build prompts ([linzumi repro](https://cdn.jsdelivr.net/npm/@linzumi/cli@1.0.179/scripts/qa/codex-keychain-partition-repro.md)) |
| Muse CLI                  | Native, plus `/usr/bin/security` already trusted                                                                                                                                                                                                              | team DR + `security`; `teamid:V9WTTPBFK9, apple-tool:` | Stable for aiuse's `security` read                                                                                                                                                                   |
| 1Password                 | The app keeps device keys in the login keychain. `op` talks to the app over IPC and asks for Touch ID per terminal process ([1Password community](https://www.1password.community/developers-69/avoiding-repeated-biometric-auths-in-editor-subshells-10515)) | No per-secret keychain items for `op`                  | No keychain ACL to break                                                                                                                                                                             |
| Electron `safeStorage`    | One "`<App>` Safe Storage" item holding an encryption key                                                                                                                                                                                                     | App DR; ad-hoc builds store cdhash                     | Breaks on ad-hoc rebuilds; deleting the item orphans every secret it encrypts                                                                                                                        |

## 3. The "Always Allow" trap

1. **Allowing `security`** adds an Apple requirement that every
   `security find-generic-password -w` call satisfies. After that, any
   same-user process, including a malicious npm postinstall script, reads the
   item silently. Many items already sit in this state by default (`gh`,
   Claude Code, Cursor tokens), so the user-account boundary is the real
   boundary for them.
2. **Allowing an interpreter** (`python3`, `node`, `bun`) trusts every
   script that interpreter runs, and the grant dies when the interpreter is
   upgraded.
3. **Allowing an ad-hoc binary** adds a cdhash that dies at the next build,
   and the dead entries pile up.
4. **Allowing when the partition list is the problem** changes nothing,
   because the click edits the trusted-app list only. The prompt returns
   every time.
5. **"Allow all applications" (`-A`)** sets the decrypt entry to
   `applications: <null>` (repro T5). Narrowing it again needs a recreate,
   because no CLI edits the trusted-app list in place.

## 4. What breaks between macOS versions

| Version           | Change                                                                                                                                                                                                                             | Source                                                                                                                                      |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 10.9              | Data protection keychain arrives on macOS with iCloud Keychain                                                                                                                                                                     | TN3137                                                                                                                                      |
| 10.12 (Sierra)    | Partition IDs; cross-team reads prompt even when the app is in the trusted list                                                                                                                                                    | [forum 746931](https://developer.apple.com/forums/thread/746931)                                                                            |
| 10.13.1           | `promptSelector` ignored; adding a trusted app always asks for the keychain password                                                                                                                                               | [Apple ACL docs](https://developer.apple.com/documentation/security/access-control-lists)                                                   |
| macOS 12 SDK      | `SecKeychainCreate` deprecated                                                                                                                                                                                                     | TN3137                                                                                                                                      |
| 26.4              | Keychain files may depend on `/var/db/SystemKeys` protected entropy files                                                                                                                                                          | TN3137, revision 2026-09-24                                                                                                                 |
| 26.5              | Reports of much more frequent partition-list resets on Claude Code items                                                                                                                                                           | [claudeissues 62361](https://claudeissues.com/issue/62361-bug-keychain-credential-partition-id-silently-resets-to-apple-tool-alone-on-5min) |
| 27.0.1 (observed) | A keychain made by `security create-keychain` stores version-256 records with no `partition_id` entry, and `set-generic-password-partition-list` exits 0 without adding one. Login-keychain records are version 512 and carry one. | repro T4, this Mac                                                                                                                          |

The last row means partition behaviour cannot be tested headlessly on a
throwaway keychain here, unlike the
[linzumi repro](https://cdn.jsdelivr.net/npm/@linzumi/cli@1.0.179/scripts/qa/codex-keychain-partition-repro.md),
which reports success on an earlier OS.

## 5. aiuse today versus the pattern

Found with `rg -n -i keychain` in this checkout (`main` at `09c129f`):

1. **Muse reader** (`src/aiuse/collectors/muse.py`,
   `_read_muse_keychain_access_token`). It runs
   `security find-generic-password -s ai.meta.dev.credentials -a meta -w` with
   a 5 s timeout. It is silent today only because the Muse CLI added
   `/usr/bin/security` and `apple-tool:` to the item. If a Muse update
   recreates the item without them, an hourly LaunchAgent run would raise a
   SecurityAgent prompt and time out. In a timed-out test call here, killing
   the client left no dialog window behind, but the row silently becomes
   empty. The same empty row results when the login keychain is locked
   (section 6.1, item 4).
2. **caut and CodexBar** are run as subprocesses and read their own items.
   `aiuse trust sign-caut` gives caut a stable self-signed identity, which
   fixes the cdhash problem for the trusted-app list. The partition side is
   unverified (section 1.3).
3. **`fix_codexbar_cache_account`** (`src/aiuse/macos_trust.py`) reads,
   deletes and re-adds each CodexBar cache item. Compared with the pattern:
   1. It passes the secret as `-w <secret>` and the keychain password as
      `-k <password>` on argv, where any same-user process can see them in
      `ps` for the life of the call. go-keyring and the RubyGems backend use
      `security -i` on stdin for this reason.
   2. It deletes before it adds. If the add fails, the item is gone and the
      secret exists only in Python memory. For a cache this is recoverable;
      for a credential it would not be.
   3. It adds `-T /usr/bin/security`, which broadens the item to every
      same-user process (section 3).
   4. It does not snapshot the old ACL, so there is nothing to roll back to.
4. **SecretSpec** (`aiuse credential refresh`) stores aiuse's own secrets
   through the privilege-separated `sudo-secretspec` path, not a login
   keychain item that aiuse's binary must be trusted on. It is outside this
   problem.

## 6. Recommended pattern

**Verdict: enhance the existing helpers.** A replacement only pays off as a
signed Swift helper app with a provisioning profile that uses the data
protection keychain. That would cover aiuse's own secrets but not the
provider-owned items in the login keychain, which are the ones that prompt.

### 6.1 Read path

1. **Preflight the ACL, read-only.** Before any `-w` read, parse
   `security dump-keychain -a` for that one item. It never decrypts and never
   prompts. Read only when the decrypt entry lists `/usr/bin/security` with
   `(OK)` and the partition list contains `apple-tool:`.
2. **Otherwise report, do not prompt.** Emit an actionable row such as "Muse
   keychain item no longer trusts `security`; run `aiuse trust audit`"
   instead of raising a dialog from a LaunchAgent.
3. **Prefer the owner's CLI** (`codexbar`, `caut`, `claude`) when one exists,
   so the owner's signed binary does the read under its own team partition.
4. **Classify failures; do not cache them as "no credential".** `security`
   exits with the low byte of the OSStatus. Observed here on 2026-10-09:

   | Exit | OSStatus                            | Meaning for a collector                                              |
   | ---- | ----------------------------------- | -------------------------------------------------------------------- |
   | 44   | `-25300` `errSecItemNotFound`       | item really missing: say "log in to the provider"                    |
   | 152  | `-60008` `errAuthorizationInternal` | keychain locked and no UI to unlock: retry later                     |
   | 124  | (from `timeout`)                    | a prompt was raised: run `aiuse trust audit`, do not retry in a loop |

   The 152 case appeared at 01:35 EDT when the screen locked during this
   run. From then on every `-w` read failed at once, `securityd` logged
   `MacOS error: -60008` from its SecurityAgent query, and `gh` reported its
   own keychain token as invalid. Attribute lookups without `-w` still
   worked. An overnight LaunchAgent therefore sees "locked" for hours, and
   the Muse collector currently caches that as an empty payload.

### 6.2 Audit (detection after updates)

Add `aiuse trust audit`, read-only, with no secrets in its output. For each
known item it reports:

1. Each trusted app's path, `(OK)` or failure status, and requirement kind
   (`cdhash`, identifier plus team, or Apple).
2. The partition list.
3. The current `codesign -d -r-` designated requirement of the binary that
   should own the item, and whether it matches the stored one.
4. Drift since the last snapshot. The snapshot file holds ACL metadata only
   and is mode 0600.

A brew `post_upgrade` hook, or the existing hourly run, can call it and send
one notice on change. Repair stays interactive and user-approved.

### 6.3 Repair, least invasive first

1. **Partition miss only:** `set-generic-password-partition-list` with the
   minimal set the stored list already had plus the missing team. It does
   not touch the secret or the trusted-app list (repro T4). Feed the keychain
   password through `security -i` on stdin, or let `security` prompt on the
   terminal, never `-k` on argv.
2. **Secret rotation:** `add-generic-password -U` without `-T` rewrites the
   value in place and keeps the ACL (repro T3). Adding `-T` to `-U` makes
   SecurityAgent ask for the keychain password, because it needs the
   item's `change_acl` right (observed 2026-10-09).
3. **Trusted-app change, only when needed:** recreate as a guarded swap:
   1. Snapshot the ACL metadata.
   2. Read the secret once into memory.
   3. Delete, then add with the full intended `-T` list, both through
      `security -i` on stdin.
   4. Verify by reading back through an already-trusted client.
   5. On any failure, re-add with the snapshot's trusted list, and say so.
4. **Never broaden by default.** No `-A`, and no `-T /usr/bin/security` unless
   the item already had it or the operator chose it for that item.

### 6.4 Rollback and recovery

1. **Broadened item** (`-A` or an extra `security` entry): recreate with the
   snapshot's `-T` list (repro T5). Keychain Access, item, Access Control
   also works by hand.
2. **Damaged partition list:** re-apply the snapshot's list with
   `set-generic-password-partition-list`.
3. **Lost item:** sign in again with the provider (OAuth re-login). This is
   why repair never deletes before the secret is safely in memory, and why
   it re-adds on failure.
4. **Whole keychain:** from macOS 26.4, restoring `login.keychain-db` may also
   need `/var/db/SystemKeys`. Carbon Copy Cloner backs up `~`, so check
   whether its system-volume task covers that directory.

### 6.5 Trade-offs

| Option                                      | Prompts after updates                           | Least privilege             | Works for a Python CLI / LaunchAgent      | Operational cost                                  |
| ------------------------------------------- | ----------------------------------------------- | --------------------------- | ----------------------------------------- | ------------------------------------------------- |
| Read through `security` (status quo)        | none while the item trusts `security`           | weak: any same-user process | yes                                       | low; breaks silently if a vendor drops `security` |
| Stable self-signed identity (`aiuse trust`) | none for trusted-app list; partition unverified | good                        | yes                                       | re-sign after each cargo install                  |
| Developer ID helper                         | none                                            | good                        | yes                                       | paid account, notarisation                        |
| Data protection keychain helper app         | none, Touch ID possible                         | best                        | only as an app-like bundle with a profile | high; cannot read existing login items            |
| "Allow all applications"                    | none                                            | none                        | yes                                       | low, but unacceptable for credentials             |

## 7. Open items

1. **Self-signed partition token (unverified).** Sign a test binary with
   `aiuse-local-codesign`, create an item from it in the login keychain,
   then read `partition_id`. That decides whether caut needs a partition
   repair after signing. It touches the login keychain and may prompt, so
   it needs the operator at the screen.
2. **OpenUsage on this Mac** is an ad-hoc signed local build
   (`com.robinebers.openusage.dev`, 0.7.0-dev, DR is a cdhash), not the
   Homebrew cask (0.7.14, not installed). That build re-prompts on every
   rebuild by design, so the symptom in #30 is expected for it. The release
   build described in #30 (Developer ID, team `QC3D3H67V9`) should survive
   updates. If it still prompts, check its items' partition list first.
3. **Prototype on a real item.** The throwaway-keychain repro covers ACL
   shape, `-U` and `-A`. It cannot cover partitions (section 4) or the "no
   prompt after update" claim, which needs a GUI session.

## Sources

All retrieved 2026-10-09.

1. Apple, [TN3137: On Mac keychain APIs and implementations](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains), revised 2026-09-24.
2. Apple, [Access control lists](https://developer.apple.com/documentation/security/access-control-lists).
3. Apple Developer Forums, [Keychain access prompt on app upgrade](https://developer.apple.com/forums/thread/98484), DTS, Mar 2018.
4. Apple Developer Forums, [Why don't my Apps receive unconditional access … with -T](https://developer.apple.com/forums/thread/746931), DTS, Feb 2024.
5. [nextcloud/desktop #10976](https://github.com/nextcloud/desktop/issues/10976), opened 2026-10-01.
6. [NousResearch/hermes-agent #91115](https://github.com/NousResearch/hermes-agent/issues/91115), Aug 2026.
7. [PsychQuant/che-keychain #11](https://github.com/PsychQuant/che-keychain/issues/11), Sep 2026.
8. [Claude Code partition reset report](https://claudeissues.com/issue/62361-bug-keychain-credential-partition-id-silently-resets-to-apple-tool-alone-on-5min), May to Jul 2026.
9. [linzumi Codex keychain partition repro](https://cdn.jsdelivr.net/npm/@linzumi/cli@1.0.179/scripts/qa/codex-keychain-partition-repro.md).
10. [zalando/go-keyring `keyring_darwin.go`](https://github.com/zalando/go-keyring/blob/master/keyring_darwin.go).
11. [jaraco/keyring macOS backend](https://github.com/jaraco/keyring/blob/main/keyring/backends/macOS/api.py).
12. [RubyGems `Gem::CredentialStore::MacOSBackend`](https://docs.ruby-lang.org/en/master/Gem/CredentialStore/MacOSBackend.html) (the `security -i` stdin pattern).
13. [1Password community: biometric auth per terminal process](https://www.1password.community/developers-69/avoiding-repeated-biometric-auths-in-editor-subshells-10515).
14. Local evidence on this Mac: `security dump-keychain -a` (ACL metadata
    only), `codesign -d -r-`, and the repro script's output, 2026-10-09.
