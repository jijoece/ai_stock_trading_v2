# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `chore/yield-spread-httpx-and-notes`
- Reviewed HEAD: `e37f101caa8d0d10a959ebc06ab5989026015c0f`
- Subject: Record local post-commit review of 0b835d9; add new-strategies placeholder
- Claude commits reviewed: 21af518dd55d34289b167a0506ea6d738181a79b,68b67980c62423839bd826127a42f246c66a5490,d7dbba0f22080a27c4b9496e9afe57252ba72f50,0b835d9aa07e6178a6fa298f643d89b3596b3b93,8df15082691039493d261bfdd8c9fb1732e5811b,e37f101caa8d0d10a959ebc06ab5989026015c0f
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: d027646fd396d50f41a1bfbffcc6943834ce9c77

## Findings

### [P2] Investigate: inactive-instrument substring can suppress unrelated pricing failures

Commit: 8df15082691039493d261bfdd8c9fb1732e5811b

Location: src/trading_research/mcp/robinhood_portfolio.py:204-214

Concern: Investigate whether classifying any tool error containing `inactive_instruments` as an unpriced holding fails open when the error does not specifically identify the requested symbol as inactive.

Evidence: The exception path performs only a substring check against redacted free-form server text. It does not parse the payload or confirm that the inactive-instrument list contains the symbol currently being priced. A malformed, mixed, or diagnostic error mentioning that field therefore causes the holding to be excluded from market value, cost basis, and unrealized P/L.

Potential impact if confirmed: The dashboard could materially understate portfolio totals while incorrectly describing a provider or parsing failure as a delisted or bankrupt holding.

Investigation and conditional remediation: Verify the concern against the current code and actual Robinhood MCP error contract first. If confirmed, parse a stable structured error representation and require an exact match between the requested symbol and the reported inactive-instrument set; otherwise fail closed. Add regression coverage for malformed errors, unrelated symbols, empty lists, and errors that merely mention `inactive_instruments`. If disproved or already fixed, document that evidence and leave the code unchanged.

Validation: Inject an `McpToolError` whose detail mentions `inactive_instruments` but identifies another symbol or contains no valid symbol list. The snapshot should raise instead of placing the current holding in `unpriced`.

### [P2] Investigate: overnight entries may be selected using next-morning information

Commit: 0b835d9aa07e6178a6fa298f643d89b3596b3b93

Location: docs/intraday-earnings-gap-plan.md:161-169,181-187

Concern: Investigate whether the proposed candidate filter introduces look-ahead bias for `post_report_overnight`.

Evidence: The plan applies `premarket_gap`, `premarket_hold`, and pre-market relative-volume filters to every variant, while `post_report_overnight` enters during the preceding post-market session. Those next-morning observations do not exist at entry time, and the plan specifies no variant-specific exception.

Potential impact if confirmed: The backtest could select prior-evening trades using subsequent price action and liquidity, overstating expectancy and invalidating its out-of-sample acceptance gate.

Investigation and conditional remediation: Verify the concern against the current plan and intended `select_candidates` contract first. If confirmed, define an overnight-specific filter using only information available before entry and add regression coverage proving next-morning bars cannot affect eligibility. If disproved or already fixed, document that evidence and leave the plan unchanged.

Validation: Hold all pre-entry inputs constant while varying only next-morning bars; overnight eligibility and entry decisions must remain identical.

### [P2] Investigate: close-derived signals may receive the signal bar’s unactionable closing price

Commit: 0b835d9aa07e6178a6fa298f643d89b3596b3b93

Location: docs/intraday-earnings-gap-plan.md:130,144-147,181-187

Concern: Investigate whether the regular-session fill model uses information unavailable when the proposed fill price was actionable.

Evidence: Bars and their signals are defined as available only at bar end. The `orb`, `pmh_break`, and `vwap_pullback` rules depend on the completed bar’s close, yet D5 fills the entry at that same closing price plus slippage.

Potential impact if confirmed: The simulator could obtain a price that was no longer tradable when the signal became known, systematically overstating results on fast-moving earnings-gap days.

Investigation and conditional remediation: Verify the intended event ordering against the current plan first. If confirmed, require execution no earlier than the next tradable bar or define a genuinely pre-close order rule, then add regression coverage for adverse gaps immediately after a signal close. If disproved or already fixed, document that evidence and leave the plan unchanged.

Validation: Use hand-built bars where the signal becomes true only at the close and the next bar gaps adversely; the fill must use the documented post-signal execution price.

Tests or diagnostics run:

- Inspected all seven specified full diffs chronologically and traced affected behavior through HEAD `a62aee98090edabf3c5519ced3cdf3314bbfdd4c`.
- Confirmed every specified commit is an ancestor of the reviewed HEAD.
- `git diff --check db9b17bb67efe615f2bec9ca04192335ffa1c756..a62aee98090edabf3c5519ced3cdf3314bbfdd4c` passed.
- No test suite was run because the review environment is read-only and canonical Nox sessions create files and environments.
- No files were modified and no credentialed, broker, model, scheduler, or external operations were performed.
