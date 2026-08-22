# `.agent/` — automation control surface

Two files, both small on purpose.

| File | What it is |
|---|---|
| `config.yaml` | Cost and behaviour limits. Committed **disabled** (`enabled: false`). |
| `state.json` | Machine-only cache of the migration position. Never an authority. |

Neither file contains roadmap content. What each migration phase contains, and
which phase runs next, is decided only by:

```text
docs/library-migration/MASTER_PLAN.md
docs/library-migration/STATUS.md
docs/library-migration/DECISIONS.md
phase records and ADRs
docs/milestones/rebuild/plan.md   (execution protocol)
```

`state.json` holds only what a fresh process cannot re-derive from GitHub —
chiefly `last_reviewed_sha` and `review_round`, which stop one commit being
reviewed (and paid for) twice and stop a review/fix loop running forever.
Everything else in it is recomputed on every run.

Full description: [`../docs/library-migration/AUTOMATION.md`](../docs/library-migration/AUTOMATION.md).

## Current status

Automation Phase A (discovery and state reconciliation) is implemented.
Phases B-F are not. Nothing in this directory can call Claude, call OpenAI,
mutate GitHub, or reach the trading application.

```bash
python scripts/automation/orchestrator.py --dry-run
```
