# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `chore/yield-spread-httpx-and-notes`
- Reviewed HEAD: `54755ebf6250a887a429eb3bb579d365de90fe4b`
- Subject: Record fixes for PR 42 review findings (pending re-review)
- Claude commits reviewed: 21af518dd55d34289b167a0506ea6d738181a79b,68b67980c62423839bd826127a42f246c66a5490,d7dbba0f22080a27c4b9496e9afe57252ba72f50,0b835d9aa07e6178a6fa298f643d89b3596b3b93,8df15082691039493d261bfdd8c9fb1732e5811b,e37f101caa8d0d10a959ebc06ab5989026015c0f,a62aee98090edabf3c5519ced3cdf3314bbfdd4c,d027646fd396d50f41a1bfbffcc6943834ce9c77,54755ebf6250a887a429eb3bb579d365de90fe4b
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P2
- Finding count: 0
- Fix commit: c4b68123af5b8c623d19df5dcdc6c3b265e623c0

## Findings

### [P2] Investigate: inactive-instrument parsing still accepts unrelated failure responses

Commit: 8df15082691039493d261bfdd8c9fb1732e5811b

Location: src/trading_research/mcp/robinhood_portfolio.py:161-180

Concern: Investigate whether the inactive-instrument exception still fails open for responses that contain the expected JSON object but are not the documented inactive-instrument error.

Evidence: `_reports_only_inactive()` finds the first `{` and uses `JSONDecoder.raw_decode()`, but ignores both the preceding status text and any trailing content. At reviewed HEAD it returns `True` for both `API error 500: {"inactive_instruments":["DEAD"]}` and `API error 400: {"inactive_instruments":["DEAD"]} trailing unrelated failure`. The documented contract expects `API error 400` containing only the inactive-instrument payload. Existing regression cases cover malformed JSON, unrelated symbols, and a 500 response that merely mentions the field, but not a 500 or mixed response containing an otherwise valid object.

Potential impact if confirmed: A provider outage or compound error could be misclassified as a delisted holding. The affected position would be omitted from market value, cost basis, and unrealized P/L instead of failing closed, understating dashboard totals.

Investigation and conditional remediation: Verify the concern against the current code and the actual Robinhood MCP error contract first. If confirmed, require the documented status and ensure no non-whitespace content follows the parsed object, or consume a stable structured error representation instead of free-form text. Add regression coverage for a 500 response with a valid inactive-instrument object and for trailing or compound error content. If the provider contract proves these forms unambiguously mean the requested symbol is inactive, document that evidence and leave the code unchanged.

Validation: Inject the two responses above while pricing `DEAD`; unless the provider contract confirms they are valid inactive-instrument responses, snapshot generation should raise `McpToolError` rather than adding the holding to `unpriced`.

Tests or diagnostics run:

- Inspected all nine specified full diffs chronologically and traced later fixes through HEAD `54755ebf6250a887a429eb3bb579d365de90fe4b`.
- `git diff --check db9b17bb67efe615f2bec9ca04192335ffa1c756..54755ebf6250a887a429eb3bb579d365de90fe4b` — passed.
- Read-only Python diagnostic confirmed both malformed classifications return `True`.
- Targeted pytest execution could not start because the read-only environment had no usable temporary directory; no tests executed.
- No files, external services, brokers, schedulers, models, or orders were modified or accessed.
