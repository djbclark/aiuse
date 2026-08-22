# QwenCloud quota

QwenCloud (qwencloud.com) is Alibaba's model-studio platform behind Qwen
models. It sells three relevant things, and `aiuse` maps each to the band it
behaves like:

| Product                          | aiuse band           | Reset semantics                           |
| -------------------------------- | -------------------- | ----------------------------------------- |
| Coding plan (lite/standard/pro)  | subscription windows | 5h + weekly + monthly credit windows      |
| Token plan (Team/personal seats) | prepaid credit pool  | Credits roll until spent                  |
| Pay-as-you-go + billing limit    | PAYG `n/a` row       | Spend counts up toward operator-set limit |

## Source: the `qwencloud` CLI (native)

The collector runs one primary call, `qwencloud usage summary --format json`,
which returns `{coding_plan, token_plan, pay_as_you_go, free_tier}` in one
payload, plus a best-effort `qwencloud billing limit --format json` for the
PAYG cap.

Setup is one-time OAuth — **there is no API-key mode** (verified against CLI
v1.3.0):

```bash
qwencloud auth login        # PKCE (browser) with device-flow fallback
qwencloud auth status       # {"authenticated": true, ...}
```

Credentials live in the macOS Keychain (service `qwencloud-cli`,
`QWENCLOUD_KEYRING=plaintext` falls back to a file). If login state is lost
the collector surfaces the CLI's error — re-run `qwencloud auth login`.

Window mapping (`coding_plan.windows`):

| CLI key   | Label          | Nominal minutes |
| --------- | -------------- | --------------- |
| `per_5h`  | `qwen 5-hour`  | 300             |
| `weekly`  | `qwen weekly`  | 10080           |
| `monthly` | `qwen monthly` | 43800           |

Each CLI window carries `{remaining, total, used_pct, next_reset_at}` in
credits. The same credits burn across all three windows (nested, like
ClinePass/OpenCode Go), so `shared_allotment`-style reading applies: a low
5-hour % is not a separate pool.

Per-model **free-tier** quotas (e.g. 10k TTS characters) are intentionally
ignored — hundreds of rows, none use-or-lose subscription allotments.

## Second source: CodexBar `qwen-cloud`

CodexBar ships a `qwen-cloud` provider that reads QwenCloud session cookies
from Chrome (it errors with "No Qwen Cloud session cookies found" until you
sign in to qwencloud.com in Chrome and allow CodexBar's Keychain access). It
reports canonical provider id `qwencloud`, identical to the native collector,
so the two cross-check when both are enabled. Source priority is
`qwencloud` (native CLI) over `codexbar`.

## Prior art

- [CodexBar issue #2328](https://github.com/steipete/CodexBar/issues/2328) —
  end-to-end curl recipe for the personal Token Plan API
  (`cs-data.qwencloud.com`, cookie auth, consumed-percent + epoch-ms resets).
- [QwenUsage](https://github.com/PeachGumi/QwenUsage),
  [QwenBar](https://github.com/jzbora/QwenBar) (via
  [bailian-cli](https://www.npmjs.com/package/bailian-cli)) — menu-bar apps
  on the same endpoints.
- [ccusage](https://ccusage.com/guide/qwen/) and
  [Qwen-usage-tracker](https://github.com/Momi1370/Qwen-usage-tracker) —
  local-JSONL token analytics (`~/.qwen/usage/token-usage-*.jsonl`), i.e.
  activity, not subscription quota; see
  [`claude-local-usage.md`](claude-local-usage.md) for why aiuse keeps those
  distinct.
- Qwen Code OAuth free tier (portal.qwen.ai, 2000 req/day) had **no quota
  API** (qwen-code#331 closed unimplemented) and was discontinued
  2026-04-15; not tracked.
