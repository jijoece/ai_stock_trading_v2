# Pre-step before PR 6 — LumiBot backtest-mode boundary: Opus architecture review and feasibility spike

**Date:** 2026-07-26; extended 2026-08-01 with the sentinel credential-safety
proof (§2.3, covering both the `.env` and process-environment leak paths) and
owner acceptance of ADR 0009.
**Model:** Opus (the review `MASTER_PLAN.md`'s "Pre-step before PR 6" row requires)
**Scope:** architecture and feasibility only. No PR 6 implementation, no code
under `src/`, `scripts/`, `paper_runtime/src/`, `tests/`, or `config/`.
**Outcome:** Option B selected. New ADR
[`0009-lumibot-backtest-distribution-boundary.md`](../../adr/0009-lumibot-backtest-distribution-boundary.md),
**Accepted** by the repository owner on 2026-08-01. The pre-step is complete
and PR 6 is unblocked (not started).

This document supersedes the reasoning recorded in the first (Sonnet) pass of
this pre-step. That pass reached a different conclusion — a second in-process
import boundary at `src/trading_research/backtesting/lumibot_backtest/` — on
premises this review measured and found false. Section 5 lists each corrected
premise.

---

## 1. Why the earlier decision was reopened

`MASTER_PLAN.md` row "Pre-step before PR 6" specifies **Opus review**. The
first pass ran under Sonnet and said so in its own status entry. Its
conclusion was therefore recorded but not ratified, and PR 6 was not actually
unblocked. This review re-ran the decision under the required model and, in
the process, ran the feasibility spike the first pass did not.

The first pass also reasoned from `lumibot==4.5.74` — a stale local
installation. The repository's pin has been `lumibot==4.5.78` since PR 1
(`paper_runtime/pyproject.toml`). Every measurement below uses exactly
`4.5.78`.

---

## 2. Feasibility spike (pinned `lumibot==4.5.78`)

Full raw output: [`spike_output.txt`](spike_output.txt). Scripts:
[`guards.py`](guards.py), [`spike_backtest.py`](spike_backtest.py). Clean
disposable venv, Python 3.11.15 (the interpreter line CI already uses), macOS
arm64.

Network was made to **fail closed** before `lumibot` was imported:
`socket.getaddrinfo`, `socket.create_connection`, `socket.socket.connect`, and
`socket.socket.connect_ex` were patched to record the target and raise
`NetworkBlocked`, with `localhost`/loopback exempted. Every read of
`os.environ` was recorded by an `os._Environ` subclass installed before the
import, so credential access is measured rather than assumed.

### 2.1 What passed

| Claim to prove | Result | Evidence |
|---|---|---|
| Installs cleanly in a dedicated distribution | **PASS** | 309 packages, 40.7s, `pip check` → "No broken requirements found" |
| Installed version is exactly `4.5.78` | **PASS** | asserted in-process: `lumibot.__version__ == "4.5.78"` |
| The exact Pandas backtesting class imports | **PASS** | `lumibot.backtesting.pandas_backtesting.PandasDataBacktesting` |
| One minimal caller-supplied DataFrame backtest runs | **PASS** | 10 hand-written daily bars, 1 symbol, `total_return = 0.00065` |
| All input bars are caller supplied | **PASS** | perturbing the final bar's close by +5.00 moved `total_return` to `0.00115`; no other input changed |
| Results are deterministic across repeated runs | **PASS** | runs B1 and B2 produced bit-identical result dicts |
| No network attempted **when the environment holds no broker credentials** | **PASS** | runs B1/B2/P1: **0** blocked attempts |

### 2.2 What failed

Three of the required proofs did **not** pass. These are the findings that
change the architecture decision.

**Finding 1 — LumiBot looks for broker credentials at import,
unconditionally.** Importing `lumibot` read **277 distinct environment
variables**, of which **64 are credential-named**, including `ALPACA_API_KEY`,
`ALPACA_API_SECRET`, `ALPACA_IS_PAPER`, `TRADIER_ACCESS_TOKEN`, `IB_PASSWORD`,
`COINBASE_PRIVATE_KEY`, `SCHWAB_APP_SECRET` — and `ANTHROPIC_API_KEY`, which
this repository genuinely uses. This happens on plain `import lumibot`, before
any backtest object exists. There is no backtest-only import path that avoids
it.

This is why the requirement this review places on `backtest_runtime/` is **not
"zero credential reads."** That is unachievable: LumiBot reads the *names*
whatever the environment holds, and no flag changes it. The achievable — and
sufficient — requirement is that none of those reads finds a credential
*value*, which §2.3 measures directly.

**Finding 2 — with credentials present, a backtest opens a live broker
connection.** With sentinel Alpaca credentials in `os.environ`, the *offline
backtest run* produced **177 blocked outbound connection attempts to
`paper-api.alpaca.markets:443`** (696 in an earlier identical run — the count
is driven by a background retry thread and varies; the fact of attempting does
not). LumiBot's own log line during the run is explicit: `Waiting for the
socket stream connection to be established`. Had the guard not been installed,
those connections would have succeeded against the operator's real paper
broker account.

The trigger is credentials being *visible*, nothing else. Same code, same
fixture, credentials scrubbed → zero attempts. And the results were identical
either way (`total_return` matched to the last digit in both modes), so the
live connection contributes nothing to the backtest — it is pure side effect.

**Finding 3 — `lumibot`'s credential loader walks the filesystem for `.env`.**
`lumibot/credentials.py` calls `find_and_load_dotenv()` against both the
script directory and the **current working directory**, loading whatever it
finds into `os.environ` at import time.

This repository's `.env.example` documents exactly `ALPACA_API_KEY` and
`ALPACA_API_SECRET`, and `.env` is gitignored — i.e. a real operator machine
is expected to have one, holding exactly the credentials that trigger Finding
2. Combined:

```text
developer runs `pytest tests/` from the repository root
  -> an in-process lumibot_backtest package imports lumibot
  -> lumibot loads ./.env from the CWD
  -> the operator's real Alpaca paper credentials enter os.environ
  -> a background thread connects to paper-api.alpaca.markets
```

That is a live credentialed broker connection opened by a test run of a
"deterministic offline backtest." It is precisely the outcome ADR 0002 exists
to prevent. §2.3 establishes the exact mechanism that prevents it.

**Finding 4 (protocol hazard, informational) — stdout is contaminated.**
A backtest run wrote **382–543 bytes to fd 1**: the `LumiBot v4.5.78 starting`
banner (58 bytes, at import), plus progress bars carrying `\r` and `ESC[K`
escape sequences. ADR 0002 recorded this for 4.5.74 and fixed it in
`paper_runtime/.../__main__.py`; it is **unchanged in 4.5.78**. Any design
that speaks a protocol over stdout must redirect fd 1 before importing
`lumibot`, not merely before running a backtest.

### 2.3 Credential safety, proved with sentinels (2026-08-01)

Findings 1–3 establish the hazards. This section establishes the fixes and
measures them: the exact import-time mechanism that prevents the pinned
versions (`lumibot==4.5.78`, `python-dotenv==1.2.2`) from loading an
operator's `.env`, and the credential scrub that removes credentials inherited
from the process environment. Both are proved against fake sentinel
credentials planted on each path.

Raw output: [`dotenv_sentinel_output.txt`](dotenv_sentinel_output.txt).
Harness: [`run_dotenv_sentinel.sh`](run_dotenv_sentinel.sh),
[`summarize_dotenv_sentinel.py`](summarize_dotenv_sentinel.py), fixtures in
[`sentinel_dotenv/`](sentinel_dotenv). Reproduce with
`./run_dotenv_sentinel.sh <disposable-workdir>`.

**The mechanism: `LUMIBOT_DISABLE_DOTENV=1`, set before `import lumibot`.**
`lumibot/credentials.py` evaluates it at module scope, before any discovery
runs:

```python
_disable_dotenv = _env_flag_enabled("LUMIBOT_DISABLE_DOTENV")   # 1/true/yes/on
if _disable_dotenv:
    found_dotenv = False                       # script-dir walk skipped
else:
    found_dotenv = find_and_load_dotenv(script_dir)
if not found_dotenv and not _disable_dotenv:
    found_dotenv = find_and_load_dotenv(os.getcwd())   # cwd walk skipped too
```

When the flag is set, both walks are skipped and `dotenv.load_dotenv` is never
called at all.

**Why nothing else works.** ADR 0009 originally specified "point LumiBot's
`.env` discovery at a path guaranteed not to exist." That is not
implementable. The two base directories are
`os.path.dirname(os.path.abspath(sys.argv[0]))` and `os.getcwd()`; neither is
configurable, and `find_and_load_dotenv()` walks **upward** from each to the
filesystem root, loading the first `.env` it meets (then `.env.local`, with
`override=True`). So there is no path to point it at, and `chdir` to an empty
directory does not help — an ancestor's `.env` is still found. The ADR has
been corrected.

**Setup.** A sentinel `.env` and `.env.local` carrying unique, obviously-fake
Alpaca values (token `SENTINEL-DOTENV-7f3a9c21e4b8`) are placed in the working
directory the spike is launched from. No real credential was read, used, or
exposed: the values are fabricated, the run happens in a disposable scratch
directory, and the harness refuses to start if a pre-existing `.env` sits
anywhere in its ancestor chain. The spike detects leakage by scanning
`os.environ` and LumiBot's `*_CONFIG` objects for the token, and reports it by
key name — never by value. Where a control run inherits the operator's real
ambient environment, the record reports a count rather than enumerating which
credential variables that machine defines.

A second leak path is measured alongside it: credentials **inherited from the
process environment**, which the entry point's credential scrub must remove
before the import. Two sentinel tokens keep the paths distinguishable —
`SENTINEL-DOTENV-…` in the dotenv fixtures, `SENTINEL-PROCENV-…` exported into
the child process by the harness.

| Run | Suppression | Scrub | `.env` in CWD | Inherited creds | Sentinel | Env-sourced values | Net | Broker | `total_return` |
|---|---|---|---|---|---|:---:|:---:|---|---|
| S0 | on | on | no | no | not loaded | 0 | 0 | `None` | 0.00065 |
| S1 | **off** | on | yes | no | **LEAKED** | 3 | **62** | **built** | 0.00065 |
| S2 | on | on | yes | no | not loaded | 0 | 0 | `None` | 0.00065 |
| S3 | on | on | yes | no | not loaded | 0 | 0 | `None` | 0.00065 |
| S4 | on | on | yes | no | not loaded | 0 | 0 | `None` | 0.00115 |
| S5 | **off** | on | no (empty CWD) | no | **LEAKED** | 3 | **56** | **built** | 0.00065 |
| P1 | on | **off** | no | yes | **LEAKED** | 6 | **56** | **built** | 0.00065 |
| P2 | on | on | no | yes | not loaded | 0 | 0 | `None` | 0.00065 |

S0 is the no-`.env` baseline; S3 repeats S2 exactly; S4 perturbs one input
bar; S5 runs from an *empty subdirectory* of the sentinel directory. P1 and P2
inherit identical fake Alpaca credentials from the parent process in a CWD
with **no** `.env`, so the environment is the only possible source and the
scrub is the only difference between them.

Outbound-attempt counts in the controls vary between runs — a background
retry thread drives them — so only the *fact* of attempting is asserted, never
a count. "Env-sourced values" counts credential-named variables whose value
came from the process environment — the strict metric, distinguished from LumiBot's own
`.get()` defaults by measurement rather than by interpretation (below).

**Positive controls (S1, S5, P1) — the sentinels are real.** Each control
deliberately omits one protection and demonstrates the hazard it prevents, so
a protected run's clean result cannot be an artefact of an undetectable
sentinel. In every control the sentinel values reach `os.environ`, propagate
into LumiBot's `ALPACA_CONFIG`, construct a live Alpaca broker object, and
drive blocked outbound attempts to `paper-api.alpaca.markets:443`. The
summariser asserts all four of those per control, and fails if any control
stops leaking.

* **S5 is decisive for the `.env` path:** its CWD contains no `.env` at all,
  and the parent's still loaded — which rules out `chdir` as a mechanism, and
  would equally doom a backtest run from any subdirectory of a repository
  whose root holds a `.env`.
* **P1 is the process-environment path:** credentials inherited from the
  parent reach LumiBot untouched when the scrub does not run, even with
  `LUMIBOT_DISABLE_DOTENV=1` set. The dotenv flag protects one path only; the
  scrub is what protects the other.

**In the protected runs (S0/S2/S3/S4 and P2)** — sentinel `.env` and
`.env.local` still sitting in the CWD for S2/S3/S4, fake Alpaca credentials
still inherited from the parent for P2 — all five required properties hold:

1. *No credential value available or loaded.* Credential-named variables whose
   value came **from the process environment**: **0** in every protected run.
   Credential-named variables resolving to any value at all: exactly **3**, all
   of them LumiBot's own hardcoded `.get()` defaults, matched only because the
   tracer keys on names — `COINBASE_SANDBOX` → `"false"`,
   `IB_USE_PAPER_ACCOUNT` → `"true"`, `DATADOWNLOADER_API_KEY_HEADER` →
   `"X-Downloader-Key"` (a header name).

   That attribution is **measured, not interpreted**: the tracer resolves key
   presence separately from the value, so a default returned for an *absent*
   key is recorded as not-from-environment. The summariser asserts the set of
   three **exactly** — a fourth credential-named value, or any of these three
   arriving from the environment instead of a default, fails the check rather
   than being explained in prose. Note the contrast with the **61**
   credential-named variables LumiBot *looked for* in the same run: reads are
   unavoidable, values are what matter.
2. *Nothing loaded from the environment or from `.env`/`.env.local`.* Neither
   sentinel token appears in `os.environ` after the scrub, after import, or
   after the run, nor in any LumiBot `*_CONFIG`. For P2 the scrub is shown
   working at the exact point it must: the inherited keys are present before
   it and gone immediately after, still before `import lumibot`.
3. *No broker and no live data provider initialized.*
   `lumibot.credentials.broker` and `.data_source` are both `None` after
   import — against the controls, where `broker` is a constructed Alpaca
   object.
4. *Zero outbound network attempts*, under the fail-closed guard, across a
   full backtest.
5. *Determinism.* S2 and S3 produced bit-identical result dicts; both equal
   the no-`.env` baseline S0 exactly, and so does P2 — neither a `.env` nor an
   inherited credential changed anything. S4 shows the run is still reading
   its inputs: perturbing one bar moved `total_return` from `0.00065` to
   `0.00115`, so the backtest consumed only the caller-supplied 10-bar
   fixture.

The summariser exits non-zero on any failed assertion, so this evidence is
regenerated by a check that fails closed rather than by a narrative pass over
the numbers.

**Consequence for PR 6.** The entry point must set
`LUMIBOT_DISABLE_DOTENV=1` **and** run the credential scrub (alongside the
stdout redirect) *before* importing `lumibot` — P1 shows the flag alone is not
sufficient, and S1/S5 show the scrub alone would not be either. The blocking
CI job must assert the five properties above rather than a credential-read
count, and must pin the benign-default set exactly so a LumiBot upgrade that
resolves one more credential-named variable fails rather than passing quietly.
Because the `.env` half of the guarantee rests on one upstream flag, a LumiBot
release that renames or drops it must fail that job loudly — recorded in ADR
0009's reconsideration conditions.

### 2.4 Option C's dependency conflict, re-verified at 4.5.78

`pip install -e <root> lumibot==4.5.78` (dry run) → **`ResolutionImpossible`**.
Two independent walls, both reproduced:

1. `lumibot 4.5.78` requires `google-genai<2.0.0,>=1.72.0`; every
   `google-adk >= 2.2.0` requires `google-genai>=2.4`.
2. Isolated separately (`pip install lumibot==4.5.78 "jsonschema>=4.26.0"`):
   `litellm` pins `jsonschema==4.23.0` exactly across every compatible
   release, against this repository's `jsonschema>=4.26.0` base floor.

Wall 2 confirms `DECISIONS.md` D5's diagnosis still holds at the current pin.
The isolated Option B environment resolves `jsonschema==4.23.0` — fine there,
because nothing in that environment requires the newer floor.

---

## 3. The three designs

### Option A — extend the existing `paper_runtime`

Add bounded offline-backtest operations to the credentialed runtime.

The first pass rejected this abstractly ("more boundary surface"). Measured
concretely, the protocol cost is real but is not the disqualifier:

`paper-runtime.v2` caps a single envelope at **65,536 bytes**
(`MAX_REQUEST_BYTES`, and `MAX_RESPONSE_BYTES` on the client side). Measured
JSON DTO sizes against the repository's existing `backtesting/models.py`
shapes:

| DTO | Bytes (JSON, compact) |
|---|---|
| One `HistoricalBar` | 218 |
| One `BacktestDailyState` | 143 |
| One `BacktestFill` | 226 |

| Parity run | Request payload | vs. 64 KB cap |
|---|---|---|
| 1 symbol × 252 sessions | 54,936 B | 0.8× (fits) |
| 3 symbols × 504 sessions | 329,616 B | **5.0×** |
| 5 symbols × 504 sessions | 549,360 B | **8.4×** |
| 10 symbols × 756 sessions | 1,648,080 B | **25.1×** |

| Result payload | Bytes | vs. cap |
|---|---|---|
| 252 states + 20 fills | 40,556 B | 0.6× (fits) |
| 504 states + 60 fills | 85,632 B | **1.3×** |
| 756 states + 120 fills | 135,228 B | **2.1×** |

So a single-symbol one-year parity run fits in one round trip; anything
larger requires either raising the envelope cap or adding chunking/pagination
operations. PR 7 compares "orders, fills, timestamps, prices, quantities,
cash, positions, fees, P&L, equity, drawdown" over a fixture set — multi-symbol
and multi-year is the realistic case, so chunking would be needed.

The actual disqualifier is different and more serious: `paper_runtime` is the
**credentialed** process. It holds `PAPER_RUNTIME_ENV_FILE`, real Alpaca keys,
and a live broker gateway. Adding backtest operations there puts deterministic
research work inside the one process authorized to reach a real broker, and
makes every backtest run a reason to start that process. Given Finding 2 —
LumiBot autoconnects whenever credentials are visible — Option A maximizes
exactly the exposure that should be minimized. It also enlarges a protocol
whose smallness ADR 0002 Decision 2 treats as a safety property.

Additionally, ADR 0002 Decision 1 forbids installing the main project
alongside `paper_runtime`. PR 7 must run `backtesting/engine.py` (which imports
`analysis.indicators`, `paper_books.lifecycle_state`, and
`evidence_providers.economic_calendar`) and the LumiBot side over identical
fixtures. Under Option A those two sides live in environments that may never
be installed together, so the comparison must cross the protocol anyway.

**Verdict: rejected.** Not primarily on protocol cost — on credential
proximity.

### Option B — dedicated isolated backtest distribution (`backtest_runtime/`)

A second top-level distribution, sibling to `paper_runtime/`, with its own
`pyproject.toml`, its own `requires-python`, `lumibot==4.5.78` as a base
dependency, **no broker credentials**, no live-submission operations, a
deterministic fixture-in/result-out contract, its own tests, and its own
blocking CI job.

Every spike finding is contained by this shape:

- Finding 1 (credential reads): the distribution's entry point scrubs
  credential-named variables from `os.environ` and sets an explicit empty
  `.env` path before importing `lumibot`. Nothing else in that process needs
  credentials, so scrubbing costs nothing.
- Finding 2 (autoconnect): with nothing to find, the spike measured **zero**
  connection attempts — and its own test suite can assert that with the same
  fail-closed guard used here, as a blocking CI check.
- Finding 3 (`.env` discovery): the process's CWD is controlled by its own
  entry point rather than being "wherever the developer ran pytest."
- Finding 4 (stdout): identical to the fix `paper_runtime/.../__main__.py`
  already carries, and reusable from it.
- Option C's conflict: never arises. The root `pyproject.toml` is untouched,
  `jsonschema>=4.26.0` stays, and this distribution resolves
  `jsonschema==4.23.0` in its own environment, as `paper_runtime` already
  does.
- Reproducibility: `pip install -e backtest_runtime/` is a real, declared,
  CI-verifiable install target. No hand-built scratch virtualenv, no
  `importorskip`-and-hope.

Cost: a third distribution to maintain, a fixture/result serialization
contract to define, and ~1.9 GB of installed environment in one more CI job.

**Verdict: selected.**

### Option C — install LumiBot into the main environment

Ruled out by construction: the pre-step's own instruction forbids selecting it
while the `jsonschema` conflict remains, and §2.4 re-confirmed that conflict at
`4.5.78` alongside a second, independent `google-genai` wall. Even if both were
resolved tomorrow, Findings 1–3 would still make an in-process import a
credential-exposure change requiring its own decision.

**Verdict: rejected, twice over.**

### Option D (implicit) — the first pass's proposal, evaluated on its merits

`src/trading_research/backtesting/lumibot_backtest/` inside the main source
tree, with no declared extra, `pytest.importorskip("lumibot")` on every test,
and hand-installed scratch virtualenvs.

This is not Option C — it does not install LumiBot into the root environment —
but it inherits Option C's exposure without Option C's honesty about it:

- It is an **undeclared** dependency. No extra declares it, ordinary CI never
  installs it, and every test skips. The AST boundary would permit an import
  that nothing reproducible ever exercises.
- Whenever a developer *does* hand-install it, the import happens in a process
  whose CWD is the repository root — triggering Finding 3, then Finding 2,
  against real credentials.
- It cites the existing skipped `runtime/lumibot` tests as precedent. That
  precedent is itself weaker than documented (see §4), and expanding an
  unresolved condition is not the same as having resolved it.

**Verdict: rejected.**

---

## 4. Collateral finding: the AST import boundary does not run in ordinary CI

`test_no_lumibot_import_outside_runtime_package` lives in
`tests/unit/test_lumibot_adapter.py`, whose **module-level line 27** is
`pytest.importorskip("lumibot")`. LumiBot is not installed by `main-tests`
(`pip install -e ".[dev]"`), so the whole file — including the AST walk —
**skips**. The only import-boundary check that actually runs in CI is
`tests/unit/test_runtime_client_no_lumibot_import.py`, which walks an explicit
list of 17 named files, not the tree.

Confirmed by execution, not by reading: in a fresh `pip install -e ".[dev]"`
environment (exactly what `main-tests` builds), running both boundary test
files gives **2 passed, 1 skipped** — the skip is
`tests/unit/test_lumibot_adapter.py:27`, which takes the entire module with
it, and the 2 passes are both from `test_runtime_client_no_lumibot_import.py`.

`DECISIONS.md` D4's claim that the constraint "remains AST-enforced, not
documentation-only" is therefore not true as things stand. This does not
change the option selection, but it means PR 6 must move the tree-walking AST
test into a file that runs without LumiBot installed — otherwise the new
boundary is documentation-only too. Recorded as a PR 6 requirement in the ADR.

---

## 5. Premises from the first pass that this review corrected

| First-pass premise | Measured result |
|---|---|
| "the installed `lumibot==4.5.74` … ships `PandasDataBacktesting` … with no network call and no credentials" | Version stale; the class exists, but the **package** reads 64 credential variables at import and connects to `paper-api.alpaca.markets` whenever credentials are visible |
| Routing through `paper_runtime` "would pay for isolation against a live-credential risk that does not exist here" | The risk exists and was measured. It is a property of importing `lumibot`, not of the data source |
| A new in-process package "follows the precedent `runtime/lumibot/adapter.py` already established" | That precedent's enforcement test does not run in CI (§4); the precedent is weaker than recorded |
| "requires no new ADR" | Extending LumiBot to a second boundary is a material change to ADRs 0001/0002; ADR 0009 drafted |
| `paper-runtime.v1`, "9 operations" | Current protocol is **`paper-runtime.v2`, 19 operations** (`paper_runtime/.../protocol.py`) |
| "~140 transitive packages" (ADR 0001/0002, repeated since) | **309 packages, 1.9 GB** installed at 4.5.78 |

---

## 6. Decision matrix

Weighted on the criteria `MASTER_PLAN.md` set for this review. Scores: 2 =
satisfies, 1 = satisfies with work, 0 = fails.

| Criterion | A: `paper_runtime` | B: `backtest_runtime/` | C: main process | D: in-process pkg |
|---|:---:|:---:|:---:|:---:|
| Dependency isolation | 2 | 2 | 0 | 0 |
| Respects accepted ADR 0001/0002 constraints | 1 | 2 | 0 | 1 |
| Reproducible installation | 2 | 2 | 0 | 0 |
| CI testability (blocking, non-skipping) | 2 | 2 | 0 | 0 |
| Offline / no-credential guarantee | 0 | 2 | 0 | 0 |
| PR 7 parity-data transfer | 1 | 2 | 2 | 2 |
| Rollback and failure isolation | 1 | 2 | 0 | 1 |
| **Total (max 14)** | **9** | **14** | **2** | **4** |

Per-criterion notes:

- *Offline / no-credential*: A scores 0 because `paper_runtime` is
  credentialed by definition; C and D score 0 because of Findings 1–3. Only B
  can scrub the environment it controls and assert the result in CI.
- *PR 7 parity-data transfer*: A scores 1 — the 64 KB envelope forces chunking
  above ~1 symbol-year (§3). B scores 2 via a file-based fixture/result
  contract with no envelope limit.
- *Rollback*: B is deletable as a directory with no root `pyproject.toml`
  change; A requires unwinding protocol operations from a live-broker process.
- *ADR constraints*: A scores 1 because it enlarges the protocol ADR 0002
  Decision 2 deliberately kept small.

**Selected: Option B.**

---

## 7. Pre-step status: complete

All gates are met, and PR 6 is unblocked. Tracked in `STATUS.md`:

1. **Opus architecture review** — this document.
2. **Pinned-version feasibility spike** — §2, raw output in
   [`spike_output.txt`](spike_output.txt).
3. **Sentinel-`.env` suppression proof** — §2.3, raw output in
   [`dotenv_sentinel_output.txt`](dotenv_sentinel_output.txt).
4. **Owner acceptance of ADR 0009** — granted 2026-08-01; the owner selected
   Option B, an isolated, credential-free `backtest_runtime/` distribution.

`backtest_runtime/` does **not** exist, and its absence is not a gate.
Creating it — the directory, its installable `pyproject.toml`, its tests, and
its blocking `backtest-runtime-tests` CI job — is PR 6 implementation work and
a condition for *merging* PR 6, recorded as PR 6 acceptance criteria in ADR
0009 Decision 4. An earlier version of this section listed that PR 6 work as a
prerequisite for *starting* PR 6, which made the pre-step unsatisfiable by
construction; it is corrected here.

## 8. Deliberately out of scope

- No file under `src/`, `scripts/`, `paper_runtime/src/`, `tests/`, or
  `config/` was modified. In particular the AST test fix identified in §4 is
  a PR 6 change, not a pre-step change.
- Stale `4.5.74` version labels remain in
  `src/trading_research/runtime/lumibot/event_mapper.py` (docstring),
  `tests/support/runtime_client_fixtures.py` (a fake fixture value), and the
  dated milestone/ADR narratives that correctly describe what was true when
  written. The `event_mapper.py` docstring's *content* was re-verified against
  4.5.78 and is still accurate — `[s.value for s in Order.OrderStatus]` is
  byte-identical to what it records, and the four deliberately-unmapped
  statuses (`cash_settled`, `assigned`, `exercised`, `unknown`) are still
  exactly the four it fails closed on. Only the version label is stale, so
  correcting it is cosmetic and is left to PR 6/PR 9.
- No broker, provider, model, or market-data service was called. Every network
  attempt made during the spike was blocked by the fail-closed guard and is
  itemized in `spike_output.txt`. No live data was fetched, no scheduler
  enabled, no trading limit or authorization rule touched.
