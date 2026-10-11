# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/18-final-authority-safety-audit`
- Reviewed HEAD: `df6bb571cbc018c7ad3e7861692ed83090981540`
- Subject: PR 18: final authority and safety audit
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa,7e523012072b6887e6bb4d9de61158e6df49d648,5840263d87fd53bf4561d8c444bc2135871435bd,6958cc72250572f16df50ac6ad5dfc6937fd9c3c,7f53240d254e0961e9808051161f82871e63d3f3,c355b37863fa6a536fd6391d7048be3c6de46a18,08d6a6fd4f8f672ef477bd8762c672abb66cd455,13d49ad3370eb9563ad0960cb0568086db6d87d5,30fdc546be223173e754ef637a2d0c1436b5dd5e,88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672,177d2744c62314ec5bc709f55f696aaf6ff0ea1d,3e15923397f461d07b95ee8d8605c772e3521303,8f73889c146a78887415ae2731654539d76dd16e,9732aa1bc3571f772316231dc71e4dcdd7fec12c,59eb96d30868c15dab4dccb69ca3fb67bd4009a4,9bd7446febf54a3722970c9d6df57f5fd4540dc9,6f66e1e871cbfc51c7f56ff2f25a6aed6ce7191e,fdfb219d4f5308ce48921b496dcba3e8e66e8287,3a5b9dc4f3f8ac895e10f301b9ac934fd08ac413,987013aa173fae4962b1fef8e5e35a90e0968e9f,155b0a3f6326687e272dc6d4355a64254c4b65f6,7594aca024ed08df11f8dfe75cb9804ddbe410b6,445a3ec97cb3c76456d28ce9bc21cc29c9fd93bd,df6bb571cbc018c7ad3e7861692ed83090981540
- Review scope: INCREMENTAL
- Fix round: 0
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: P1 (confirmed, fixed)
- Finding count: 0
- Fix commit: 27b7fcd410bf28ae055a4b7f870214f879762084

## Findings

### [P1] Investigate: JSON logging can expose escaped registered secrets from arbitrary extra objects

Commit: 13d49ad3370eb9563ad0960cb0568086db6d87d5

Location: src/trading_research/logging_config.py:104-118

Concern: Verify whether JSON logging still violates the module’s no-secret-leak contract when an arbitrary object supplied through `extra` renders a registered secret containing JSON-escaped characters.

Evidence: Commit `13d49ad…` replaced the fixed extra-field allowlist with `structlog.stdlib.ExtraAdder()`, allowing arbitrary objects into the JSON event. At HEAD, `_redact_value()` returns unrecognized objects unchanged. `JSONRenderer` subsequently serializes such an object through its representation, escaping quotes before the final string-level `redact()` pass. A focused probe registered `s3cr"et-value`, logged an object whose `__repr__` contained that value, and produced:

```json
{"details": "opaque s3cr\\\"et-value payload", "message": "event", ...}
```

The existing tests cover exceptions, mappings, sequences, sets, and bytes, but not arbitrary objects whose representation contains an escapable registered secret.

Potential impact if confirmed: API credentials, tokens, account identifiers, or other registered secrets embedded in diagnostic/domain objects could be written to JSON logs despite the documented centralized-redaction guarantee.

Investigation and conditional remediation: First verify the concern against the current logging pipeline using an arbitrary `extra` object whose `__repr__` contains registered secrets with a quote, backslash, and control character. If confirmed, ensure non-primitive values are converted and redacted before JSON serialization, or otherwise prevent arbitrary representations from entering rendered output, and add regression coverage for each escaping case. If the concern is disproved or already fixed through behavior not apparent in the current code, document that evidence and leave the code unchanged.

Validation: Parse the emitted JSON and assert that neither the original secret nor its decoded value appears, while `[REDACTED]` does. Run `nox -s tests -- tests/unit/test_logging_config.py` and the canonical `nox -s ci`.

Tests or diagnostics run:

- Inspected every specified commit chronologically, including full code/test/configuration changes and relevant current ADR, migration, and runbook context.
- Reproduced the JSON redaction leak with `.venv/bin/python` against reviewed HEAD.
- Attempted the focused pytest suite, but the read-only environment had no writable temporary directory; pytest stopped during capture initialization before collecting tests.
- `nox` was unavailable in the active environment.
- No credentialed tests, broker operations, model calls, scheduler activation, or external order actions were performed.

## Fix Record

**Confirmed.** At `df6bb57`, logging an `extra` object whose `__repr__`
contained a registered secret with a quote, backslash, or newline emitted the
secret in JSON-escaped form (all three cases reproduced with
`.venv/bin/python`). `_redact_value()` returned the object unchanged, and
`JSONRenderer`'s `repr()` fallback escaped the secret before the final
string-level `redact()` pass.

Fix (`27b7fcd`): `_redact_value()` now passes JSON scalars
(`int`/`float`/`bool`/`None`) through and replaces any other non-container
object with `redact(repr(value))`, the same text `JSONRenderer` would have
rendered, before serialization. `_redact_event_dict()` skips the
`ProcessorFormatter` meta keys (`_record`, `_from_structlog`).

Regression tests in `tests/unit/test_logging_config.py`:

- `test_json_output_redacts_registered_secret_in_arbitrary_object_repr_with_json_escaped_characters`
  (quote, backslash, newline; top-level and nested objects; parses the JSON
  and asserts neither the raw nor decoded secret appears while `[REDACTED]`
  does). It fails on `df6bb57` and passes with the fix.
- `test_json_output_preserves_json_scalar_extra_fields`.

Validation:

- `nox -s tests -- tests/unit/test_logging_config.py`: 41 passed.
- `nox -s ci`: passed (tests 3416 passed / 107 skipped; paper_tests 160
  passed; safety_typecheck 0 errors; migration_smoke passed).
- No credentialed tests, broker operations, model calls, scheduler
  activation, or external order actions were performed.
