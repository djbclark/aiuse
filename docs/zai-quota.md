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
- Display id is `zai`.

MCP monthly time markers that CodexBar sometimes attaches as a 1-minute
`TIME_LIMIT` are not a third burn pool we invent — they stay on the weekly
slot when that is the time window the API sent.
