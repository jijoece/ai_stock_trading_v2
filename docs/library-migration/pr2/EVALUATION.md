# PR 2 — Boundary Validation Evaluation

Scope per `DECISIONS.md` D2 and `MASTER_PLAN.md`: evaluation only. Compares
current hand-written trust-boundary validation against a Pydantic v2
boundary-model implementation. Does not replace any frozen dataclass domain
model. Adds no dependency to `pyproject.toml` — Pydantic was tested against
a locally available interpreter install (`pydantic==2.12.5`), not added to
any project dependency declaration.

## 1. Boundary inventory

Every hand-validated untrusted-input boundary found in the repository:

| Boundary | File / entry point | Rejects unknown fields? | Errors |
|---|---|---|---|
| YAML: research config | `research/configuration.py::load_research_config` | Yes, at every nesting level | `ResearchConfigError` + specific subclasses |
| YAML: scoring config | `analysis/scorer.py::load_scoring_config` | **No** (gap) | `ScoringConfigError` |
| YAML: screening config | `analysis/screener.py::load_screening_config` | **No** (gap) | `ScreeningConfigError` |
| YAML: paper books config | `paper_books/config.py` (multiple sections) | Yes (`_reject_unknown_keys` helper) | `PaperBooksConfigError` |
| YAML: evidence provider config | `evidence_providers/config.py::load_evidence_provider_config` | Partial | `EvidenceProviderConfigError` |
| YAML: strategy config | `strategies/config.py` | Partial | `StrategyConfigError` |
| YAML: shadow ops config | `shadow/config.py` | Yes | `ShadowOperationsConfigError` |
| YAML: paper runtime config | `runtime/paper_runtime_config.py::load_paper_runtime_config` | Yes, top-level and per-section | `PaperRuntimeConfigError` |
| YAML: execution config | `execution/config.py::load_execution_config` | **No** (gap) | `ExecutionConfigError` |
| CLI arguments | `cli.py::main` (argparse) | N/A (argparse `choices=`) | `argparse` / one `ValueError` |
| CLI: scripts | `scripts/indicators.py`, `scripts/score.py` | **No shape validation at all** (gap) | none — fails downstream with an opaque `KeyError`/`TypeError` |
| CLI: macro pillar | `scripts/macro_pillar.py::extract_closes` | Yes (shape-checked) | `MarketDataShapeError` |
| JSONL broker protocol | `paper_runtime/.../protocol.py::parse_request_line` | Yes, exact required-field set | `RuntimeOperationError` |
| Provider HTTP responses | `evidence_providers/http_client.py::HttpJsonClient.get_json` | Depth/size bounded, credential-query-param redaction | `MalformedProviderResponseError` |
| Provider response shape | `evidence_providers/market_data_provider.py`, `alpaca_news_provider.py`, `economic_calendar.py` | Per-field checks | `MalformedProviderResponseError` / `ProviderConfigurationError` |

Secrets: credentials are env-var-only (never in YAML/CLI). `field(repr=False)`
on secret dataclass fields, an explicit dotenv-key allowlist with permission
checks, and query-param redaction in `http_client.py` are the existing
secret-handling conventions — none of the YAML/JSONL boundaries above carry
secret fields directly.

## 2. Comparison methodology

Built a scratch Pydantic v2 implementation (`boundary_comparison_scratch.py`,
this directory; not merged into `src/`) of the two highest-stakes boundaries:

1. **`paper_runtime_config.py`** — YAML config carrying the paper-trading
   safety invariants (`real_money_enabled` must be false, exact allowed
   sides/order-types/base-URL, no fractional/margin/shorting/extended-hours).
2. **`protocol.py::parse_request_line`** — the JSONL broker-protocol envelope,
   the strictest existing boundary (exact required-field set, extra fields
   rejected, protocol-version pinning).

These were chosen because they are the most safety-critical and the most
thoroughly hand-validated; if Pydantic doesn't clearly win here, it is
unlikely to justify itself at the lower-stakes boundaries. Raw output is in
`comparison_output.txt`.

## 3. Findings

**Dependency/performance impact.** `pydantic==2.12.5` pulls exactly four
lightweight transitive packages: `pydantic-core` (compiled, prebuilt wheels
for all target platforms), `annotated-types`, `typing-extensions`,
`typing-inspection`. No heavy transitive footprint (contrast TA-Lib/
OpenTelemetry). `model_validate()` on the config-sized payload above measured
~3.7us/call over 20k iterations — negligible next to hand-written validation
of a comparable dict (~5.6us/call in an unscientific microbenchmark). No
material performance difference either direction.

