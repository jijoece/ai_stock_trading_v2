# Codex Project Instructions

This is a research and paper-trading system. Python computes; Codex explains;
the user decides and explicitly approves any external paper-order action.

## Project navigation

- Start with `docs/INDEX.md` when documentation is needed. Prefer its canonical
  current documents over milestone history.
- Treat code, tests, configuration, ADRs, and current runbooks as authoritative
  when historical milestone documents disagree.
- Use the `run-agentic-trading-desk` skill for ticker scoring, deterministic
  indicator computation, or verification of the scripts in `scripts/`.
- Use the `deep-dive` skill only for an explicitly requested formal deep dive,
  end-to-end investigation, or evidence-backed audit.

## Trading and computation guardrails

- Never calculate indicators by reasoning over price bars. Fetch the data and
  run `scripts/indicators.py`, `scripts/score.py`, or
  `scripts/macro_pillar.py` as appropriate.
- Default all broker interaction to read-only. Never submit an external paper
  order without an explicit request and the repository's confirmation gates.
- Do not describe research output as financial advice.

## Canonical validation tasks

- Use the root Nox sessions documented in `README.md` as the canonical command
  interface. Do not invent a second task interface unless debugging Nox itself.
- Before opening or finalizing a PR, run `nox -s ci`. For targeted iterations,
  use `nox -s tests -- <pytest arguments>` or
  `nox -s paper_tests -- <pytest arguments>`.
- Run `nox -s safety_typecheck` for changes to safety-critical modules. Report
  the exact Nox sessions and results in the PR summary.
- Never run credentialed smoke tests, broker operations, model calls, scheduler
  activation, or external-paper submission unless the operator explicitly asks.

## Python code intelligence

Prefer the Pyright LSP for Python navigation and diagnostics:

- Use definitions, references, symbols, implementations, hover types, and call hierarchy before broad grep or full-file reads.
- Read only the relevant symbol body when sufficient.
- Treat Pyright diagnostics as guidance; verify behavior with the project's tests.
- Do not perform broad type-error cleanup unless the task explicitly requests it.

## Compact instructions

When compacting, preserve the current goal, decisions, changed files and
symbols, test failures and passes, unresolved risks, and remaining work.
Discard raw search results, successful command output, historical-document
excerpts, repeated explanations, and superseded hypotheses.

## Code Review Rules

Focus on consequential defects, not style.

Prioritize:
- correctness and regressions
- trading safety boundaries
- broker side-effect ambiguity
- retry/idempotency defects
- accounting/data-integrity issues
- point-in-time / look-ahead bias
- persistence correctness
- unsafe defaults or fail-open behavior
- concurrency/race conditions
- ADR and migration-plan violations
- missing regression tests

Do not report formatting, naming, or lint issues already covered by CI.
