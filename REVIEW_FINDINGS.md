# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `0d053969f9b2315181b20f2c4d0f1f485d2b90bc`
- Subject: Record PR 43 round-5 fix pending re-review
- Claude commits reviewed: 9578517a701754a82f7148a4d44be53d43171adc,4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0,32dd766ce8f292aebbb5cbab8b304904398b3d50,14c77d1881f2dd61bfbe03d713e376e310d1f15a,5c8062a4a18ca2ea579db379cbfd3d10fcb0168c,3c2b311c56757ec0e0ba1f4ddcd4f8b9edebaf83,3dc8d4d4912e4aa6f2150760183187d277fdc185,a5515c9aef3a6a80f278cf6e8d5ce563e4456233,534bfa1ad58025cc6adab4a334f0639a74c52c47,b0afdd6d0388e161277d9baa435b12c157e78cae,0d053969f9b2315181b20f2c4d0f1f485d2b90bc
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: 99d6457279d3c07f5244ba4f456ada0e3544c408
- Highest priority: P1
- Finding count: 0

## Findings

### [P1] Investigate: truncated data discards an already-filled position as an unfilled order

Commit: b0afdd6d0388e161277d9baa435b12c157e78cae

Location: src/trading_research/backtesting/intraday_engine.py:634-653, 793-797

Concern: A truncated timeline may remove an executed position from performance results instead of invalidating the backtest.

Evidence: `_simulate` records the buy before evaluating the remaining timeline. If data ends before the exit horizon, it returns `_Outcome(unfilled_reason=INCOMPLETE_SESSION_DATA)`, discarding those buy legs. The caller then records the event as an `unfilled` rejection. The added regression tests explicitly expect zero trades, even though their fixtures reached and filled the entry before truncation.

Potential impact if confirmed: Missing post-entry data can selectively remove open positions—and their unknown or adverse outcomes—from P&L, trade count, drawdown, capacity, and acceptance-gate calculations. Labeling these positions “unfilled” also corrupts the reported unfilled-order rate.

Investigation and conditional remediation: Verify this concern against the current execution lifecycle first, confirming that the truncated fixtures actually execute their entry before the missing exit horizon. If confirmed, fail the entire affected run or represent the unresolved filled position explicitly so it cannot enter acceptance metrics; fix the classification and add regression coverage proving a post-entry truncation cannot become an unfilled order or disappear from the sample. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Use otherwise identical complete, pre-entry-truncated, and post-entry-truncated timelines. Confirm that only the pre-entry case may be classified as unfilled, while the post-entry case invalidates the run or remains explicitly unresolved.

### [P1] Investigate: the ordinary-day control reuses catalyst-day eligibility

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/strategies/intraday_metrics.py:454-505

Concern: The ordinary-day control may not rerun the strategy’s candidate rules on the control date.

Evidence: `control_candidates` selects only already-eligible catalyst candidates, changes their dates and prior close, but preserves `status`, `reasons`, and the catalyst-day `signals`. `run_control` passes these decisions directly to the engine without recomputing the ordinary day’s gap, premarket hold, relative volume, liquidity, or trend checks. This conflicts with the module’s stated contract that the control “re-runs the variant’s rules” on an ordinary session.

Potential impact if confirmed: The acceptance gate may compare the strategy against trades that would not qualify under its ordinary-day rules. Control expectancy can therefore produce false passes or failures and cannot reliably establish whether profitability is catalyst-specific.

Investigation and conditional remediation: Verify the intended control contract against the current plan and runbook first, then construct an ordinary day that fails the configured gap or relative-volume threshold while the corresponding event day passes. If confirmed, recompute control-date eligibility point-in-time before simulation and add regression coverage for failing and passing ordinary-day filters across regular and extended variants. If the intended control deliberately preserves event-day eligibility and tests only execution rules, document that evidence and leave the code unchanged.

Validation: Confirm that changing only the control day’s premarket gap, hold, or relative volume changes its eligibility and control trade count without changing the original event decision.

### [P2] Investigate: configured in-sample fraction is ignored by the chronological split

Commit: 9578517a701754a82f7148a4d44be53d43171adc

Location: src/trading_research/cli.py:2753-2767, 2833-2842

Concern: Alternative strategy configurations may report one split fraction while executing another.

Evidence: `EarningsGapIntradayConfig.in_sample_fraction` is loaded, validated, and included in the configuration hash, but `_intraday_gap_split` always uses the module constant `(3, 5)`. The CLI loads the configuration before splitting but does not pass its fraction to the split function.

Potential impact if confirmed: A configuration such as `in_sample_fraction: 0.7` still executes a 60/40 split, while the report identity indicates the 0.7 configuration. This can move events between tuning and out-of-sample sets relative to the operator’s declared configuration and undermine reproducibility.

Investigation and conditional remediation: Verify first whether `in_sample_fraction` is intended to be operator-configurable or whether 60/40 is an immutable protocol rule. If configurable, use it when calculating the unique-date boundary and add tests for non-default fractions and report identity. If immutable, remove or reject non-0.6 values so configuration cannot misrepresent behavior, with regression coverage. If already fixed or otherwise disproved, document the evidence and leave the code unchanged.

Validation: Run the same dated events with 0.6 and 0.7 configurations and verify either that the boundary changes accordingly or that the noncanonical value fails validation.

Tests or diagnostics run:

- Inspected the eleven specified commits chronologically using their diffs and relevant current code, tests, configuration, plan, and runbook.
- Targeted pytest selection: 4 passed in 3.05 seconds.
- `git diff --check 5f370050be5e51f4b47ff7e66e92e2ac0dc83228..0d053969f9b2315181b20f2c4d0f1f485d2b90bc` reported only a blank line at EOF in `config/strategies.yaml`; not a review finding.
- No files were modified, and no network, model, broker, scheduler, credentialed test, or order path was invoked.
