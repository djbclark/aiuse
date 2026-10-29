# AWS Bedrock month-to-date spend

Related: [`provider-identity.md`](provider-identity.md) ·
[`json-contract.md`](json-contract.md) · CodexBar `docs/bedrock.md`

Bedrock is not its own aiuse collector. CodexBar already reads AWS Cost
Explorer (`ce:GetCostAndUsage`, $0.01 per call) and, when a budget is set,
CloudWatch. aiuse asks CodexBar for that row and does not sign AWS requests
itself.

## When a new reading is worth $0.01

The operator does not use Bedrock alone, so other quota moving is the signal
that a fresh Bedrock number might have moved too.

1. The first collect with no stored reading always asks once, so the
   month-to-date figure appears.
2. A reading younger than six hours is held, even if other usage moved.
3. After six hours, a new call happens only when some other provider's
   balance, grant, or window percent differs from the fingerprint saved with
   the last success. Bedrock's own row is not part of that fingerprint.
4. A failed call is stamped too. The next try waits out the six hours. If an
   older spend is on disk, the board keeps showing it.

The hold is `~/.local/state/aiuse/bedrock-spend.json` (override
`AIUSE_BEDROCK_STORE`). It stores the dollars and the two timestamps, not the
CodexBar payload. The activity fingerprint is the previous
`~/.cache/aiuse/snapshots/latest.json` (override `AIUSE_BEDROCK_SNAPSHOT`).

CodexBar listing Bedrock as disabled does not skip it: the normal collect and
the watch `a` sweep both go through this gate. `[disabled_services]`
`bedrock` does skip it, and then the held number is not shown. The `a` sweep
does not waive the $0.01 cap.

## What the number means

The figure counts **up**. It is pay-as-you-go, not a prepaid wallet and not
part of a monthly plan grant. With no budget the row is `↑ $16.89 PAYG`.
With a budget it is `↑ $16.89 of $50.00 PAYG`. `@9:02pm` is when Cost
Explorer measured it. A held row stays fresh on the board (the hold is the
decision) and keeps that clock so the number stays honest.

A total of `$0.00` is not a measurement. Cost Explorer sometimes answers
with an empty total, and CodexBar reports that as `Spend: $0.00`. aiuse
shows `UNKNOWN` and does not store the zero as spend, so the next eligible
collect tries again. The row stays `payg_api`. It is never a prepaid balance.

JSON: `billing_kind` `payg_api`, `usage_credits.used` is the spend,
`usage_credits.limit` is the budget or null, `balance_usd` is null. `spent_usd`
is not this meter. That field is the expiring plan grant (`omp`). When the
reading is unknown, `error` is `UNKNOWN` and `usage_credits` is omitted.

There is no percent clock. A 0–100 bar would look like a plan window.
