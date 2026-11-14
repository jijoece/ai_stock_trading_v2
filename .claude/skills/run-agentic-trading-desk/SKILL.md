---
name: run-agentic-trading-desk
description: Build, run, and smoke-test agentic-trading-desk's deterministic scoring scripts (indicators.py, score.py, macro_pillar.py). Use when asked to run agentic-trading-desk, verify a change to the scoring/indicator logic, score a ticker, compute the macro pillar, or check the CLI/JSON output of these scripts.
---

The repository includes three stdlib-only deterministic scoring scripts:
`scripts/indicators.py`, `scripts/score.py`, and `scripts/macro_pillar.py`.
Use `.claude/skills/run-agentic-trading-desk/driver.py` to exercise their CLI
and direct-import surfaces. All paths below are relative to the repository
root.

## Prerequisites

Python 3.9+, stdlib only — no pip install, no venv required.

```bash
python3 --version   # 3.11.15 when this was verified; anything >=3.9 works
```

## Run (agent path — the driver)

```bash
python3 .claude/skills/run-agentic-trading-desk/driver.py
```

This generates synthetic ticker/macro data on the fly (no fixture files to
keep in sync), then:
- runs `scripts/indicators.py` with no args
  (self-test), with a 291-bar file, and with an 8-bar file (checks the
  short-series warning + null EMA200 path)
- runs `scripts/macro_pillar.py` with no args
  (self-test) and with a synthetic 8-ETF series file in both table and
  `--json` mode
- runs `scripts/score.py` with no args (self-test),
  with a synthetic ticker in `--json` mode, and with a missing file
  (checks nonzero exit)
- imports `score_symbol`, `compute`, and `score_macro` directly and calls
  them without going through subprocess/CLI at all

Prints `[OK]`/`[FAIL]` per check and exits nonzero (raises) on first
failure. All 21 checks currently pass.

## Direct invocation (what most PRs should use)

If a change only touches scoring/indicator logic, importing is faster than
shelling out and is the same path the driver uses:

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from score import score_symbol
card = score_symbol([100.0 + i*0.3 for i in range(300)], macro_score=1, holding=True)
print(card['decision']['action'], card['pillar_total'])
"
```

## Run (human path — individual scripts)

Each script also has a synthetic self-test with no args, useful for a quick
sanity check from the repository root:

```bash
python3 scripts/indicators.py      # synthetic 290-bar indicator dump (JSON)
python3 scripts/macro_pillar.py    # synthetic macro regime read
python3 scripts/score.py           # synthetic three-pillar scorecard + decision
```

Against real data, each takes a JSON file (see `README.md` for the exact
schemas) and an optional `--json` flag for machine-readable output:

```bash
python3 scripts/indicators.py ticker.json
python3 scripts/macro_pillar.py macro_input.json --json
python3 scripts/score.py ticker_input.json --json
```

## Fetching prices for `score.py` (`--alpaca`)

```bash
.venv/bin/python scripts/score.py --alpaca AAPL MSFT --macro-score 1 --not-holding --json
```

- Needs the project `.venv` and `ALPACA_MARKET_DATA_API_KEY` /
  `ALPACA_MARKET_DATA_API_SECRET`, read from the repo's `.env` (which wins) or
  else the environment; never read or print the file. This is a credentialed network call: do not
  run it unless the operator asks. The driver and the unit tests
  (`tests/unit/test_score_alpaca.py`) never touch the network.
- Flags: `--adjustment {split,all}` (default `split`), `--feed {iex,sip}`
  (default `iex`), `--macro-score N`, `--holding|--not-holding`,
  `--as-of YYYY-MM-DD`, `--min-sessions N` (default 220), `--save-input DIR`.
- Exit `0` all scored, `2` some symbols failed (see `errors`), `1` all failed
  or configuration error.
- Pass the real `macro_pillar.py` `pillar_score` as `--macro-score`; never
  estimate it. `--macro-score auto` computes it for you from the same data
  source (all eight cross-asset ETFs are fetched). Add `--yield-spread fred`
  for the 10Y-2Y spread (public FRED `T10Y2Y` CSV: level and slope; it is a
  network call, so only when the operator asks) or `--yield-spread 0.45` for a
  level-only number; with neither, the header says that component's weight was
  redistributed.
- `--fallback-previous-session` is opt-in: if the latest session's daily bar is
  not published yet (Robinhood publishes it late), it scores the previous
  session for everything at once and prints a `NOTE:` line. When you report
  such results, state the session actually used.
- `--check-holding` reads the operator's positions from every active Robinhood
  account (read-only) to set holding. It touches private account data, so
  only run it when the operator asks, and never print or store account
  numbers or share counts; the card shows only holding / not holding.
- `--robinhood SYMBOL ...` is the same fetch through the Robinhood MCP
  (read-only `get_equity_historicals` only). It needs a one-time interactive
  OAuth login by the operator (`python3 scripts/mcp_login.py login robinhood`); never run the login yourself
  and never run `--robinhood` unless the operator asks. Its cards carry
  `adjustment_verified: false`: say so when reporting results.

## Gotchas

- `indicators.py` needs ~220+ bars for EMA200/RSI/MACD/TRIX to be non-null;
  below that every derived indicator is `null` and a `warning` string is
  set instead of raising. The driver's 8-bar case checks this path — don't
  mistake it for a bug.
- `score.py`'s `macro_score` argument is *injected*, not computed — it's
  the `pillar_score` from a separate `macro_pillar.py` run. Feeding it a raw
  composite (`-1.0..+1.0` float) instead of the rounded `-2..+2` int pillar
  score will silently skew `pillar_total`.
- All three scripts raise/traceback (exit 1) on a missing or malformed
  input file rather than printing a friendly error — this is expected
  behavior, not something to "fix."

## Troubleshooting

No failures were hit while verifying this skill (stdlib-only, no
environment setup needed; the offline modes of `score.py` and the driver
never import the repo package). If `driver.py` fails, the `[FAIL]` line names
the exact check and the script's stderr — start there rather than
re-running individual scripts blind.
