# Runbook: Build the intraday-gap earnings CSV (FMP free plan + SEC)

Builds `data/earnings.csv` for the
[intraday earnings-gap backtest](intraday-gap-backtest.md) with the
`build-earnings-csv` command. Research data only: no broker, model call,
scheduler or paper order is involved. Nothing here is financial advice.

## Where each column comes from

| Column | Source |
|---|---|
| `symbol`, `report_date`, `eps_actual`, `eps_estimate` | FMP `stable/earnings?symbol=...` (free plan: about four past quarters per symbol, no report timing) |
| `timing`, `release_time` | Acceptance time of the SEC Form 8-K with Item 2.02 ("Results of Operations") filed on the report date. Before 09:30 ET is `am`; from 16:00 ET is `pm` |
| `fiscal_period` | `report-<report_date>` (FMP's free response has no fiscal period) |
| `source`, `captured_at` | `fmp-free+sec-8k-2.02` and the time the FMP file was fetched |

The 8-K follows the press release, so its acceptance time is never earlier
than the real release. The strategy can only wait longer, never see a report
early. Reports are dropped, and counted by reason, when: no Item 2.02 8-K was
accepted that day, the 8-K was accepted during the regular session, the date
is not a trading day, the report is in the future or outside `--start`/`--end`,
or either EPS value is null.

## Limits of the free plan

- **About one year of history.** Step 7's out-of-sample slice is 40% of event
  dates, and the gate needs 100 trades per variant. With four quarters per
  symbol and a candidate filter that rejects most events, expect too few
  trades for a verdict unless the universe is very large.
- **Revised estimates.** FMP returns its current `epsEstimated`, which may
  have been revised after the report. The backtest report already carries a
  point-in-time caveat.
- **Daily request cap.** `fetch-fmp` stops after `--max-requests` (default
  240) or at the first rate-limit response. Rerun it on later days: saved
  symbols are skipped.

## Fetch results and exit codes

Each fetch prints `saved`, `already_saved`, `failed` (by symbol, error class
only), `remaining` and `complete`. `remaining` counts every requested symbol
that still has no current raw file, whether it failed, had no CIK, or was not
reached before a stop. An empty FMP history (`[]`) is not saved: it is
reported as `failed: empty_response`, because a symbol with no coverage must
not count as complete. The command exits `0` only when `complete` is true,
`3` when it saved what it could but left work outstanding (rerun it), and `2`
on an error. Do not build until both fetches report `complete: true`.

The build enforces this. If any requested symbol has no FMP or SEC raw file,
or its FMP file holds no rows (listed as `symbols_with_empty_fmp_history`), it
exits `2` with `INCOMPLETE_RAW_DATA` and leaves the existing output alone.
To exclude those symbols on purpose, pass `--accept-missing` with a
comma-separated list that names exactly the missing symbols (for example
`--accept-missing BBB,CCC`). A list that leaves a symbol out, or names a
symbol that is not missing, is rejected. A successful build records the
accepted exclusions as `accepted_missing` next to its SHA-256.

## Refreshing a saved universe

Saved raw files are snapshots and are never refetched by default. FMP's
endpoint is a rolling window and SEC submissions accumulate new filings, so
before building a later sample, refetch both with the same cutoff date:

```bash
.venv/bin/python -m trading_research.cli build-earnings-csv fetch-fmp \
  --symbols-file data/earnings-symbols.txt --refresh-before 2027-02-01
.venv/bin/python -m trading_research.cli build-earnings-csv fetch-sec \
  --symbols-file data/earnings-symbols.txt --refresh-before 2027-02-01
```

Files fetched before 00:00 UTC on the `--refresh-before` date are refetched.
Rerunning with the same date resumes, because refreshed files are skipped. A
failed refresh keeps the previous file, which still counts as `remaining`.
The build reports `fmp_fetched_at_span` and `sec_fetched_at_span` so mixed
snapshot dates are visible. A report whose SEC snapshot was taken no later
than the report day is dropped as `sec_snapshot_predates_report`, not as a
missing 8-K.

## SEC acceptance time

`acceptanceDateTime` (for example `2026-08-03T20:10:00.000Z`) is read the
same way the repository's SEC provider reads it, as UTC, and then converted
to New York time. There is no operator option. Every Item 2.02 acceptance
must fall within EDGAR's filing hours, 06:00–22:00 ET. If one does not, the
build fails closed with `EARNINGS_EXPORT_ERROR`, because the data contradicts
the UTC contract. Treat that as a code-level question for the shared SEC
parser (`sec_provider._parse_accepted_at`), not something to work around in
this command. `item_2_02_acceptance_hour_histogram_et` is still reported as
a sanity check.

The UTC reading was checked against EDGAR on 2026-11-24. Each submissions-feed
value was compared with the "Accepted" time on the filing's EDGAR index page,
which is Eastern:

| Filing | `acceptanceDateTime` | EDGAR "Accepted" (ET) |
|---|---|---|
| AAPL 0000320193-26-000005 (EST) | `2026-01-29T21:30:33.000Z` | 2026-01-29 16:30:33 |
| AAPL 0000320193-26-000018 (EDT) | `2026-07-30T20:30:28.000Z` | 2026-07-30 16:30:28 |
| JPM 0001628280-26-001902 (EST) | `2026-01-13T11:41:09.000Z` | 2026-01-13 06:41:09 |
| JPM 0001628280-26-048078 (EDT) | `2026-07-14T10:30:38.000Z` | 2026-07-14 06:30:38 |

The `Z` hour shifts by one across daylight saving while the Eastern time
stays fixed, so the field is genuine UTC. Apple's Item 2.02 history back to
2015 shows the same pattern: `20:30Z` in summer and `21:30Z` in winter for its
16:30 ET release. `tests/unit/test_earnings_export.py` pins these examples.

## Steps

1. **Write the universe**, one symbol per line (`#` comments allowed), for
   example `data/earnings-symbols.txt`. Choose names that gap on earnings and
   trade enough to pass the candidate filter.
2. **Fetch FMP** (operator, credentialed; reads `FMP_KEY` from `.env`):

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv fetch-fmp \
     --symbols-file data/earnings-symbols.txt
   ```

   See [fetch results and exit codes](#fetch-results-and-exit-codes). Raw
   files go to `data/raw/fmp/` (gitignored).
3. **Fetch SEC filings** (operator, network; no key, but SEC requires a
   contact User-Agent, read from `sec.user_agent_contact` in
   `config/evidence_providers.yaml`):

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv fetch-sec \
     --symbols-file data/earnings-symbols.txt
   ```

4. **Build** (offline; see [SEC acceptance time](#sec-acceptance-time)):

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv build \
     --symbols-file data/earnings-symbols.txt --start 2025-01-01 --end 2026-11-20
   ```

   The command writes `data/earnings.csv`, checks it with the same strict
   loader the backtest uses, and prints its SHA-256 and the drop counts.
   Record the SHA-256 and treat the file as a frozen snapshot (see step 1 of
   the [backtest runbook](intraday-gap-backtest.md)).
