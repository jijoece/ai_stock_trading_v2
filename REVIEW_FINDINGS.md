# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `fcb5f745cd4afca7ac6b1ce7f0f960f1c4bc0870`
- Subject: Record review fixes applied for 7436589 (pending re-review)
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa,7e523012072b6887e6bb4d9de61158e6df49d648,5840263d87fd53bf4561d8c444bc2135871435bd,6958cc72250572f16df50ac6ad5dfc6937fd9c3c,7f53240d254e0961e9808051161f82871e63d3f3,c355b37863fa6a536fd6391d7048be3c6de46a18,08d6a6fd4f8f672ef477bd8762c672abb66cd455,13d49ad3370eb9563ad0960cb0568086db6d87d5,30fdc546be223173e754ef637a2d0c1436b5dd5e,88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672,177d2744c62314ec5bc709f55f696aaf6ff0ea1d,3e15923397f461d07b95ee8d8605c772e3521303,8f73889c146a78887415ae2731654539d76dd16e,9732aa1bc3571f772316231dc71e4dcdd7fec12c,59eb96d30868c15dab4dccb69ca3fb67bd4009a4,9bd7446febf54a3722970c9d6df57f5fd4540dc9,6f66e1e871cbfc51c7f56ff2f25a6aed6ce7191e,fdfb219d4f5308ce48921b496dcba3e8e66e8287,3a5b9dc4f3f8ac895e10f301b9ac934fd08ac413,987013aa173fae4962b1fef8e5e35a90e0968e9f,155b0a3f6326687e272dc6d4355a64254c4b65f6,7594aca024ed08df11f8dfe75cb9804ddbe410b6,445a3ec97cb3c76456d28ce9bc21cc29c9fd93bd,df6bb571cbc018c7ad3e7861692ed83090981540,27b7fcd410bf28ae055a4b7f870214f879762084,04da132a2c83b29388d94352cb0b99af3c47fc94,2276e5dd17942e8e46de7d3dc18e75c0a64d55a4,06042259ef32255aed41a814f3143bc2a7e34f70,b2271381180ed6e5c2293eadc29aa1eef2a63421,a6f03dd1eaef69b9223e81ac9374880f69026c11,79248feb33d3888cc7f0acb482f6a5f5a70742b8,9c7b20a6e4292fa1c1181f2d8f0ea00750abc36d,9dedcc080094ea8a54a5cfad343ba2d4c1c23715,19bb0edcf1594bb3d5fb16fb423377f194f39370,398ea07388af285e420e620dffd65aec1eee4d2b,9bc6e58be5dc1932236971316835d63b980c3e22,f9ec68d52ca33780da5b45c10fef54bec0a7554e,3a66d10de308fac681222c6a0160886e14a70c28,c1de3ed68a68efa330dca7eb2b6183cc10adf89e,fadb58900a5d8a655fe017c8197f85b94afe8419,581229c01301f9f4a1ce7da77cddbe69e4a84249,1555bf6f391426a8f96cff8c28e4a0440dd213b0,90995596897cb2998cbdc56f8b5a2fe6ab0d83cf,f7b141c2d875e247454d3acdf7f923c95a37c44c,480097a198b5b5559b83061b0ba7a4d20cd45c98,932395ae837518be0c3e8def166670fd8a4723d2,839991312baa99074bd4040f95ee54497f9003b1,333efbd4dd5b388598ffb936335302742495a31a,8ad687099fd82636e42c7d0bc6517ce44260f917,3b07ef73c5997555d15b7cb2f464eb5d1014ae3b,02cc18f0af95b0c3fc270dad59224d4f436f3e92,7436589695b809ae84a926455b9e41369fcb8b53,7743c242872009e5006b7ee3915b36079970bb7c,fcb5f745cd4afca7ac6b1ce7f0f960f1c4bc0870
- Review scope: INCREMENTAL
- Fix round: 1
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: 24ee0c575e1bcf57b5aa901015e6b88d4e103fb3
- Highest priority: P1
- Finding count: 0

