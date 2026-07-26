# Library-migration prompt archive

This directory is the execution-prompt archive for the library-first
migration described in `docs/library-migration/plan.md`: `plan.md` is the
original orchestrator prompt, and each numbered file (`1.md`, `2.md`, `3.md`,
`4.md`, `5.md`, ...) is the bounded, self-contained prompt used to drive one
migration PR or one round of review-driven corrections to a prior PR, per the
"per-PR execution protocol" in `plan.md`.

**Ownership:** the repository owner (migration architect/coordinator role in
`plan.md`) authors and maintains these prompts. They are read, not generated,
by the Claude Code session implementing a given PR.

**Status:** historical/instructional, not a specification of current
behavior — for what the code actually does today, use
`docs/library-migration/STATUS.md`, `MASTER_PLAN.md`, `COMPONENT_MATRIX.md`,
and `DECISIONS.md`, per `docs/INDEX.md`'s authority order.

**Convention:** add a new numbered file here, in its own documentation-only
PR, when a prompt is authoritative enough to keep for future reference. Do
not bundle a new prompt file into the same PR as the code change it
describes — see `docs/milestones/rebuild/5.md`'s "Remove scope pollution"
finding, which flagged exactly that pattern.
