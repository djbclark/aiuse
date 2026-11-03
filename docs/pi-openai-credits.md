# Pi: OpenAI API credit accounting

Pi is provider `pi`, displayed with scope `codex-api`. It is independent of
Codex CLI subscription windows. OMP remains provider `omp`, scope `claude-api`.
This configuration describes Pi's OpenAI API funding, not every model Pi can run.

## Verified figures, not invented capacity

CodexBar's `openai` provider supplies an organization API spend meter, displayed
as Pi (`pi`) with scope `codex-api`. Existing CodexBar authentication is sufficient;
aiuse does not read its credential store. `aiuse --providers pi` queries CodexBar's
`openai` source, never the `codex` OAuth subscription source. Spend counts upward;
no remaining balance is fabricated, even if a spending limit is reported.

No balance is inferred from Pi credentials, conversation token totals, a Codex
subscription, or an unverified spending limit. A Pi **balance** appears only after
the operator records a verified grant and console balance. Missing data is not
an unlimited allowance. Hidden/disabled providers stay hidden.

For a real prepaid/promotional grant, record the console's figures and grant
start using `aiuse set-credits pi --remaining USD --total USD --granted DATE`.
Add `--expires DATE` only when an actual expiry exists. Dates accept ISO dates
or timestamps. Do not substitute a pay-as-you-go spending limit for a grant.
The console reading expires after seven days; API readings expire after 24 hours.
A newer verified Pi entry without `--expires` means **non-expiring**; it does not
inherit an earlier promotion's expiry. Equal or older entries cannot replace an
established reading or its grant clock.

Funding is currently provider-scoped, not bound to an account or organization.
If a snapshot contains multiple Pi rows, aiuse withholds the unbound balance from
**all** of them, including duplicate credentials for the same organization. It
never copies one grant into independently spendable pools. Use a single verified
funding context; multi-account grant binding is not supported by this format.

## Optional organization Costs API

Declare `OPENAI_ADMIN_KEY` through the managed credential setup, never in chat,
argv, a tracked file, or shell history. With an organization Admin API key and
verified grant parameters, refresh through managed environment injection:

```sh
sudo-secretspec run --reason "aiuse OpenAI cost report" -- aiuse refresh-credits pi
```

The [official Costs endpoint](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)
reports organization costs, **not remaining prepaid cash**. The displayed figure
is explicitly a conservative grant-derived estimate: the smaller of grant minus
reported costs and console balance minus costs in buckets ending after that
console reading. Overlapping daily buckets count in full; costs may also lag.
This can underestimate available credit and is not an exact live balance.
Other API clients spending the same organization's credit affect this estimate.

If funding is pay-as-you-go, the grant model does not apply. The CodexBar cost
meter still reports measured spend, but remaining capacity stays unknown until
an organizational budget cap is declared. Declare `monthly_spend_limit` under
`[plans.pi]` in `~/.config/aiuse/config.toml`:

```toml
[plans.pi]
monthly_spend_limit = 100
```

This anchors the cost meter against a monthly budget ceiling, displaying in the
`MONTH` matrix column with accurate percentage and pace instead of staying `n/a`.
When both a monthly spend limit and a verified credit grant are present, the spend
limit tracks the monthly budget in the `MONTH` column, while the credit grant tracks
remaining cash liquidity in the `OTHER` column.

The reader follows all pages and rejects malformed/non-USD costs, negative
adjustments needing reconciliation, repeated buckets/cursors, empty reports,
and redirects. A failed refresh preserves the previous reading, which keeps
aging rather than being made fresh by the failure. Keys and response bodies are
never logged. Credit files are atomically updated under a lock so OMP and Pi
refreshes do not overwrite one another. The same lock protects operator updates
and rejects API results whose console anchor changed while the request ran.
Same-anchor refreshes cannot increase the last accepted balance, even when costs
regress or a bucket's results become empty. A genuinely newer operator console
anchor can reconcile the balance upward. For both Pi and OMP, operator updates
must have a strictly later observation timestamp: an equal timestamp is not a
new observation, regardless of write order or whether the balance rises or falls.
Old/equal writers return the already accepted reading without overwriting it,
and oversized updates are rejected before replacing either pool.
Legacy Pi API readings without anchor provenance require a refresh when an
operator anchor is present. OMP retains its purchased-credit calibration rules.

## Verification

The `test` GitHub Actions workflow supports manual Linux runs with
`workflow_dispatch`, in addition to main pushes and pull requests. The Pi tests
use injected responses only; live billing still requires credentials and verified
funding details. No Codex subscription quota is used by these tests.
