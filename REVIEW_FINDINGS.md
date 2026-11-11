# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/automated-live-trading-phases-0-3-spike`
- Reviewed HEAD: `65fc53c5b87d3af37ef7e67672cf299361b7b8a7`
- Subject: Add automated-live-trading Phase 0 docs, Phase 2-3 local controls and Phase 1 broker spike
- Claude commits reviewed: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: NEEDS_FIXES
- Highest priority: P2
- Finding count: 1

## Findings

### [P2] Investigate: strategy-control configuration is not captured in the persisted decision audit

Commit: 65fc53c5b87d3af37ef7e67672cf299361b7b8a7

Location: src/trading_research/paper_books/strategy_bridge.py:383-395

Concern: Risk decisions governed by the new per-strategy capital and risk budgets are persisted without identifying the strategy-controls configuration that produced them.

Evidence: The bridge applies `strategy_controls.capital_budget_usd` and `risk_budget_fraction` during allocation, but the resulting risk decision and order intent retain only `cfg.config_hash`, which is the paper-books configuration hash. The whole-file `StrategyConfig.config_hash` includes `strategy_controls`, but `run_strategy_paper_cycle` passes only the parsed controls mapping into the bridge. The signal’s existing `configuration_hash` covers its strategy-generation subsection, not these newly introduced operating controls. Consequently, changing a strategy budget or allowlist can change outcomes without changing the configuration identity attached to persisted paper risk/order records. ADR 0010 also describes pinned strategy/config versions and risk-limit hashes as part of the audit boundary.

Potential impact if confirmed: Historical approvals, quantity reductions, and rejections may not be reproducible from their persisted records. Operators could be unable to establish which strategy budget authorized a position, weakening accounting, incident investigation, and future activation evidence.

Investigation and conditional remediation: Verify the concern against the current code first by running identical stored signals under two `strategies.yaml` control configurations while keeping `paper_books.yaml` unchanged, then compare the persisted risk decisions, order intents, strategy links, and cycle report for a durable identifier of the controls used. If confirmed, persist an immutable strategy-controls or whole-strategy-config hash with the decision/link/intent audit chain and add regression coverage proving that control changes produce distinguishable, reproducible records. If the concern is disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Confirm that an accepted, resized, or rejected signal can be traced to the exact mode, activation stage, allowlist, capital budget, risk budget, and validity configuration used at evaluation time.

Tests or diagnostics run:

- `git diff --check 8c53e26035f5e1228861f9798ff8104e2b322a0d..65fc53c5b87d3af37ef7e67672cf299361b7b8a7` — reported one trailing blank line in the proposed ADR only; not a review finding.
- `.venv/bin/python -m pytest tests/unit/test_allocation.py tests/unit/test_strategy_controls.py -q` — could not start because the read-only environment had no usable temporary directory.
- Read-only inspection of the full commit diff and relevant strategy bridge, allocation, exposure, risk, lifecycle, configuration, persistence, tests, ADR, implementation plan, and requirements matrix.
- No files or external systems were modified or accessed.
