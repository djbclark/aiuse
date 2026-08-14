# Documentation index

Use this page when you need more than the quick-start README. It groups the
project's guides by the job they help you do; most people only need one or two.

## Automation and agents

- [`json-contract.md`](json-contract.md) — stable `aiuse --json` fields and exit codes for scripts, cron, and agents.
- [`agent-api.md`](agent-api.md) — `aiuse serve` loopback HTTP API for long-lived local agents.
- [`shared-quota-semantics/`](shared-quota-semantics/) — language-neutral schemas, enums, pace formulas, and golden fixtures that another project can reuse without importing `aiuse`.
- [`shared-quota-semantics.md`](shared-quota-semantics.md) — rationale and design notes for the shared semantics package.
- [`companion-stack.md`](companion-stack.md) — menu-bar companions and `aiuse status` / `prompt` integrations.
- [`scheduling.md`](scheduling.md) — macOS LaunchAgent setup for hourly snapshots.
- [`history-learning.md`](history-learning.md) — snapshot retention and history-based learning.
- [`collector-concurrency.md`](collector-concurrency.md) — parallel collection and timeout behavior.

## Install, configuration, and trust

- [`packaging.md`](packaging.md) — installation channels and maintainer release flow.
- [`../packaging/install-deps.sh`](../packaging/install-deps.sh) — install/check optional source tools.
- [`collectors-caut-openusage.md`](collectors-caut-openusage.md) — caut and OpenUsage setup.
- [`macos-keychain-trust.md`](macos-keychain-trust.md) — `aiuse trust` and macOS Keychain prompts.
- [`../config/config.example.toml`](../config/config.example.toml) — annotated configuration example.
- [`../completions/`](../completions/) — bash and zsh completions.

## Provider and collector notes

- [`provider-identity.md`](provider-identity.md) — canonical provider id vs config key, and window identity across collectors that label the same window differently.
- [`cswap-reliability.md`](cswap-reliability.md) — Claude multi-account source reliability.
- [`opencode-go-quota.md`](opencode-go-quota.md) — authoritative OpenCode Go web data and shared quota behavior.
- [`opencode-zen-balance.md`](opencode-zen-balance.md) — Zen-balance source audit, SecretSpec setup, and Chrome credential refresh.
- [`source-coverage.md`](source-coverage.md) — which services currently have multiple live client sources and which do not.
- [`cursor-quota.md`](cursor-quota.md) — Cursor Included, Auto, Other Models, and on-demand pools.
- [`antigravity-pools.md`](antigravity-pools.md) — independent Gemini and Claude/GPT pools.
- [`tokscale-per-provider-investigation.md`](tokscale-per-provider-investigation.md) — current tokscale per-provider limitation.
- [`claude-local-usage.md`](claude-local-usage.md) — why local Claude activity is distinct from subscription quota.

## Product and implementation background

- [`pretty-display.md`](pretty-display.md) — terminal rendering choices.
- [`competitive-landscape.md`](competitive-landscape.md) — product positioning and comparable tools.
- [`next-options.md`](next-options.md) — optional future work and issue map.
- [`consumption-flexibility-plan.md`](consumption-flexibility-plan.md) — original scoring-design background.

## Maintainers and contributors

- [`../AGENTS.md`](../AGENTS.md) — repository orientation and current priorities.
- [`handoff.md`](handoff.md) — current session handoff and verification commands.
- [`fix-implementation-plan.md`](fix-implementation-plan.md) — completed review-derived implementation plan.
- [`quota-algorithm-audit-2026-08-01.md`](quota-algorithm-audit-2026-08-01.md) — pending implementation plan for issues #19–#22 (Cursor pool split, overage awareness, dual-debit investigation), with a full verified vendor-quota-structure appendix.
- [`code-review-2026-07-23.html`](code-review-2026-07-23.html) — adversarial review source material.
- [`review-workflow.js`](review-workflow.js) — reproducible review workflow.
- [`generate-readme-demo.py`](generate-readme-demo.py) — generates the README's synthetic output demo.
- [`memory/`](memory/) — thin project-memory index for compatible agent tooling.
