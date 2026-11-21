# z.ai (GLM coding plan) quota

z.ai Lite/Pro/Max coding plans are a **subscription** with two nested credit
windows, not a prepaid dollar wallet.

Official [usage table](https://docs.z.ai/devpack/overview):

| Plan | 5-hour credits | Weekly credits |
| ---- | -------------- | -------------- |
| Lite | 2,000          | 10,000         |
| Pro  | 12,000         | 60,000         |
| Max  | 28,000         | 140,000        |

5-hour credits refresh five hours after they are consumed (so a **fresh 0%
used** bar often has **no `resetsAt`** — that is correct). Weekly credits
activate at subscribe and reset every 7 days.

`analysis.provider_overrides.zai.shared_allotment` is **true**. Lite list
price used for value-at-risk is **$18/mo** (promos exist).

## What `aiuse` shows

- CodexBar `api` → `z.ai 5-hour` and `z.ai weekly` (a third `z.ai session`
  slot only if the API sends two token windows).
- Plan label comes from the API (`lite` on this machine).
- CodexBar’s synthetic `zai-codexbar-api-key` account name is dropped; ACCT
  is `—` unless a real identity is present.
- Display id is `zai/zcode` (canonical provider id stays `zai`): Zcode uses
  Z.ai by default, so the row names the default client for this quota.
  Crush (uninstalled 2026-10-08) is not the Z.ai client either.

## No coding plan

If z.ai answers `当前用户不存在coding plan` ("the current user has no coding plan"),
the account has no active plan: lapsed, cancelled or never bought, or the key
belongs to another account. CodexBar passes the text through, and the row reads
`z.ai: this account has no coding plan` (the original is kept in the
row's `notes`). Nothing in aiuse is wrong; renew or fix the key at z.ai, or list `zai`
under `[disabled_services]` to stop checking. Seen 2026-11-20 at about 22:20Z,
ten minutes after a normal reading (5-hour 0%, weekly 20% used). A live chat
call with the vault key on the coding endpoint (2026-11-21) answered HTTP 429
code 1113 "Insufficient balance or no resource package": a missing plan, not an
invalid key.

MCP monthly time markers that CodexBar sometimes attaches as a 1-minute
`TIME_LIMIT` are not a third burn pool we invent — they stay on the weekly
slot when that is the time window the API sent.
