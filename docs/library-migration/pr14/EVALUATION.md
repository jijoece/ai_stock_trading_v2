# PR 14 — APScheduler/Tenacity Feasibility

Scope per `MASTER_PLAN.md` row 14 (reframed at PR 0 from "replace" to
"coexist"): decide whether APScheduler v3 can take over any part of this
repository's due-time scheduling, given its existing lease/generation-fencing
logic must stay custom regardless; and give Tenacity's retry scoping a
structural test proving it cannot silently wrap the ambiguous external-broker
retry path (`paper_books/external_broker.py`). Risk classified High
(decision) — Opus review — because the outcome determines whether a new
in-process dependency is introduced into safety-critical scheduling/retry
code, not because either package is legally or technically fragile.

## 1. Live re-verification (2026-08-30, PyPI JSON API)

| Package | Verified version | Requires-Python | License | Source |
|---|---|---|---|---|
| APScheduler | 3.11.3 stable; latest overall release is `4.0.0a6` (alpha) | `>=3.8` | MIT | PyPI JSON API, 2026-08-30 |
| Tenacity | 9.1.4 | `>=3.10` | Apache-2.0 | PyPI JSON API, 2026-08-30 |

Both figures are unchanged from the PR 0 record (`DEPENDENCY_MATRIX.md`
Section 1). APScheduler v4 is still alpha-only nine releases in a row
(`4.0.0a1`-`4.0.0a6` are the newest entries in its release list) — the "not
production-ready" characterization stands unmodified; this evaluation
considers v3.11.3 only. Neither package is a dependency of this repository
today (`pyproject.toml`, `paper_runtime/pyproject.toml` — confirmed by direct
grep, no match).

## 2. Question (a): can APScheduler take over any part of the existing due-time scheduling?

Two independent, already-tested, pure due-time computations exist today:

* `paper_books/recurring_scheduler.py::calculate_due_slot` — timezone-aware
  local wall-time comparison against a configured hour/minute, gated by
  `evaluation/market_calendar.py::is_trading_day` (the `exchange_calendars`
  XNYS calendar adopted in PR 3), producing a deterministic
  `intended_schedule_id`.
* `shadow/schedule.py::resolve_due_status` — a richer state machine:
  intended-time computation, a bounded run window
  (`_run_window_bounds`), market-holiday gating, catch-up-window
  classification (`MISSED_WITHIN_CATCHUP` vs. `MISSED_TOO_OLD`, driven by
  `max_catch_up_cycles`), and an idempotency check against
  `shadow_scheduler_runs` for an already-completed slot.