## Findings

### [P1] Investigate: concurrent strategy cycles can approve intents against stale risk state

Commit: 7436589695b809ae84a926455b9e41369fcb8b53

Location: src/trading_research/paper_books/strategy_bridge.py:173-187,217-225,265-279

Concern: Concurrent Stage 5 cycles may each approve different signals using the same pre-commit portfolio and daily-limit state, allowing their combined reservations to exceed paper-book risk limits.

Evidence: The duplicate check, portfolio snapshot, `committed_notional`, daily-risk calculation, and risk evaluation occur before `transaction(conn)` acquires SQLite’s write lock. `committed_notional` covers only earlier intents in the same process. Two connections can therefore evaluate different signals concurrently, both see neither process’s reservation, both approve, and then serialize their independently approved writes. The command has no cycle lease, and existing idempotency only protects the same signal or deterministic intent—not different signals competing for shared cash and limits.

Potential impact if confirmed: Local paper-book cash reservations, daily-new-notional limits, position counts, or symbol-concentration limits could be exceeded. This would corrupt Stage 5 safety evidence and make promotion results unreliable.

Investigation and conditional remediation: Verify this concern against the current code first with two SQLite connections synchronized immediately after risk approval and before transaction acquisition, using different eligible signals whose combined notional breaches a configured limit. If confirmed, serialize the risk-state read, decision, and registration under one book/cycle lock or `BEGIN IMMEDIATE`, recomputing authoritative portfolio and daily-risk state after acquiring it, and add a concurrent regression test. If disproved or already fixed, document the locking evidence and leave the code unchanged.

Validation: Assert that at most the permitted aggregate notional is reserved, one competing signal is rejected using the committed state, and retries remain idempotent without duplicate decisions, claims, reservations, intents, or links.

### [P1] Investigate: strategy entries continue after the preceding lifecycle reports failure

Commit: 7436589695b809ae84a926455b9e41369fcb8b53

Location: src/trading_research/forward_testing/paper_cycle.py:101-107

Concern: The Stage 5 orchestration may create new entry intents even when the required lifecycle phase failed for the baseline book.

Evidence: `run_paper_book_lifecycle()` records per-book exceptions in `failure_reasons` and returns normally. `run_strategy_paper_cycle()` does not inspect `failure_reasons`, `books_processed`, or baseline reconciliation status before immediately calling `create_strategy_entry_intents()`. A failure while resolving pending orders, evaluating exits, reconciling, or persisting lifecycle outputs therefore does not itself stop new entries.

Potential impact if confirmed: New reservations and intents could be based on incomplete pending-order processing, missed exits, or an unreconciled lifecycle state, causing incorrect exposure and undermining the documented lifecycle-before-entry safety boundary.

Investigation and conditional remediation: Verify the concern against current code first by inducing a baseline lifecycle failure before entry creation while otherwise providing an approvable signal. If the bridge still creates an intent, fail closed whenever the baseline book was not fully processed, has a lifecycle failure, or lacks the required successful reconciliation; add regression coverage proving no risk decision, claim, reservation, intent, or strategy link is created. If disproved or already fixed, record the evidence and leave the code unchanged.

Validation: Cover failures during pending-order processing, exit evaluation, reconciliation, and post-reconciliation persistence, asserting the cycle reports the failure and creates no new entry-side records.

Tests or diagnostics run:

- `git diff --check 30514df42ae61163875646eb8bc4c66f09140a41..fcb5f745cd4afca7ac6b1ce7f0f960f1c4bc0870` — passed.
- Parsed and compiled all 265 Python files under `src/trading_research` and `scripts` without writing bytecode — passed.
- Targeted pytest suite was attempted but could not start because the read-only sandbox provides no writable temporary directory. No credentialed, broker, model, scheduler, or order operations were run.
