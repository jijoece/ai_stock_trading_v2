# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `chore/yield-spread-httpx-and-notes`
- Reviewed HEAD: `0b835d9aa07e6178a6fa298f643d89b3596b3b93`
- Subject: Fetch FRED with httpx; add intraday earnings-gap plan and dashboard access research
- Claude commits reviewed: 21af518dd55d34289b167a0506ea6d738181a79b,68b67980c62423839bd826127a42f246c66a5490,d7dbba0f22080a27c4b9496e9afe57252ba72f50,0b835d9aa07e6178a6fa298f643d89b3596b3b93
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: NEEDS_FIXES
- Highest priority: P2
- Finding count: 2

## Findings

### [P2] Investigate: overnight entries may be selected using next-morning information

Commit: 0b835d9aa07e6178a6fa298f643d89b3596b3b93

Location: docs/intraday-earnings-gap-plan.md:161-169,187

Concern: The proposed candidate filter may introduce look-ahead bias for `post_report_overnight`.

Evidence: The plan says every variant must pass filters based on `premarket_gap`, `premarket_hold`, and pre-market volume through 09:25. However, `post_report_overnight` enters during the preceding post-market session after `post_signal_time`, before those pre-market observations exist. No variant-specific exception is specified.

Potential impact if confirmed: Backtests could select prior-evening trades using knowledge of the following morning’s price action and liquidity, materially overstating expectancy and invalidating the out-of-sample acceptance gate.

Investigation and conditional remediation: Verify the concern against the current plan and intended `select_candidates` contract first. If confirmed, define a variant-specific candidate filter containing only information available before the post-market entry, and add regression coverage proving that next-morning bars cannot affect eligibility. If disproved or already fixed, document the evidence and leave the code and plan unchanged.

Validation: Backtest fixtures should hold all post-entry data constant except next-morning pre-market behavior and demonstrate identical `post_report_overnight` eligibility and entry decisions.

### [P2] Investigate: signal-bar closing prices may be used for fills before they are actionable

Commit: 0b835d9aa07e6178a6fa298f643d89b3596b3b93

Location: docs/intraday-earnings-gap-plan.md:130,146-147,183-185

Concern: The regular-session fill model proposes entering at the signal bar’s close even though the affected signals are first established by that same close.

Evidence: Intraday bars are defined as available only at their end. The `orb`, `pmh_break`, and `vwap_pullback` entries depend on a bar closing above or below a threshold, while D5 fills at that bar’s closing price plus slippage. The current repository contract explicitly prohibits future-bar fills, and existing vector backtests execute close-derived signals on the next bar.

Potential impact if confirmed: The simulator could fill at a price that was no longer actionable when the signal became known, creating systematic look-ahead bias and optimistic results on fast-moving earnings-gap days.

Investigation and conditional remediation: Verify the concern against the intended event ordering first. If confirmed, specify execution no earlier than the next tradable bar, or define a genuinely pre-close order rule, then add regression coverage demonstrating that changing the signal bar’s closing move cannot retrospectively obtain its closing price. If disproved or already fixed, document the evidence and leave the plan unchanged.

Validation: Add hand-built bars where the signal becomes true only at the close and the next bar gaps adversely; the resulting fill must use the documented post-signal execution price.

Tests or diagnostics run:

- `git diff --check db9b17bb67efe615f2bec9ca04192335ffa1c756..0b835d9aa07e6178a6fa298f643d89b3596b3b93` — passed.
- Targeted `nox -s tests -- ...` — could not start because the read-only environment had no usable temporary directory for creating `.nox/tests`; no tests executed.
- Reviewed all four specified full diffs chronologically and verified both findings against HEAD, ADR 0010, current backtesting code, and regression-test conventions.
