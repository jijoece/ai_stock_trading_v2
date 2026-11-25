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

## Steps

1. **Write the universe**, one symbol per line (`#` comments allowed), for
   example `data/earnings-symbols.txt`. Choose names that gap on earnings and
   trade enough to pass the candidate filter.
2. **Fetch FMP** (operator, credentialed; reads `FMP_KEY` from `.env`):

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv fetch-fmp \
     --symbols-file data/earnings-symbols.txt
   ```

   The output lists `saved`, `already_saved`, `failed` (by symbol, error
   class only) and `remaining`. Raw files go to `data/raw/fmp/` (gitignored).
3. **Fetch SEC filings** (operator, network; no key, but SEC requires a
   contact User-Agent, read from `sec.user_agent_contact` in
   `config/evidence_providers.yaml`):

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv fetch-sec \
     --symbols-file data/earnings-symbols.txt
   ```

4. **Calibrate the acceptance clock once.** SEC's submissions API writes
   `acceptanceDateTime` with a trailing `Z`; confirm which clock it really
   reports before trusting the timing. Run the build both ways for a few
   symbols you know report after the close:

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv build \
     --symbols-file data/earnings-symbols.txt --start 2025-01-01 --end 2026-11-20 \
     --acceptance-clock eastern --out /tmp/earnings-eastern.csv
   ```

   The right clock puts most Item 2.02 acceptances at 06:00–09:00 or
   16:00–17:00 in `item_2_02_acceptance_hour_histogram_et`. The wrong clock
   shifts them by four or five hours, so many land mid-session and are
   dropped as `released_during_session`.
5. **Build** with the calibrated clock:

   ```bash
   .venv/bin/python -m trading_research.cli build-earnings-csv build \
     --symbols-file data/earnings-symbols.txt --start 2025-01-01 --end 2026-11-20 \
     --acceptance-clock <utc|eastern>
   ```

   The command writes `data/earnings.csv`, checks it with the same strict
   loader the backtest uses, and prints its SHA-256 and the drop counts.
   Record the SHA-256 and treat the file as a frozen snapshot (see step 1 of
   the [backtest runbook](intraday-gap-backtest.md)).
