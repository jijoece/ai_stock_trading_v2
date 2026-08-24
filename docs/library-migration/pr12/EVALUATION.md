# PR 12 — Riskfolio-Lib Evaluation

Scope per `MASTER_PLAN.md` row 12: **evaluation only.** No dependency is
added to `pyproject.toml`; no file under `src/`, `scripts/`,
`paper_runtime/src/`, or `backtest_runtime/` is modified by this PR.
`docs/library-migration/pr12/scratch_smoke_test.py`, `scratch_output.txt`,
and `scratch_output_py311.txt` (this directory) are a scratch reproduction,
not merged into `src/` — mirroring the pattern PR 2 established for an
evaluation-only phase. This PR's review fix rounds added one regression
test, `tests/unit/test_pr12_evaluation_docs.py`, which checks only that this
directory's documentation states the facts above consistently — it adds no
application code and exercises no trading capability.

## 1. What Riskfolio-Lib would add

Riskfolio-Lib is a portfolio-optimization library (classic mean-variance,
risk-parity, hierarchical risk parity/HERC, CVaR and other risk-measure
objectives) built on `cvxpy` for the underlying convex optimization. Per
`COMPONENT_MATRIX.md`, "Portfolio optimization" has **no existing
implementation** in this repository today — this would be a wholly new
capability, not a replacement for an abandoned or hand-rolled formula (unlike
TA-Lib/empyrical-reloaded/`exchange_calendars`, each of which replaced
existing custom or unmaintained code).

