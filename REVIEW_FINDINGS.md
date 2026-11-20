# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `intraday-earnings-gap`
- Reviewed HEAD: `ebd73222f9ec72e4b6cc90c610d91b567549eb82`
- Subject: Add Step 7 tooling: in-sample grid sweep, committed freeze, run-once OOS ledger
- Claude commits reviewed: 3a530d278245e47dc47c248a8cdb4470522a9c69,0c8f5c8550aba60de53543153db6e6ac6d4b45d8,638db304115852d1b8eb497170c139ea16053cda,9578517a701754a82f7148a4d44be53d43171adc,4f0ede1eaf54297e8e754b45e8f9249ffe96a3b0,32dd766ce8f292aebbb5cbab8b304904398b3d50,14c77d1881f2dd61bfbe03d713e376e310d1f15a,5c8062a4a18ca2ea579db379cbfd3d10fcb0168c,3c2b311c56757ec0e0ba1f4ddcd4f8b9edebaf83,3dc8d4d4912e4aa6f2150760183187d277fdc185,a5515c9aef3a6a80f278cf6e8d5ce563e4456233,534bfa1ad58025cc6adab4a334f0639a74c52c47,b0afdd6d0388e161277d9baa435b12c157e78cae,0d053969f9b2315181b20f2c4d0f1f485d2b90bc,99d6457279d3c07f5244ba4f456ada0e3544c408,0b4789ee4009c55081a91e931769a865469c8aa4,f424a3369c0cd5a24f9d6efc4457880a15651060,a2ff2e297f9072d3d2347c78b69c36e08f496e1d,ebd73222f9ec72e4b6cc90c610d91b567549eb82
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1
- Finding count: 0
- Fix commit: 399b812dcdc79a0daca07bdd6b5d18f713221178

## Findings

### [P1] Investigate: frozen OOS parameters do not bind the tuned strategy configuration

Commit: ebd73222f9ec72e4b6cc90c610d91b567549eb82

Location: src/trading_research/backtesting/intraday_gap_step7.py:165-178,203-235; src/trading_research/cli.py:2850-2872

Concern: The committed freeze may permit an out-of-sample run using a materially different strategy configuration from the one evaluated in-sample.

Evidence: `FrozenParameters` retains only the CSV hash, split fraction, opening range, and maximum risk percentage. Although the generated template includes each suggestion’s `in_sample_run_id`, `load_frozen_parameters` discards that field. The OOS path compares only the CSV hash and `in_sample_fraction` before applying the two grid parameters to the currently loaded configuration. Changes to eligibility thresholds, costs, sizing, participation, entry/exit times, spread assumptions, or other strategy settings therefore remain accepted. Existing tests cover CSV, fraction, and grid mismatches, but not changes to other configuration fields between tuning and OOS.

Potential impact if confirmed: The single OOS evaluation could test a strategy that was never tuned or selected in-sample, weakening the chronological holdout and allowing accidental post-selection parameter changes to affect the acceptance gate.

Investigation and conditional remediation: Verify the concern against the current code first by tuning with one configuration, committing the generated freeze, changing a non-grid strategy setting, and attempting OOS. If confirmed, bind the freeze to the complete tuned configuration identity—such as the in-sample report’s configuration hash or an equivalent canonical strategy-config hash—and refuse mismatches before evaluating OOS. Add regression coverage for both accepted identical configurations and rejected non-grid configuration changes. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Demonstrate that a freeze produced from configuration A accepts OOS under A but fails closed under configuration B when any result-defining strategy setting differs.

### [P1] Investigate: an interrupted or concurrent OOS run can bypass the run-once ledger

Commit: ebd73222f9ec72e4b6cc90c610d91b567549eb82

Location: src/trading_research/cli.py:2949-2962; src/trading_research/backtesting/intraday_gap_step7.py:155-159,260-274

Concern: The run-once guarantee may not survive failures or concurrent processes because checking, publishing reports, and recording the ledger are separate operations without a reservation or lock.

Evidence: The CLI checks the ledger, writes both report artifacts, and only then calls `record_run`. A crash or ledger-write failure after `write_report` leaves completed OOS results without a ledger entry, allowing a different later run. Two processes can also both observe no prior entry, produce different reports, independently reread the empty ledger, and overwrite the same ledger through a shared fixed `.tmp` path. The existing test exercises only sequential successful runs and does not inject persistence failures or concurrency.

Potential impact if confirmed: Multiple distinct OOS results could be observed for one variant while the ledger records only the last one—or none—undermining the stated single-use holdout and its audit trail.

Investigation and conditional remediation: Verify the concern against the current code first with fault injection between report publication and ledger recording, plus two coordinated processes attempting different run IDs. If confirmed, introduce an atomic per-variant reservation/commit protocol with process-safe exclusion and recoverable pending state; ensure report publication and ledger finalization have explicit recovery semantics. Add regression coverage for interruption, ledger-write failure, and competing processes. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Prove that once any process begins the authoritative OOS evaluation, a competing different run cannot proceed, and that an interruption leaves an auditable state that cannot silently permit another distinct evaluation.

Tests or diagnostics run:

- Inspected the 19 specified Claude-authored commits chronologically, their diffs, and the relevant current code, tests, configuration, plan, runbook, and review log.
- Confirmed that earlier truncation findings were addressed by `f424a3369c0cd5a24f9d6efc4457880a15651060` and `a2ff2e297f9072d3d2347c78b69c36e08f496e1d`.
- Targeted pytest execution was blocked before collection because the read-only environment exposed no writable temporary directory.
- `git diff --check 5f370050be5e51f4b47ff7e66e92e2ac0dc83228..ebd73222f9ec72e4b6cc90c610d91b567549eb82` reported only whitespace in configuration/documentation; those are outside the requested finding scope.
- CodeRabbit was not run because its required host-execution permission is unavailable in this session.
- No files or external state were modified; no network, model, broker, scheduler, credentialed test, or order path was invoked.
