# AWS account month-to-date spend

Related: [`provider-identity.md`](provider-identity.md) ·
[`json-contract.md`](json-contract.md) · [`omp-cloud-credits.md`](omp-cloud-credits.md)

The `bedrock` provider/config identity now displays **overall AWS account**
month-to-date charges before credits, as requested on 2026-10-29. The compact
row is `↑ $16.89 AWS MTD (as of …)` with no category, credit, or net breakdown.
It counts up and is not a prepaid balance.

## Billing source and authentication

The Bedrock path uses the installed AWS CLI and Cost Explorer, grouping
`UnblendedCost` by `SERVICE` and `RECORD_TYPE`. Service-only totals can hide
usage: $3.44721901 of Amazon Bedrock usage and -$3.44721901 of credit net to
zero. Earlier releases misclassified that zero as unavailable. The repaired
account total is $16.89 once the $13.44 Cost Explorer charges are included.
The row shows only that account total and its measurement time.
AWS may mark current-month totals estimated.

Existing AWS environment credentials or `AWS_PROFILE` are supported. On a
managed machine, `sudo-secretspec run` can inject the existing
`CODEXBAR_AWS_ACCESS_KEY_ID` and `CODEXBAR_AWS_SECRET_ACCESS_KEY` into the AWS
child process. Secret values are never command arguments or output. aiuse
does not install the CLI, authenticate a profile, or access a keychain.
The collector source remains `codexbar` so existing source pins keep working;
the native billing breakdown is identified by `bedrock_billing`.

## Query limits and cached readings

Cost Explorer charges per request. The first reading is fetched; subsequent
reads wait at least six hours and require another provider's usage to move.
Pagination can require more than one billed page. Failed attempts are held
too. A process lock prevents concurrent collectors from issuing duplicate reads.
`[disabled_services] bedrock` skips collection. Neither watch nor its
operator-only all-provider sweep waives the gate.

The store is `~/.local/state/aiuse/bedrock-spend.json`, overridable with
`AIUSE_BEDROCK_STORE`. `AIUSE_BEDROCK_SNAPSHOT` overrides the activity snapshot.
The store is scoped to the STS-verified AWS account and UTC billing month;
previous-month, different-account, or Bedrock-only dollars cannot be presented
as the current account total. An earlier Bedrock-only cache keeps its attempt
throttle but not its measurement during migration.
Legacy unscoped readings are not proof of a current total. An old attempt's
six-hour hold is retained during migration.

## Output contract

`billing_kind` remains `payg_api`; `usage_credits.used` is gross AWS account charges,
`balance_usd` remains null. `spent_usd` remains the expiring grant field, not
this meter. Additive `bedrock_billing` metadata carries `account_id`,
`billing_month`, `scope` (`aws_account`), `gross_usd`, signed `credits_usd`, `net_usd`, `estimated`,
and `read_at` (the original measurement time, retained on cached snapshots).
The metadata survives snapshot serialization and watch reuse.

A successful native billing response can establish genuine zero usage.
Unavailable data is not converted into zero. Legacy CodexBar zeros without
billing provenance remain `UNKNOWN`. No percent clock or burn score applies.

## Review of the preceding 48 hours (2026-10-29)

The bounded static review covered substantive changes through `c39c082`,
including billing/display, watch input, HTTP transport, OpenCode keys,
history counting and SIPB configuration. It did not independently validate
every release artifact or every documentation claim.

The omp duration fix only covered CodexBar's parser, while overrides replaced
that window and omitted its duration. The Bedrock releases alternated zero
and UNKNOWN presentation without inspecting credit offsets. A live STS check
confirmed the billing credentials and console refer to the same AWS account;
the earlier wrong-account explanation was unsupported.

Concurrent CodexBar HTTP workers also lost their ContextVar client; explicit
per-worker context copying corrects that transport regression. Separately,
SIPB's `ignore_expired_certificates` option sets `verify=False`, disabling
issuer and hostname verification too, including on bearer-authenticated
requests. That security finding is tracked separately as bead `aiuse-hap`.

The upstream billing defect is reported in
[CodexBar issue 4846](https://github.com/steipete/CodexBar/issues/4846).