**This repository's architecture already forbids the primary way APScheduler
is normally used.** ADR 0005 ("Shadow operations are a bounded,
single-invocation control layer... not a daemon") Decision 1 is explicit and
Accepted, unamended: *"There is no `while True` loop, no background thread,
and no self-installing OS schedule anywhere in this repository. Recurring
behavior is entirely the responsibility of whatever invokes the process
(cron, launchd, a CI scheduler, or an operator typing the command)."*
`shadow/scheduler.py`'s own module docstring repeats this verbatim ("no
loop, no daemon, no self-installing OS schedule anywhere in this module").
APScheduler's primary API surface — `BlockingScheduler`/`AsyncIOScheduler`
plus a persistent jobstore, running an internal loop that fires callables at
computed times without external re-invocation — **is** that daemon. Adopting
it in that mode would not be a library substitution; it would reopen an
Accepted ADR's core decision, which is out of this PR's bounded scope
(feasibility/decision only, not an ADR amendment).

**The narrower "coexist" reading — using only APScheduler's stateless
trigger classes (e.g. `CronTrigger.get_next_fire_time(previous, now)`) as a
drop-in for the hand-written due-time arithmetic, never running the
scheduler loop itself — was tested directly** (scratch reproduction:
`pr14/scratch_apscheduler_trigger_gaps.py`, raw output
`pr14/scratch_apscheduler_output.txt`, `apscheduler==3.11.3` installed alone
in a disposable venv, pip freeze captured in
`pr14/scratch_pip_freeze.txt`). Two concrete gaps, not just documentation
claims:

1. **No exchange-calendar awareness.** A `CronTrigger(hour=9, minute=30,
   day_of_week="mon-fri", ...)` — the closest bare-APScheduler equivalent of
   this repository's "9:30 ET, market days only" schedules — computed its
   next fire time as **2026-09-07 09:30:00-04:00 for a probe just after the
   preceding Friday's slot**. 2026-09-07 is Labor Day, a real NYSE closure;
   `CronTrigger`'s `day_of_week` filter has no concept of a fixed-date or
   floating federal/exchange holiday, let alone the early-closes and one-off
   closures `evaluation/market_calendar.py` already handles via
   `exchange_calendars`. Making a `CronTrigger` (or a custom `Trigger`
   subclass) holiday-aware would mean wiring the same `exchange_calendars`
   XNYS calendar into it that this module already calls directly — not a
   reduction in custom code, an added layer around an unrelated new
   dependency's trigger-plugin interface.
2. **No catch-up/idempotency concept.** `IntervalTrigger`'s and
   `CronTrigger`'s public surface (confirmed directly:
   `['end_date', 'get_next_fire_time', 'interval', 'interval_length',
   'jitter', 'start_date', 'timezone']` for `IntervalTrigger`) has no notion
   of "this due slot was already attempted and is in a bounded catch-up
   window" vs. "too old to recover" vs. "already completed" — every trigger
   class only answers "what is the next fire time after `previous_fire_time`
   given `now`", stateless per call. `resolve_due_status`'s
   `MISSED_WITHIN_CATCHUP`/`MISSED_TOO_OLD`/`ALREADY_COMPLETED`
   classification depends entirely on this repository's own persisted
   `shadow_scheduler_runs` table — APScheduler would contribute nothing to
   that logic even if adopted for the bare next-fire-time computation.

**The documented jobstore/lease limitation was independently re-confirmed at
the source-code level, not just asserted from the PR 0 record.**
`apscheduler.schedulers.base.BaseScheduler.__init__` constructs
`self._jobstores_lock`/`self._executors_lock`/`self._listeners_lock` via
`self._create_lock()`, a plain in-process lock (`threading.Lock` or
equivalent) guarding the scheduler's own in-memory bookkeeping — not a
cross-process distributed lease. `SQLAlchemyJobStore` (confirmed via its own
docstring: "Stores jobs in a database table using SQLAlchemy") persists job
definitions/next-run-times to a shared table but provides no fencing token,
no heartbeat/TTL, and no owner-conflict-detection primitive comparable to
this repository's `paper_recurring_scheduler_leases`
(`acquire_recurring_lease`/`heartbeat_recurring_lease`/
`release_recurring_lease`) or `shadow_run_leases` (`shadow/lease.py`). This
reconfirms, from the library's own source rather than only from PR 0's
prose, `MASTER_PLAN.md` row 14's own premise: "existing lease/
generation-fencing logic stays custom" is not optional even if APScheduler
were adopted for triggering.

**Conclusion: defer, do not adopt APScheduler**, for two independent
reasons, either alone sufficient: (1) its natural mode of use (an in-process
scheduler loop) directly conflicts with ADR 0005 Decision 1, an Accepted,
unamended architectural decision this PR is not scoped to reopen; (2) even
its narrowest possible use (stateless trigger classes only, no loop) adds a
new dependency while solving none of this repository's actual scheduling
complexity (market-calendar awareness, catch-up windows, idempotency,
distributed leasing) — all of that must remain hand-written regardless, so
the "coexist" framing does not identify any current line of custom code
APScheduler would actually let this repository delete.

## 3. Question (b): can Tenacity be structurally scoped away from the ambiguous external-broker retry path?

