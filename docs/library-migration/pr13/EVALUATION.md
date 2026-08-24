# PR 13 — SQLAlchemy/Alembic Feasibility and ADR

Scope per `MASTER_PLAN.md` row 13: **no implementation.** No dependency is
added to `pyproject.toml`; no file under `src/`, `scripts/`,
`paper_runtime/src/`, or `backtest_runtime/` is modified by this PR. Row 13
requires two things to be *explicitly tested*, not just reasoned about, plus
an ADR only if adoption is recommended:

(a) whether trigger-protected tables (append-only tables, `real_orders`) can
be touched only via SQLAlchemy Core statements, never ORM-session
flush/unit-of-work — the framing DEPENDENCY_MATRIX.md Section 5 already
proposed, on the theory that "the ORM's unit-of-work flush ordering and
identity-map caching can mask a trigger-rejected write";

(b) whether Alembic's branching revision graph can be constrained to
linear-only history matching `storage/schema_version.py`'s current
monotonic gate (a single strictly-increasing integer ledger, no branch or
merge concept).

`docs/library-migration/pr13/scratch_trigger_orm_vs_core.py` and
`scratch_alembic_linearity.py` (this directory) are scratch reproductions
against the real `sqlalchemy`/`alembic` packages in a disposable virtualenv
(`/tmp/pr13_scratch_venv`, not committed) — not merged into `src/`, mirroring
the pattern PR 2 and PR 12 established for evaluation-only phases. Raw
output in `scratch_trigger_output.txt` and `scratch_alembic_output.txt`;
resolved package versions in `scratch_pip_freeze.txt`.

## 1. Live re-verification (2026-08-23, PyPI JSON API)

| Package | Latest on PyPI | Tested here | License | `Requires-Python` |
|---|---|---|---|---|
| SQLAlchemy | 2.0.52 | 2.0.52 | MIT | `>=3.7` (repo floor `>=3.10` is inside) |
| Alembic | 1.19.1 | 1.18.5 (`alembic>=1.18,<1.19` pin resolved this) | MIT | `>=3.10` (exact match to repo floor) |

