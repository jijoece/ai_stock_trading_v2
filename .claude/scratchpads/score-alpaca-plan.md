# Plan: let `score.py` fetch Alpaca prices

Settled decisions: (a) the fetching goes into `scripts/score.py`, with
split-adjusted prices, the IEX feed, at least 220 sessions of history,
completed sessions only, `--macro-score` and `--holding` flags, several symbols
per run, and tests with no network calls.

One part of (a) was decided without asking. `score.py` reuses the repo's
existing Alpaca client rather than making its own web requests. It loads that
code only when asked to fetch, so the file-input and self-test modes keep using
only the Python standard library. Writing new request code inside `score.py`
would skip the repo's existing rate limiting, credential checks, adjustment
checks and bad-response handling.

## 1. Command-line interface (`scripts/score.py`)

```
score.py [input.json]                          # unchanged: file input or self-test
score.py --alpaca AAPL MSFT NVDA [options]     # new: fetch, then score
  --adjustment {split,all}     default split; raw is not an accepted value
  --feed {iex,sip}             default iex
  --macro-score N              integer -2..+2; empty if not given
  --holding | --not-holding    default unknown (empty)
  --as-of YYYY-MM-DD           default: last completed session
  --min-sessions N             default 220
  --save-input DIR             optional: write each symbol's fetched JSON for audit and replay
  --json
```

- `--alpaca` cannot be combined with a positional input file. The two modes are
  separate.
- `main()` turns the arguments into one list of `(symbol, close[])` jobs, from
  the file or from Alpaca. Each job goes through the existing `score_symbol()`
  and `render()`. The scoring logic does not change.

## 2. Fetch path (new functions in `score.py`, loading repo code only in `--alpaca` mode)

- **`_alpaca_client(feed)`**: imports
  `trading_research.backtesting.alpaca_history.alpaca_client_from_environment`
  inside the function. Credentials come from `ALPACA_MARKET_DATA_*` and are read
  only there. If the import fails, the error says to run with the project
  `.venv`. Missing keys give a clear error.
- **`_last_completed_session(now)`**: uses
  `trading_research.evaluation.market_calendar`. If today is a trading day and
  `now` is before `regular_session_close(today)`, it returns the previous
  trading day. A calendar error stops the run rather than guessing.
- **`fetch_closes(client, symbol, *, as_of_session, adjustment, min_sessions)`**:
  - Start date is `warmup_start(as_of_session, sessions=min_sessions + 40)`,
    padded so the EMA200 slope has room.
  - `as_of` is the close of `as_of_session`. The client already drops bars
    after that date.
  - Bars dated after `as_of_session` are dropped as a safeguard.
  - If any bar's `adjusted` flag doesn't match the requested adjustment, or
    fewer than `min_sessions` bars come back, it raises an insufficient-history
    error.
  - Returns `closes: list[float]` (from `Decimal`), the first and last session
    dates, and the bar count.
- **Results for each symbol**: each card gets a `data` block with `provider`,
  `feed`, `adjustment`, `first_session`, `last_session`, `bars` and
  `fetched_at`. With `--save-input`, it writes
  `{symbol, close, macro_score, holding, data}`, which `score.py` can read back
  in file mode.

## 3. Running several symbols

- Symbols are fetched and scored one after another through one client, so the
  existing rate limiter (0.35 s) applies.
- If one symbol fails, its error is recorded and the run continues. The run
  exits `0` if all succeed, `2` if some failed, and `1` if all failed or there
  was a configuration error.
- Text output shows one card per symbol plus a list of failures. `--json`
  output is `{"as_of": ..., "results": [...], "errors": [{symbol, error}]}`.
- Single-file mode keeps its current output shape exactly.

## 4. Tests (`tests/unit/test_score_alpaca.py`, no network)

These load `scripts/score.py` through `importlib` and replace `_alpaca_client`
with a fake that returns made-up `PriceBar` tuples. They cover:

- the happy path, where the card matches `score_symbol` run on the same closes;
- refusal below `min_sessions`;
- `raw` rejected by argparse;
- a mismatched `adjusted` flag rejected;
- bars after the as-of date dropped;
- `_last_completed_session` before and after the close, on weekends, holidays
  and early-close days (fixed `now`);
- one bad symbol among several (exit `2`, errors list filled in);
- `--save-input` output that scores the same as the live run;
- missing credentials giving a clear error;
- `--alpaca` together with an input file being rejected;
- a check that the offline path still loads only the standard library: running
  the self-test in a subprocess doesn't import `trading_research`.

The existing checks in `.claude/skills/run-agentic-trading-desk/driver.py`
still pass unchanged. A smoke test against the real Alpaca API is opt-in only,
marked and skipped by default, and is never run by CI or by an agent unless the
operator asks.

## 5. Docs

- Update `score.py`'s docstring. It currently says "stdlib only"; it will say
  the offline modes use only the standard library and `--alpaca` needs the
  project venv.
- Add a usage section to the README and a short section to the
  `run-agentic-trading-desk` skill doc covering credentials and flags.
- Run `scripts/check_links.sh` if any Markdown links change.

## 6. Validation

- Work in steps with `nox -s tests -- tests/unit/test_score_alpaca.py`, then run
  `nox -s ci` before a PR.
- `nox -s safety_typecheck` probably isn't needed because no safety-critical
  module changes. Confirm against its file list.
- Branch: this is separate from the current live-trading spike. Create
  `feat/score-alpaca-data` from `main`.

## Out of scope for v1

- Automatic `macro_pillar` scoring.
- Reading positions from the broker.
- Intraday bars.
- A cache that persists between runs.