**Error-message behavior.** Pydantic's default is materially better for one
thing hand-written code does not do: it collects *all* validation errors in
one pass (`ValidationError.errors()` returns every failing field), whereas
every hand-written loader in this repository raises on the first failure.
For a human editing a YAML file, this is a genuine ergonomic win. Per-error
message text is comparably clear once a `field_validator` supplies a
domain-specific message (see `real_money_enabled` case in
`comparison_output.txt`); Pydantic's default messages for plain type/required
errors ("Field required", "Input should be a valid number") are slightly
more generic than the current hand-written messages but are structured
(`loc`, `type`, `msg`) rather than free text, which is friendlier to
programmatic handling than the current plain strings.

**Unknown-field rejection.** This is Pydantic's clearest mechanical win:
`model_config = ConfigDict(extra="forbid")` gets whole-model unknown-key
rejection for free, replacing the ~10-15 line set-difference-and-raise
pattern repeated by hand across `paper_runtime_config.py`,
`paper_books/config.py`, `research/configuration.py`, and
`shadow/config.py`. Confirmed working at both top-level and nested-section
depth in the scratch comparison.

**Safety-critical business rules do not shrink.** The invariants that matter
most in this repository — `real_money_enabled` must be false,
`allowed_sides` must be exactly `(BUY, SELL)`, `base_url` must be pinned,
`protocol_version` must match exactly — are not generic shape/type
constraints Pydantic provides out of the box. Each one still requires a
custom `field_validator`, which is exactly as many lines as the equivalent
`if` check in the current `__post_init__`. Converting
`paper_runtime_config.py` produced a Pydantic model *longer* than the
current hand-written implementation (~110 lines of model/validator code vs.
~70 lines of function + `__post_init__`), because Pydantic adds structural
scaffolding (per-section model classes) without removing any of the
business-rule checks. The clean two-layer boundary→dataclass conversion
`DECISIONS.md` D2 requires (untrusted dict → Pydantic model → explicit
conversion → frozen dataclass) would add a third representation of the same
schema on top of the existing YAML-dict-to-dataclass pattern, for a boundary
that already fails closed correctly.

**Secret-field handling.** Not exercised directly (no secrets cross any YAML/
JSONL boundary in this repository), but Pydantic's `SecretStr` would provide
parity with, not improvement over, the existing `field(repr=False)` +
dotenv-allowlist convention.

**Gaps identified, orthogonal to the Pydantic question.** Three boundaries
lack unknown-field rejection entirely (`analysis/scorer.py`,
`analysis/screener.py`, `execution/config.py`), and two CLI scripts
(`scripts/indicators.py`, `scripts/score.py`) perform no shape validation of
their `json.load`'d input at all, unlike `scripts/macro_pillar.py`'s
`extract_closes`. These are real gaps but are inconsistency/completeness
issues in the existing hand-written pattern, not evidence that a new
dependency is required to fix them — the existing `_reject_unknown_keys`-
style helper already used by `paper_books/config.py` covers this with no new
dependency.

## 4. Recommendation: do not adopt

Per the adoption bar in `DECISIONS.md` D2 ("adopt only where it produces a
clear reduction in custom boundary-validation code") and the governing
principle in `DECISIONS.md` ("preserve project-specific domain
infrastructure when it encodes safety... that the proposed library does not
provide"): **no boundary evaluated showed a clear reduction in custom
validation code.** The one mechanical win Pydantic offers for free — unknown-
field rejection — is already implemented by hand at every safety-critical
boundary; the boundaries where it is genuinely missing are small enough
(20-90 LOC loaders) that the existing in-repo helper pattern closes the gap
without a new dependency. The safety-critical business rules that make these
boundaries worth validating carefully in the first place do not shrink under
Pydantic — they relocate into `field_validator` methods of equal size.

**Decision: do not add `pydantic` to any dependency declaration in PR 2.**
No ADR is required (per the single rule in `DECISIONS.md` D2, an ADR is
needed only if adoption is recommended). `pyproject.toml` is unchanged by
this PR.

**Non-blocking follow-up (not part of this PR):** close the three
unknown-field-rejection gaps and two CLI shape-validation gaps identified
above using the existing hand-written `_reject_unknown_keys`/shape-check
pattern already established in `paper_books/config.py` and
`scripts/macro_pillar.py`, for consistency — no new dependency needed.