`DECISIONS.md`'s PR 0 record already established Tenacity's retry scoping is
decorator/context-manager based with no global interception — reconfirmed
here by inspecting the current 9.1.4 public API (`tenacity.retry`, a
decorator factory; `tenacity.Retrying`, an explicit context-manager/callable
class — neither monkeypatches or globally intercepts anything; scoping is
100% a function of which call sites choose to use them). This means the
ambiguous-broker-retry path
(`paper_books/external_broker.py::retry_external_paper_order`,
`_prepare_external_retry_attempt`, `refresh_retry_preview` — the
fresh-authoritative-NOT_FOUND-evidence-gated retry machinery documented in
that module's own docstrings) can be kept entirely free of Tenacity simply
by never importing it in that file — there is no way for a decorator applied
elsewhere in the codebase to reach into this module's functions.

**Structural test added** (real code, not evaluation-only, per
`MASTER_PLAN.md` row 14's explicit requirement):
`tests/unit/test_external_broker_no_tenacity_import_boundary.py`. Modeled
directly on `tests/unit/test_lumibot_import_boundary.py` (the existing
AST-based import-boundary precedent) — parses `external_broker.py`'s source
with `ast`, asserts no `import tenacity` / `from tenacity import ...` node
exists, and includes a second test proving the detector actually fires
against a synthetic offending file (mirroring the LumiBot test's own
proof-test pattern) rather than vacuously passing. It runs unconditionally
in every environment (no `importorskip`, no dependency on `tenacity` being
installed), and will fail — not skip — the moment anyone imports `tenacity`
into this specific file, whether or not `tenacity` is ever added as a
dependency elsewhere in the repository.

This test is intentionally scoped to `external_broker.py` alone, not the
whole `src/trading_research` tree: `COMPONENT_MATRIX.md`'s "Generic
transient retries" row leaves the per-provider hand-rolled backoff in
`evidence_providers/http_client.py` open to a future, separately-evaluated
Tenacity adoption for ordinary transport retries — only the
ambiguous-broker-retry path is structurally excluded here.

## 4. Need assessment

`evidence_providers/http_client.py::HttpJsonClient` already implements
bounded exponential backoff with `Retry-After` header support, rate-limiter
integration, and an injectable `backoff_sleep_fn` for deterministic testing —
fully custom, already tested, no defect motivating replacement. Adopting
Tenacity there would trade this domain-specific logic (Retry-After parsing,
rate-limiter coordination, the notify/observability callback) for Tenacity's
generic retry primitives plus custom glue code to reproduce the same
behavior — not a net reduction, and no current capability gap exists per
`COMPONENT_MATRIX.md`'s "Generic transient retries" row. No other module in
`src/trading_research` was found with an unmet generic-retry need.

## 5. Recommendation: coexist framing resolved as "defer both"; structural test added regardless

**APScheduler: not added to any dependency declaration.** Neither its
in-process scheduler loop (conflicts with ADR 0005 Decision 1) nor its
stateless trigger classes alone (solves none of this repository's actual
scheduling complexity) justify the new dependency. Re-evaluate only if a
future milestone proposes reopening ADR 0005 itself.

**Tenacity: not added to any dependency declaration**, consistent with the
"no concrete current need" bar already applied to Pandera/PyArrow/
Riskfolio-Lib/SQLAlchemy/Alembic (`DECISIONS.md` D4/D10/D11). **The
structural test guarding the ambiguous-broker-retry path is added
regardless** — it costs nothing (no new dependency, does not require
`tenacity` to be installed), and it converts `DECISIONS.md`'s existing prose
recommendation into an enforced, blocking regression test before any future
PR could add `tenacity` and accidentally wrap
`retry_external_paper_order`/`_prepare_external_retry_attempt`/
`refresh_retry_preview` with it.

No ADR was produced — per `DECISIONS.md` D2's single-ADR rule (an ADR is
required only when adoption is recommended), reapplied at D9/D10/D11 and
again here: neither package is recommended for adoption, so none is
required.
