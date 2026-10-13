# Alpaca Paper Operations

This runbook is for Alpaca paper trading only. Local simulation is the shipped
default. Do not use a live account or live endpoint.

## Enable deliberately

Do not edit the tracked `config/paper_books.yaml`. Copy it to a file outside
the repository and pass it with `--paper-books-config <path>` to every
external-paper command that accepts the flag (account check, baseline
activation, preview, submit, retry, reconcile, cancel). A missing or invalid override fails closed
and never falls back to the tracked default. In the copy, set
`paper_books.enabled: true`, enable exactly one book, then set
`external_broker.enabled: true`, `allow_order_submission: true`, and
`enabled_book_ids` to that one book. Keep `execution.provider:
local_simulated`; recurring execution uses that path and never mutates
Alpaca.

Keep the standing limits in the copy: `maximum_order_notional_usd: "50.00"`,
`maximum_daily_notional_usd: "150.00"`, and `max_symbol_concentration_weight`
`"0.10"`. These are configured values, not hard-coded ceilings (owner policy
B, `docs/library-migration/DECISIONS.md` D16). Raising one is a
single-validation exception that the operator approves explicitly, as
Milestone 22 did with a `$400` cap. It belongs only in that operator's
override file, never in the tracked config. The gateway always applies the
lower of `external_broker.maximum_order_notional_usd` and
`risk.max_order_notional_usd`.

In `config/paper_runtime.yaml`, keep provider `alpaca`, environment/mode
`paper`, and base URL exactly `https://paper-api.alpaca.markets`. The isolated
runtime requires `ALPACA_IS_PAPER=true` plus paper credentials. Credentials
alone enable nothing.

Credentials reach the isolated runtime process only two ways (Milestone
11.1): (a) set `ALPACA_API_KEY`/`ALPACA_API_SECRET`/`ALPACA_IS_PAPER`/
`ALPACA_BASE_URL`/`PAPER_BROKER_PROVIDER` directly in the environment that
launches the CLI — the main process passes these specific names through
verbatim, never parsing them; or (b) set `PAPER_RUNTIME_ENV_FILE` to a
dedicated, Alpaca-only dotenv file stored outside this repository — the
runtime loads exactly that file and never scans the filesystem for one. Do
not rely on this repository's own `.env`; the runtime does not discover it.

## Account check, preview, submit

`external-paper-submit` runs only after the operator gives the exact phrase
`Approved, submit this exact paper order.` for that previewed order. No
other wording counts as approval. The CLI does not check the phrase: it is
the operator gate an agent must observe before running the command. The CLI
enforces the configuration gates, a recent preview, `--operator`,
`--reason`, account fingerprinting, and the notional caps. The phrase never
enables live trading. Append `--paper-books-config <path>` to each command
below.

```bash
python -m trading_research.cli external-paper-account-check --book-id BASELINE

python -m trading_research.cli external-paper-preview \
  --book-id BASELINE --intent-id <intent-id> --operator <name>

python -m trading_research.cli external-paper-submit \
  --book-id BASELINE --intent-id <intent-id> --preview-id <preview-id> \
  --operator <name> --reason "<reason>"
```

Inspect bounded local evidence:

```bash
python -m trading_research.cli external-paper-order-show \
  --book-id BASELINE --client-order-id <client-order-id>
```

Show the submission queue's live, derived status (Milestone 11.1; read-only,
no runtime client or credentials needed):

```bash
python -m trading_research.cli external-paper-queue-show --book-id BASELINE
```

Each row shows the queue's client order ID and current status —
`AWAITING_OPERATOR_EXTERNAL_SUBMISSION`, `PREVIEWED`, `SUBMISSION_REQUESTED`,
`SUBMITTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`, `EXPIRED`,
`UNKNOWN_REQUIRES_RECONCILIATION`, or `BLOCKED_BY_RECONCILIATION` — derived
fresh from the order-event chain every call, never a stale stored value.

## Reconcile ambiguity or drift

```bash
python -m trading_research.cli external-paper-reconcile \
  --book-id BASELINE --client-order-id <client-order-id>
```

Never rerun submit after `UNKNOWN_REQUIRES_RECONCILIATION`. Reconcile first.
If the deterministic client order ID is found, local state is repaired without
submission. If and only if reconciliation persists authoritative `NOT_FOUND`,
perform the bounded explicit retry:

```bash
python -m trading_research.cli external-paper-retry-submit \
  --book-id BASELINE --intent-id <intent-id> \
  --operator <name> --reason "authoritative not-found retry"
```

Account, namespace, order, fill, cash, or position mismatches are critical and
block later external submission. Correct the external condition, then rerun
reconciliation. Do not edit historical events, fills, or reconciliations.

An authoritative `NOT_FOUND` lookup authorizes exactly one retry of the exact
ambiguous attempt it was taken against (Milestone 11.1): it is consumed the
moment the retry is submitted, and a stale or mismatched lookup (wrong
attempt, wrong payload, already consumed) is rejected — a fresh reconciliation
is required before another retry.

## Explicit cancellation

```bash
python -m trading_research.cli external-paper-cancel \
  --book-id BASELINE --client-order-id <client-order-id> \
  --operator <name> --reason "<reason>"
```

There is no automatic cancellation. An ambiguous cancel outcome requires the
same reconciliation flow.

## Rollback

Set `external_broker.allow_order_submission: false`, then
`external_broker.enabled: false` and clear `enabled_book_ids`. This does not
cancel existing paper orders; inspect/reconcile and explicitly cancel them
first if required. Local simulation remains available for future intents.

## Optional real-paper smoke

The smoke is skipped by default. It requires
`RUN_EXTERNAL_PAPER_BROKER_TESTS=true`, `ALPACA_IS_PAPER=true`, enabled
external submission for one book, exact paper endpoint verification, an
approved whole-share limit intent below all caps, and explicit operator input.
Prefer a non-marketable limit. Run only the dedicated marked test:

```bash
RUN_EXTERNAL_PAPER_BROKER_TESTS=true \
EXTERNAL_PAPER_SMOKE_BOOK_ID=BASELINE \
EXTERNAL_PAPER_SMOKE_INTENT_ID=<approved-intent-id> \
EXTERNAL_PAPER_SMOKE_OPERATOR=<operator> \
pytest -m external_paper_broker \
  tests/integration/test_external_paper_broker_smoke.py -q --tb=short
```

The test performs account check, preview, explicit submit, reconciliation,
and explicit cancellation if the order remains open. It never prints
credentials or raw account IDs.