Per `MASTER_PLAN.md` row 12 and `DECISIONS.md`'s non-goals section, any
future adoption is bound to the same pattern already established by ADR 0003
for Claude's research overlay: **advisory only, never authoritative over
risk, sizing, or order construction.** Concretely, that means any Riskfolio-
Lib output must never reach `src/trading_research/risk/position_sizing.py`'s
`compute_position_plan` (the existing deterministic, fail-closed sizing
gateway) as anything other than an input a human or a downstream
deterministic rule may consider — analogous to how `research/overlay.py`
maps a `ResearchDecision.rating` to one of four fixed actions via ordinary
Python, never a model-produced field. This PR does not build that boundary
(no adapter is in scope, unlike PR 5's title, which explicitly included
"... and adapter" — PR 12's title is "evaluation only"); it records the
requirement for whichever future PR would.

## 2. License and dependency-weight re-verification (live, 2026-08-23)

**License.** Re-verified against the PyPI JSON API (`https://pypi.org/pypi/riskfolio-lib/json`):
version `7.3.0`, `License: BSD (3-clause)`, classifier `License :: OSI
Approved :: BSD License`, `Requires-Python: >=3.10`. This is conventional
OSI-approved open source — unlike VectorBT's Apache-2.0 + Commons Clause
terms (`DECISIONS.md` D4), Riskfolio-Lib carries no fair-code restriction
requiring an owner exception. This resolves the "OSI-compatible resolution"
open item `DEPENDENCY_MATRIX.md` Section 6 listed as outstanding.

**Hard dependency on VectorBT — confirmed, and confirmed compatible at both
tested ends of the Python >=3.11,<3.15 range (not on the repository's
project-wide Python floor, and not beyond VectorBT 1.1.0's own `<3.15`
ceiling).**
`requires_dist` includes `vectorbt>=0.28.0` (no upper bound), reconfirming
`DEPENDENCY_MATRIX.md`'s existing row ("not a documentation error"). Installed
into a clean scratch virtualenv (`python3 -m venv`, wheel-only:
`pip install --only-binary=:all: riskfolio-lib==7.3.0`, macOS arm64, Python
3.14.5rc1 — this development machine, matching the interpreter PR 4/PR 5 also
verified against), pip's resolver picked **`vectorbt==1.1.0`** — exactly the
version already pinned by the approved `research` extra's
`vectorbt>=1.1.0,<1.2` (`pyproject.toml`, PR 5).

A prior review round of this PR correctly noted that a single Python 3.14
install does not establish compatibility across the full declared
`>=3.11` range — VectorBT 1.1.0's own `Requires-Python: >=3.11,<3.15`
classifier makes 3.11 the actual floor, not 3.14. A second, independent
wheel-only install of the identical `riskfolio-lib==7.3.0` was therefore run
in a second disposable scratch virtualenv on **Python 3.11.15** (macOS
arm64) — the declared floor boundary itself, not an extrapolation from it.
pip's resolver again picked `vectorbt==1.1.0`, the same 82-package closure
resolved, `pip check` again reported no broken requirements, and `import
riskfolio` (`7.3.0`) / `import vectorbt` (`1.1.0`) both succeeded (raw
output in `scratch_output_py311.txt`). This is a live, reproducible
confirmation that Riskfolio-Lib's floor and the already-adopted VectorBT pin
do not conflict on Python >=3.11,<3.15 — the range VectorBT 1.1.0 itself
declares — verified at both the tested development interpreter (3.14.5rc1)
and the declared floor itself (3.11.15) — not just a reading of declared
metadata or an inference from a single interpreter. Neither run tested
Python 3.15+, where VectorBT 1.1.0's own `Requires-Python` ceiling means the
pairing cannot resolve at all without a future VectorBT upgrade.

**Python-floor caveat.** These confirmations are scoped to the two `>=3.11`
interpreters tested (3.11.15 and 3.14.5rc1) and do not establish
conflict-free installation across this repository's full declared floor.
`pyproject.toml` still declares
`requires-python = ">=3.10"` project-wide; PR 5 left that unchanged and scoped
its own `>=3.11` requirement to the `research` extra only
(`DEPENDENCY_MATRIX.md` Section 2). VectorBT 1.1.0 itself requires
`>=3.11,<3.15` (`DEPENDENCY_MATRIX.md`'s VectorBT row), so the adopted
`vectorbt>=1.1.0,<1.2` range cannot resolve at all on a plain Python 3.10
interpreter — a constraint that exists independently of Riskfolio-Lib. A
future environment that combines Riskfolio-Lib with the adopted VectorBT
constraint therefore still requires either the `research` extra's
already-documented `>=3.11` floor or a project-wide floor increase to
`>=3.11`; it would not resolve on a bare Python 3.10 install of the base
project. This evaluation does not claim otherwise.

Full install (Python 3.14.5rc1 run): **82 packages**, wheel-only (no source
compilation on this platform), `pip check` reported **no broken
requirements**, `import riskfolio` and `import vectorbt` both succeeded. The
Python 3.11.15 run resolved the same 82-package closure with the same
clean `pip check` and import result. Key resolved versions (3.14.5rc1 run):

| Package | Resolved version |
|---|---|
| riskfolio-lib | 7.3.0 |
| vectorbt | 1.1.0 |
| numpy | 2.5.2 |
| pandas | 3.0.5 |
| scipy | 1.18.1 |
| cvxpy | 1.9.2 |
| matplotlib | 3.11.1 |
| scikit-learn | 1.9.0 |
| statsmodels | 0.14.6 |
| astropy | 8.0.1 |
| networkx | 3.6.1 |
| arch | 8.0.0 |
| numba / llvmlite | 0.67.0 / 0.49.0 |
| clarabel / scs / pybind11 | 0.11.1 / 3.2.11 / 3.1.0 |

`numpy==2.5.2`/`pandas==3.0.5` are within this project's base `pandas>=2.2.0`
floor and the `numpy>=2.4.6`/`pandas>=3.0` shared floor `DEPENDENCY_MATRIX.md`
Section 5 already documents for the VectorBT/Riskfolio-Lib pairing — no new
floor conflict found.

**Weight, beyond the previously-documented "very heavy" label.** The 82-package
closure includes several transitive dependencies with no other consumer
anywhere in this repository and no relationship to trading/research
computation: `ipywidgets`, `anywidget`, `jupyterlab_widgets`,
`widgetsnbextension` (Jupyter notebook widget support), `plotly` (interactive
charting — this repository already uses `streamlit` for presentation), and
three separate QP/conic solver backends (`clarabel`, `SCS`, `osqp`, plus
`highspy`, `qdldl`) pulled in by `cvxpy`. `astropy` (`astropy-iers-data`
alone is a multi-MB data package) is present for time/coordinate utilities
`cvxpy`'s solver stack does not itself need for portfolio optimization — its
presence here is a Riskfolio-Lib transitive choice, not something this
evaluation could reduce.

## 3. Functional smoke test

`docs/library-migration/pr12/scratch_smoke_test.py` builds a synthetic
252-day/4-asset returns `DataFrame` and calls
`rp.Portfolio(returns=...).optimization(model="Classic", rm="MV",
obj="Sharpe", ...)`. Raw output in `scratch_output.txt`. Findings:

- The optimizer's output is a plain `pandas.DataFrame` of per-asset weights
  summing to 1.0 (long-only default, no negative weights) — a pure advisory
  allocation, structurally free of any `submit_order`/`shares`/`quantity`/
  `order_type`/`side` surface (each explicitly checked and absent). This
  matches `MASTER_PLAN.md` row 12's "advisory allocation output" framing and
  would be no harder to bound at an advisory boundary than VectorBT's
  `Portfolio` object was in PR 5 (`DECISIONS.md` D4's review-fix round).
- Riskfolio-Lib's own internal code emits a `UserWarning` from `cvxpy` about
  its use of the now-deprecated `*` matrix-multiplication operator (visible
  twice in `scratch_output.txt`) — this is Riskfolio-Lib 7.3.0 calling `cvxpy`
  1.9.2 with syntax `cvxpy` itself flags for future removal. Not a functional
  blocker on the version pair verified here, but a maintenance-quality signal
  worth re-checking before any future adoption, since it originates inside
  Riskfolio-Lib's own code, not anything a caller controls.

## 4. Need assessment

`COMPONENT_MATRIX.md`'s "Portfolio optimization" row lists no existing
implementation and no other library evaluated for the same capability — this
is a green-field addition, not a migration off deprecated/abandoned code.
No module under `src/trading_research/` currently constructs a target
allocation across multiple positions; `risk/position_sizing.py` sizes one
trade at a time against explicit inputs. There is no in-repo caller today
that this dependency would unblock.

## 5. Recommendation: defer

Applying the same standard `DECISIONS.md` used for Pandera/PyArrow ("no
concrete current need exists" → **Defer**, `DEPENDENCY_MATRIX.md` Section 4)
rather than the standard applied to Pydantic ("no clear reduction in custom
code" → **do not adopt**, since there is no existing code to compare
against here) or to VectorBT ("owner-approved exception for a scoped,
already-consumed capability" → **Adopt**): Riskfolio-Lib is legally
unblocked (OSI-approved BSD-3, unlike VectorBT) and technically installable
without conflict on Python >=3.11,<3.15 (not on the project's `>=3.10` floor
without also raising it to `>=3.11`, and not on Python 3.15+ without a
future VectorBT upgrade — see Section 2's Python-floor caveat),
confirmed by live resolution at both the 3.11.15 floor boundary and the
3.14.5rc1 development interpreter against the already-adopted
`vectorbt>=1.1.0,<1.2` pin, but its 82-package closure — including several
packages with no other purpose in this codebase (Jupyter widgets, a second
charting library, multiple QP solver backends, `astropy`) — is not justified
by any concrete current consumer. Per the advisory-only constraint this PR
documents in Section 1, even an adopted Riskfolio-Lib could not become
authoritative over sizing or risk decisions, so the near-term payoff of
adding this weight now is small relative to waiting until a specific
portfolio-construction use case is scoped.

**Decision: do not add `riskfolio-lib` to any dependency declaration in
PR 12.** No ADR is required — per the single-ADR rule already established in
`DECISIONS.md` D2 (an ADR is needed only if adoption is recommended), and no
adoption is recommended here. `pyproject.toml` is unchanged by this PR.

**Non-blocking note for a future re-evaluation:** if a concrete portfolio-
construction consumer is later scoped, re-verify the `cvxpy`/Riskfolio-Lib
deprecation warning noted in Section 3 has been resolved upstream, and design
the advisory boundary (Section 1) as a dedicated adapter module — analogous
to `src/trading_research/vector_research/`'s import-boundary and
`metric_source`-labeling pattern (`DECISIONS.md` D4's review-fix round) —
before any dependency is added.
