# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/automated-live-trading-phases-0-3-spike`
- Reviewed HEAD: `61668730af922794d826f92cc6af09e8f68b9089`
- Subject: Mark review findings as fixed pending re-review (fix commit 0e6f286)
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7,0b570bf65a68738db7c48631068de82651bd1ac4,0e6f286d3911366bead442cb36be07607dc68282,f8b8086c6917a48bb9971fb5cb098a0cbfbdee1c,61668730af922794d826f92cc6af09e8f68b9089
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: 113a7da9fb9d8604b8b6ee7794dbc7baee8977e0
- Highest priority: P2
- Finding count: 0

## Findings

### [P2] Investigate: malformed strategy controls can silently enable unrestricted paper trading

Commit: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7

Location: src/trading_research/strategies/config.py:193-220

Concern: Malformed but falsey `strategy_controls` values may be silently replaced with permissive defaults instead of failing closed.

Evidence: `_parse_controls` uses `raw.get("strategy_controls") or {}`, `section.get(strategy_id) or {}`, and `entry.get("symbol_allowlist") or []` before validating types. Consequently, YAML such as `strategy_controls: []` defaults every known strategy to paper mode with no budgets or allowlist; `momentum_breakout: []` defaults that strategy similarly; and `symbol_allowlist: ""` becomes an unrestricted empty allowlist. The strict-loader regression cases cover truthy malformed values but not these falsey forms.

Potential impact if confirmed: An operator configuration error intended to disable or restrict a strategy could instead enable it for the local paper book without strategy-specific capital, risk, or symbol restrictions, producing unintended simulated exposure and invalidating paper-soak evidence used for later activation decisions.

Investigation and conditional remediation: Verify the concern against the current committed code first by loading configurations containing `strategy_controls: []`, a strategy entry of `[]`, and `symbol_allowlist: ""`, then inspect the resulting controls. Only if confirmed should the fixer distinguish absent or explicit empty mappings/lists from wrong-typed falsey values, reject malformed types before applying defaults, and add regression coverage for each falsey malformed form. If the concern is disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Confirm that omitted controls and valid empty mappings/lists retain their documented defaults, while falsey values of the wrong type fail closed and cannot produce unrestricted paper controls.

Tests or diagnostics run:

- Inspected every specified commit chronologically and traced its changes through HEAD `61668730af922794d826f92cc6af09e8f68b9089`.
- Reviewed the relevant committed code, tests, configuration, persistence schema and migrations, proposed ADR, implementation plan, and current runbook.
- `git diff --check 8c53e26035f5e1228861f9798ff8104e2b322a0d..61668730af922794d826f92cc6af09e8f68b9089` — reported only an extra blank line at the end of the proposed ADR; excluded as non-consequential.
- Pytest/Nox not run because the environment is read-only and cannot create required temporary files.
- No files, brokers, models, credentials, schedulers, orders, network services, or GitHub resources were modified or accessed.