Both OSI-approved MIT, both resolve wheel-only with no source compilation,
`pip check` clean (`scratch_pip_freeze.txt`: `alembic==1.18.5`,
`SQLAlchemy==2.0.52`, plus Alembic's own two direct dependencies,
`Mako==1.4.1` and `typing_extensions==4.16.0` — a light closure, matching
`DEPENDENCY_MATRIX.md`'s existing "Light core" / "Light" characterization).
No conflict with any other declared dependency group. Alembic's own newest
release (1.19.1) was not tested directly; the `<1.19` pin used here resolved
1.18.5, the newest release inside that pin, which is recent enough that no
material `alembic.script`/`alembic.command` API used by this evaluation is
expected to differ from 1.19.x — not independently re-verified against
1.19.1.

## 2. Question (a): does the ORM mask a trigger-rejected write?

**Method.** `scratch_trigger_orm_vs_core.py` copy-pastes the *exact*
production DDL for two representative trigger-protected tables — not a
paraphrase — from `storage/trading_schema.py` (`real_orders`, fully
RESERVED: every INSERT/UPDATE/DELETE unconditionally rejected by three
`BEFORE ... BEGIN SELECT RAISE(ABORT, ...) END` triggers) and
`storage/paper_books_schema.py` (`paper_book_cash_ledger`, append-only:
INSERT allowed, UPDATE/DELETE rejected). `PRAGMA foreign_keys=ON` is set on
every connection, matching `storage/database.py`. The engine is a
file-backed SQLite database (a temp file, not `:memory:`), also matching
`storage/database.py`'s own `sqlite3.connect(str(db_path))` — required so
that `engine.connect()` opens a genuinely independent DBAPI connection from
the ORM session's own connection, not the same connection an in-memory
`:memory:` + `StaticPool` engine would silently reuse. Nine cases:

1. **Core INSERT into `real_orders` (control).** Rejected —
   `sqlalchemy.exc.IntegrityError` wrapping the trigger's `RAISE(ABORT, ...)`
   message. Expected; establishes the trigger fires under Core.
2. **ORM `session.add()` + `flush()` into `real_orders`.** Rejected
   identically. Inspected the object's state (`sqlalchemy.inspect`) both
   immediately after the caught exception and after `session.rollback()`:
   `transient=True` throughout, `pending`/`persistent` `False` at every
   point. The object never briefly reports itself as inserted/flushed —
   there is no window where the in-memory identity map disagrees with the
   database.
3. **A caller that forgets to call `session.rollback()` after a rejected
   flush, then tries further, unrelated, legitimate work on the same
   session** (a raw `session.execute(text(...))`, not just another ORM
   flush). SQLAlchemy raises `PendingRollbackError` on the very next
   operation — "This Session's transaction has been rolled back due to a
   previous exception during flush. To begin a new transaction with this
   Session, first issue Session.rollback()." The session refuses to proceed
   in a possibly-inconsistent state; it does not silently keep going.
4. **A single `flush()` batching one legal row (`paper_book_cash_ledger`)
   and one illegal row (`real_orders`) together.** Rejected as one unit;
   before `rollback()`, both objects report `persistent=False` and a read
   from a genuinely independent DBAPI connection (`engine.connect()`
   against the file-backed engine — not the session's own connection)
   shows **zero** rows for the legal entry at both checkpoints. Per case 3's
   `PendingRollbackError`, SQLAlchemy has already rolled back the failed
   flush's transaction internally by the time the `except` block runs,
   before this test's own `session.rollback()` — so this proves the
   **post-failure end-state** is clean via true cross-connection visibility
   (nothing was left behind), not that a still-pending, not-yet-rolled-back
   write was momentarily invisible mid-transaction; no such window is
   claimed. Nothing was masked, partially applied, or persisted-then-reverted.
5. **Core UPDATE/DELETE against `paper_book_cash_ledger` (control).** Both
   rejected, confirming the append-only (INSERT-allowed) trigger pair fires
   under Core exactly as the fully-reserved `real_orders` triggers did.
6. **An ORM relationship configured with `cascade="all, delete-orphan"`,
   deleting a parent (`paper_books`) row that has a `paper_book_cash_ledger`
   child.** This is the sharpest form of the original concern: a cascade the
   developer did not write an explicit DELETE for. It still issues a real
   SQL `DELETE` against the child table, and the trigger still rejects it —
   the cascade does not resolve itself purely in Python/the identity map
   without touching the database.
7. **ORM UPDATE of an already-loaded, existing row.** Cases 2 and 6 cover a
   new-object INSERT and a cascade DELETE, but not an UPDATE reached through
   the identity map — precisely the scenario a future mapper hits when it
   loads an existing `paper_book_cash_ledger` row, changes a protected
   field, and flushes. This case loads the row case 5 inserted via
   `session.get()`, mutates `amount_usd` in place, and flushes: rejected
   identically (`IntegrityError`). Critically, re-reading the mutated
   attribute *before* calling `session.rollback()` was attempted and itself
   raised `PendingRollbackError` — SQLAlchemy expires an object's attributes
   after a failed UPDATE flush, and a session left dirty by an unhandled
   flush error refuses to serve even a read of its own expired attribute,
   the same fail-closed behavior case 3 pins for unrelated work. A read from
   a genuinely independent connection during that same window shows the
   original, unmutated value; after `session.rollback()` and
   `session.expire()`, re-reading the attribute triggers a fresh `SELECT`
   that returns the same original value — the identity map does not retain
   or later resurface the rejected mutation.
8. **The Core-only boundary itself, enforced and adversarially tested.**
   Question (a) asks not only whether the trigger fires under ORM usage
   (cases 2–7), but whether trigger-protected tables can be *constrained to*
   Core-only statements, never ORM-session flush/unit-of-work. Cases 2–7
   demonstrate the trigger still fires when ORM usage is attempted; they do
   not by themselves prevent that usage. Case 8 closes that gap: a
   `before_flush` guard registered once on the ORM `Session` class
   (`TriggerProtectedTableORMGuard`, ~10 lines) rejects any flush touching
   `real_orders` or `paper_book_cash_ledger` *before* any SQL is emitted —
   verified by asserting the guard's own exception type is raised, not
   `IntegrityError` from the trigger, which would mean the guard fired too
   late. Tested through two distinct, independently permitted session
   construction paths — a `sessionmaker()`-produced session and a directly
   constructed `Session(bind=engine)` — because both route through the same
   class-level event and must both be blocked for the boundary to hold
   generally (as would `scoped_session` and `Session.begin()`, which wrap
   the same class). With the guard installed, Core statements against the
   same tables (case 5's insert) are re-verified to still succeed unmodified
   — the guard is ORM-flush-only and never intercepts Core.
9. **The guard's table coverage, checked against every trigger-protected
   table production actually defines, not just the two reproduced here.**
   Cases 1–8 only ever exercise `real_orders`/`paper_book_cash_ledger`,
   because those are the two tables with real DDL copy-pasted into this
   file. A hand-maintained allowlist limited to those two would silently
   admit ORM writes to the other 48 tables production protects with a
   write-rejecting trigger — including `paper_book_fills`,
   `research_attempts`, `research_attempt_failures`, and
   `research_cycle_provider_provenance_links`. Case 9 closes that gap
   structurally: `TRIGGER_PROTECTED_TABLES` is no longer a hardcoded set but
   is derived by
   `discover_trigger_protected_tables_from_production_schema()`, which scans
   every `src/trading_research/storage/*_schema.py` module for a `CREATE
   TRIGGER ... BEFORE {INSERT,UPDATE,DELETE} ON <table> ... RAISE(ABORT ...
   END;` block (unconditional or `WHEN`-conditional, e.g. `recommendations`'
   frozen-row guard) and collects `<table>` — currently **50** tables, not
   2. The guard (unchanged logic) is then exercised against a disposable
   synthetic single-column table for every one of the 50 discovered names
   (full production DDL for 50 tables is unnecessary: the guard only
   inspects `obj.__table__.name` and fires before any SQL reaches a real
   trigger), and every single one is rejected pre-SQL by
   `TriggerProtectedTableORMGuard`. Because the policy is re-derived from
   the same production files on every run rather than hand-maintained, a
   future table gaining a write-rejecting trigger is picked up automatically
   the next time this reproduction (or its regression test) runs; it cannot
   silently regress to a stale hardcoded list the way the original
   two-table allowlist did.

**Finding: the masking hypothesis is not substantiated against SQLAlchemy
2.0.52 for either representative table.** Every one of the seven masking
cases (2–7) — including the two adversarial ones beyond the original
hypothesis (an unhandled failed flush, and an ORM relationship cascade) plus
an ORM UPDATE through the identity map — fails closed: the trigger fires,
the exception propagates, and the ORM's own session-state machine (not this
evaluation's code) prevents any further work, or even any attribute read, on
a session left in an inconsistent state. `DEPENDENCY_MATRIX.md` Section 5's
concern — "the ORM's unit-of-work flush ordering and identity-map caching
can mask a trigger-rejected write" — is **withdrawn as unsubstantiated** for
the tested version and table shapes, across INSERT, UPDATE, and cascade
DELETE; see "Correction" below. This does not prove no ORM version or usage
pattern could ever produce a masking effect (bulk `insert()`/`update()`
constructs that skip ORM events, or a future SQLAlchemy release, were not
tested), but no such effect was found under direct, adversarial testing
here.

**Question (a) is answered, not just recommended.** Separately from the
masking withdrawal, case 8 proves trigger-protected tables *can* be
constrained to Core-only statements: a single class-level `before_flush`
guard blocks every permitted ORM session construction path tested, before
any SQL reaches the trigger, while Core access is unaffected. Case 9
additionally proves that guard's *policy* — which tables it protects — is
complete against every table production currently defines, not just the two
tables this reproduction happens to carry real DDL for, and cannot silently
go stale as production's schema grows. Any future adoption that wants the
Core-only boundary enforced, not merely followed by convention, has a
proven, minimal, self-updating mechanism to enforce it — not because the
ORM is unsafe (Section 2's masking finding says it is not), but because
Core statements map 1:1 onto the exact SQL the triggers were written
against, which is easier to audit line-by-line for a safety-critical table
than an ORM unit-of-work whose generated SQL depends on session state,
mapper configuration, and cascade settings. This is a simplicity/
auditability preference for a High-risk decision, reinforced by a proven
enforcement mechanism, not merely asserted as a convention.

## 3. Question (b): can Alembic be constrained to linear-only history?

**Method.** `scratch_alembic_linearity.py` builds a real, disposable Alembic
environment (an actual `script.py.mako`, a real `versions/` directory, a
real `alembic.config.Config`) against a throwaway SQLite database, using
`alembic.command` and `alembic.script.ScriptDirectory` — not a description
of documented behavior. Six cases:

1. **A linear 3-revision chain (control).** `ScriptDirectory.get_heads()`
   returns exactly one head, as expected.
2. **A second revision targeting an already-referenced parent (a real
   branch).** Alembic's own default **refuses this outright**:
   `command.revision(..., head="0003")` a second time raises
   `CommandError: Revision 0003 is not a head revision; please specify
   --splice to create a new branch from this revision`. This is a built-in
   guard this evaluation did not have to add — Alembic already resists an
   *accidental* branch. Only after explicitly passing `splice=True` (the
   `--splice` CLI flag) does the branch succeed, producing two heads.
3. **`alembic upgrade head` (singular target) with two heads present.**
   Alembic itself raises `CommandError: Multiple head revisions are present
   for given argument 'head'; please specify a specific target revision,
   '<branchname>@head' to narrow to a specific head, or 'heads' for all
   heads.** A second built-in guard: an ambiguous upgrade target is refused,
   not silently resolved to one arbitrary head.
4. **A custom "linear-only" gate function** (`linear_only_gate()` in the
   scratch script — about 15 lines using only `ScriptDirectory.get_heads()`
   and `walk_revisions()`) asserting exactly one head **and** that no
   revision has more than one child **and** that no revision's
   `down_revision` is a tuple (Alembic's representation of a merge
   revision's multiple parents). Run against the branched graph from Case 2:
   it correctly reports two violations (2 heads; revision `0003` has 2
   children).
5. **`alembic merge` reconciling the two branch heads into one.** This is
   the library's own documented reconciliation mechanism. It does restore
   `get_heads() == 1`, **but the gate still (correctly) flags a
   violation** — the merge revision's `down_revision` is the tuple
   `('0004a', '0004b')`, which is structurally a merge point (two parents),
   not a linear predecessor. This confirms "exactly one head" alone is
   **not** sufficient to prove linearity; the no-merge-revision check is
   required in addition, because a DAG can converge to one head without
   ever being a single chain.
6. **The actual repair a linear-only policy would force**: delete the
   losing branch revision file and the merge revision, leaving the winning
   branch (`0004a`) as a normal single-parent continuation of `0003`. The
   gate then reports zero violations and one head — a real linear chain,
   restored by file deletion and rebase, not by merging. This is the same
   repair shape `schema_version.py`'s dict-based ledger already has by
   construction (there is nothing to branch in a `dict[int, ...]` keyed by a
   strictly increasing integer).
7. **A revision with a single `depends_on` edge.** `depends_on` is a
   dependency mechanism separate from `down_revision`: Alembic does not
   count it toward `get_heads()` or toward a revision's down-revision
   children, so a revision can carry a `depends_on` edge while the graph
   still reports exactly one head and no down-revision branch. The initial
   version of `linear_only_gate()` inspected only `down_revision` and
   parent fan-out, so it evaluated zero violations against this case: a
   real gap, not a hypothetical one. The gate now also rejects any revision
   whose
   `dependencies` attribute is non-empty, and re-running this case confirms
   it: one head, one violation reported, correctly naming the `depends_on`
   target.
8. **A revision with multiple `depends_on` targets.** Same result: the
   corrected gate flags the revision, naming every dependency target in the
   violation message.

**Finding: yes, Alembic's branching graph can be constrained to
linear-only history, but only with an added, maintained guard that must
account for `depends_on` as well as `down_revision` — this is not the
library's default end-state, only its default resistance.** Alembic
already resists *accidental* branching
(Case 2's un-spliced refusal, Case 3's ambiguous-upgrade refusal) better
than this evaluation expected going in, but a deliberate `--splice`, an
`alembic merge`, or a `depends_on` edge (cases 7–8) all still produce a
graph shape (`schema_version.py`'s ledger has no counterpart for) that a
gate checking `get_heads()`, down-revision fan-out, merge revisions, *and*
`dependencies` — exactly the corrected `linear_only_gate()` function this
evaluation wrote and proved catches every case tested, including
`depends_on` — would be needed to keep out permanently. That gate does not
exist today and would need to be built, tested, and kept in CI
indefinitely if Alembic were adopted; it is not something Alembic ships.
A `down_revision`-only gate would have been a real defect: it silently
accepts a `depends_on` edge that has no equivalent in the monotonic
integer ledger it is meant to enforce equivalence with.

## 4. Need assessment

`COMPONENT_MATRIX.md`'s "Persistence" and "Migrations" rows both list the
existing hand-written `storage/*_schema.py` DDL modules and
`storage/schema_version.py`'s ordered-migration ledger as already
"Category B: Evaluate" — not as unmaintained or broken. Distinct from
TA-Lib/empyrical-reloaded/`exchange_calendars` (each replacing abandoned or
hand-rolled formula code), there is no maintenance, correctness, or
capability gap driving this evaluation:
`src/trading_research/storage/schema_version.py` already gives idempotent
additive DDL, an ordered non-idempotent-migration ledger, and a
forward-version refusal gate, entirely in the standard library plus
`sqlite3`. No module under `src/trading_research/storage/` currently has a
problem SQLAlchemy/Alembic would solve that the existing pattern does not
already solve.

## 5. Recommendation: defer, not adopt

Applying the same bar `DECISIONS.md` already uses for Pandera/PyArrow/
Riskfolio-Lib ("no concrete current need exists" → **Defer**) rather than
Pydantic's bar ("no clear reduction in custom code" → do not adopt, D2) or
VectorBT's ("owner-approved exception for a scoped, already-consumed
capability" → Adopt, D4): both libraries are legally unblocked (OSI-approved
MIT, matching this project's `>=3.10` floor exactly for Alembic and well
within it for SQLAlchemy) and now **more** technically de-risked than the
PR 0 record assumed — the trigger-masking concern that was this evaluation's
main open technical question is withdrawn (Section 2) — but adopting either
would mean migrating roughly twenty existing hand-written schema/repository
modules, including every safety-critical trigger-protected table, for no
current capability gap, while *adding* a maintenance obligation
`schema_version.py` does not have today: a custom linear-only CI gate
(Section 3) to keep Alembic's revision DAG from drifting away from the
strictly-ordered ledger model this repository already relies on for
correctness elsewhere (the forward-version refusal in
`check_schema_not_forward_versioned`, the ordered non-idempotent-migration
guarantee in `apply_pending_schema_migrations`). The near-term payoff of
that migration is small relative to its cost and the new surface it would
require maintaining.

**Decision: do not add `sqlalchemy` or `alembic` to any dependency
declaration in PR 13.** No ADR is required — per the single-ADR rule already
established in `DECISIONS.md` D2 and reapplied in D10, an ADR is needed only
if adoption is recommended, and none is here.

**Non-blocking notes for a future re-evaluation**, if a concrete need for a
richer persistence/migration layer is later scoped:

* Re-verify against Alembic 1.19.1 (or whatever is then current) directly,
  not only inferred from the 1.18.5 pin tested here.
* Build the linear-only CI gate (Section 3, `linear_only_gate()`, including
  its `depends_on` check added after cases 7–8) as an actual blocking check
  *before* any Alembic revision is committed, not after — the gate is cheap
  to write but easy to forget once branching (or a `depends_on` edge)
  becomes possible.
* Keep trigger-protected tables on Core-only statements, enforced with the
  `before_flush` guard proven in Section 2 case 8, with its table policy
  derived from the production schema per case 9 (not merely followed by
  convention) — an auditability preference, not a correctness finding since
  Section 2 found no masking risk, and worth re-confirming against whatever
  SQLAlchemy version is current at that time.
* Bulk Core constructs (`sqlalchemy.dml.Insert`/`Update` used for
  multi-row bulk operations, which bypass ORM events but not Core/trigger
  execution) were not separately tested here and should be, since they were
  named as an open question in Section 2's finding.
