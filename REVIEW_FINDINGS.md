# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `feat/historical-backtest-step1`
- Reviewed HEAD: `a0b3d7f6a96ea88a6cd10265e8323d45415c5c84`
- Subject: Record review fixes applied for fcb5f74 (pending re-review)
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa,7e523012072b6887e6bb4d9de61158e6df49d648,5840263d87fd53bf4561d8c444bc2135871435bd,6958cc72250572f16df50ac6ad5dfc6937fd9c3c,7f53240d254e0961e9808051161f82871e63d3f3,c355b37863fa6a536fd6391d7048be3c6de46a18,08d6a6fd4f8f672ef477bd8762c672abb66cd455,13d49ad3370eb9563ad0960cb0568086db6d87d5,30fdc546be223173e754ef637a2d0c1436b5dd5e,88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672,177d2744c62314ec5bc709f55f696aaf6ff0ea1d,3e15923397f461d07b95ee8d8605c772e3521303,8f73889c146a78887415ae2731654539d76dd16e,9732aa1bc3571f772316231dc71e4dcdd7fec12c,59eb96d30868c15dab4dccb69ca3fb67bd4009a4,9bd7446febf54a3722970c9d6df57f5fd4540dc9,6f66e1e871cbfc51c7f56ff2f25a6aed6ce7191e,fdfb219d4f5308ce48921b496dcba3e8e66e8287,3a5b9dc4f3f8ac895e10f301b9ac934fd08ac413,987013aa173fae4962b1fef8e5e35a90e0968e9f,155b0a3f6326687e272dc6d4355a64254c4b65f6,7594aca024ed08df11f8dfe75cb9804ddbe410b6,445a3ec97cb3c76456d28ce9bc21cc29c9fd93bd,df6bb571cbc018c7ad3e7861692ed83090981540,27b7fcd410bf28ae055a4b7f870214f879762084,04da132a2c83b29388d94352cb0b99af3c47fc94,2276e5dd17942e8e46de7d3dc18e75c0a64d55a4,06042259ef32255aed41a814f3143bc2a7e34f70,b2271381180ed6e5c2293eadc29aa1eef2a63421,a6f03dd1eaef69b9223e81ac9374880f69026c11,79248feb33d3888cc7f0acb482f6a5f5a70742b8,9c7b20a6e4292fa1c1181f2d8f0ea00750abc36d,9dedcc080094ea8a54a5cfad343ba2d4c1c23715,19bb0edcf1594bb3d5fb16fb423377f194f39370,398ea07388af285e420e620dffd65aec1eee4d2b,9bc6e58be5dc1932236971316835d63b980c3e22,f9ec68d52ca33780da5b45c10fef54bec0a7554e,3a66d10de308fac681222c6a0160886e14a70c28,c1de3ed68a68efa330dca7eb2b6183cc10adf89e,fadb58900a5d8a655fe017c8197f85b94afe8419,581229c01301f9f4a1ce7da77cddbe69e4a84249,1555bf6f391426a8f96cff8c28e4a0440dd213b0,90995596897cb2998cbdc56f8b5a2fe6ab0d83cf,f7b141c2d875e247454d3acdf7f923c95a37c44c,480097a198b5b5559b83061b0ba7a4d20cd45c98,932395ae837518be0c3e8def166670fd8a4723d2,839991312baa99074bd4040f95ee54497f9003b1,333efbd4dd5b388598ffb936335302742495a31a,8ad687099fd82636e42c7d0bc6517ce44260f917,3b07ef73c5997555d15b7cb2f464eb5d1014ae3b,02cc18f0af95b0c3fc270dad59224d4f436f3e92,7436589695b809ae84a926455b9e41369fcb8b53,7743c242872009e5006b7ee3915b36079970bb7c,fcb5f745cd4afca7ac6b1ce7f0f960f1c4bc0870,24ee0c575e1bcf57b5aa901015e6b88d4e103fb3,a0b3d7f6a96ea88a6cd10265e8323d45415c5c84
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Fix commit: 4a45fc23ce4a29f53f54ea813df544f82cb3bc5c
- Highest priority: P2
- Finding count: 0

## Findings

### [P2] Investigate: open fills are omitted from the shadow fill-rate metric

Commit: 7436589695b809ae84a926455b9e41369fcb8b53

Location: src/trading_research/forward_testing/shadow_report.py:69-76, src/trading_research/forward_testing/shadow_report.py:106-108

Concern: Verify whether the Stage 4 shadow report is intended to count currently `OPEN` signals as filled. The documented formula says `filled = CLOSED + OPEN`, but the report loads only terminal outcomes and then defines `filled = len(closed)`.

Evidence: `list_terminal_outcomes()` cannot return `OPEN` progress rows. Consequently, an eligible signal that filled but remains open is absent from both numerator and denominator. For example, one open fill and one `NO_FILL` produces a reported fill rate of `0`, while the documented formula produces `1/2`. The arithmetic test covers closed, no-fill, and rejected rows but has no open outcome. The Phase 2 plan uses fill rate as a Stage 4 band/exit criterion.

Potential impact if confirmed: Forward fill rate can be understated while positions remain open, potentially producing incorrect `BELOW` band results and misleading the operator’s stage-promotion evidence.

Investigation and conditional remediation: First verify the intended metric against the current Phase 2 plan and current reporting semantics. If confirmed, include the latest `OPEN` progress outcome for unresolved signals when calculating fill rate, while continuing to use terminal `CLOSED` rows for round-trip and return metrics. Add regression coverage with a mixture of `OPEN`, `CLOSED`, `NO_FILL`, and `REJECTED` outcomes. If the intended definition has changed or current code already obtains open outcomes through another path, document that evidence and leave the code unchanged.

Validation: Reproduce the report with one persisted `OPEN` progress outcome and one terminal `NO_FILL`; confirm whether fill rate should be `0.5`, and verify that repeated progress rows for the same signal are counted only once using the latest state.

Tests or diagnostics run:

- `git diff --check 30514df42ae61163875646eb8bc4c66f09140a41..a0b3d7f6a96ea88a6cd10265e8323d45415c5c84` — passed.
- `nox -s tests -- ...` — not run; `nox` was not on `PATH`.
- `.venv/bin/pytest -q tests/unit/test_strategy_shadow.py tests/unit/test_strategy_paper_cycle.py tests/unit/test_strategy_backtest_runner.py` — blocked before collection because the read-only environment had no writable temporary directory.
- Read-only chronological diff and current-code review completed; no broker, credentialed, scheduler, model, or order operations were performed.
