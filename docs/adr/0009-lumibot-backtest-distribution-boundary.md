# ADR 0009: LumiBot backtest mode gets its own isolated, credential-free distribution

**Status:** **Accepted** (repository owner, 2026-08-01). The owner reviewed the
architecture review and feasibility spike and selected Option B — an isolated,
credential-free `backtest_runtime/` distribution.
**Date:** 2026-07-26 (library-migration, pre-step before PR 6); accepted
2026-08-01.

`backtest_runtime/` does **not** exist yet, and it is not a precondition for
starting PR 6. Creating it — the directory, its installable
`pyproject.toml`, its tests, and its blocking CI job — is PR 6's work, and is
recorded as PR 6 acceptance criteria in Decision 4. Acceptance of this ADR
completes the pre-step.
**Supplements:** ADR 0001 (LumiBot import boundary), ADR 0002 (credentialed
runtime process isolation). Supersedes neither; both remain in force exactly
as written.

## Context

`MASTER_PLAN.md` rows 6–8 require a LumiBot-based backtest adapter beside the
existing `backtesting/engine.py` (PR 6), a parity report between the two
(PR 7), and a removal decision gate (PR 8). `DECISIONS.md` D4 open item 1
required the import/dependency boundary for that work to be resolved first.

ADRs 0001 and 0002 together define the only LumiBot boundary this repository
has accepted so far:

* **ADR 0001 Decision 1** — LumiBot is imported in exactly one package,
  `src/trading_research/runtime/lumibot/`, enforced by an AST walk.
* **ADR 0002 Decision 1** — LumiBot's dependency tree belongs to a separate
  distribution, `paper_runtime/`, "never installed alongside the main
  project," reached only over the `paper-runtime` JSON Lines protocol.

Neither anticipated an *offline, deterministic, uncredentialed* LumiBot use.
Backtesting is that use, and it does not fit either boundary as written:
ADR 0001's in-process boundary assumes the import is safe in the main process,
and ADR 0002's boundary is built around a credentialed live-broker
connection.

A first pass at this decision (Sonnet, recorded and now superseded in
`DECISIONS.md` D4) proposed extending ADR 0001 — a second in-process import
directory, `src/trading_research/backtesting/lumibot_backtest/`, undeclared in
any extra and verified only in hand-built scratch virtualenvs. An Opus review
with a pinned-version feasibility spike
(`docs/library-migration/pre-step-06/EVALUATION.md`) found the premises of
that proposal false. Three measurements decided this ADR:

1. `import lumibot` (4.5.78) reads **64 credential-named environment
   variables** — `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `IB_PASSWORD`,
   `ANTHROPIC_API_KEY`, and others — before any backtest object exists.
2. With those credentials visible, an **offline backtest run opened repeated
   connections to `paper-api.alpaca.markets:443`** (177 and 696 attempts in
   two runs; a background retry thread drives the count). With credentials
   scrubbed: zero attempts, and byte-identical results — the connection
   contributes nothing.
3. `lumibot/credentials.py` **loads `.env` from the current working
   directory** at import. This repository's `.env.example` documents exactly
   `ALPACA_API_KEY` and `ALPACA_API_SECRET`, and `.env` is gitignored, so an
   operator machine is expected to have one. An in-process import from a
   repo-root `pytest` run would therefore load real paper-broker credentials
   and connect.

Separately re-verified at the current pin: `pip install -e <root>
lumibot==4.5.78` still fails with `ResolutionImpossible`
(`litellm`'s exact `jsonschema==4.23.0` against this repository's
`jsonschema>=4.26.0` floor; plus an independent `google-genai`/`google-adk`
wall), so no main-environment installation is available regardless.

## Decision 1: a third distribution, `backtest_runtime/`, owns LumiBot backtest mode

**Selected process/package boundary.** A new top-level installable
distribution, `backtest_runtime/`, sibling to `paper_runtime/` and never
installed into either the main project's environment or `paper_runtime`'s. It
runs as a separate process over a deterministic fixture-in / result-out
contract. It is not a second in-process import site, and it is not an
extension of `paper_runtime`.

**Dependency owner.** `backtest_runtime/pyproject.toml` owns
`lumibot==4.5.78` as a **base** dependency (not optional — the distribution
has no reason to exist without it), with `requires-python` declared
explicitly. The root `pyproject.toml` gains **zero** dependencies and declares
**no** new extra, preserving ADR 0002 Decision 1 verbatim.
`paper_runtime/pyproject.toml` keeps its own independent `lumibot==4.5.78`
declaration; the two are separately resolved environments that must not drift
apart silently, so a test asserts both declare the same version.

**Exact LumiBot version.** `lumibot==4.5.78`, pinned exactly, matching
`paper_runtime`. Verified installable and `pip check`-clean in a dedicated
environment (309 packages, ~1.9 GB).

**Installation method.** `pip install -e backtest_runtime/` — a real,
declared, CI-executed target. No hand-built scratch virtualenv is part of the
supported path, and no step of the install may be manual.

**Rejected alternative: extend `paper_runtime` with backtest operations
(Option A).** Rejected primarily on credential proximity, not protocol cost:
`paper_runtime` is the one process authorized to reach a real broker, and
Finding 2 above shows LumiBot autoconnects whenever credentials are visible.
Putting deterministic research work there makes every backtest a reason to
start the credentialed process. The protocol cost is also real and was
measured rather than asserted: `paper-runtime.v2` caps an envelope at 65,536
bytes, while a 3-symbol × 2-year parity fixture is 329,616 bytes of
`HistoricalBar` JSON (5.0×) and its result 85,632 bytes (1.3×), so chunking or
pagination operations would have to be added to a protocol ADR 0002
Decision 2 deliberately keeps small.

**Rejected alternative: a second in-process import directory
(`src/trading_research/backtesting/lumibot_backtest/`).** This was the first
pass's proposal. It declares no dependency, so ordinary CI never installs
LumiBot, every test skips, and the import boundary permits something no
reproducible environment exercises. Worse, the moment a developer does
hand-install LumiBot, the import runs with the repository root as CWD —
triggering the `.env` load and the live connection above.

**Rejected alternative: install LumiBot into the main environment
(Option C).** Unavailable: `ResolutionImpossible` re-confirmed at 4.5.78. Even
if resolved, it would place credential-reading, autoconnecting code in the
main process.

## Decision 2: no credentials, and it is enforced, not documented

**What is required, stated precisely.** LumiBot 4.5.78 reads credential
variable *names* on plain `import lumibot`, unconditionally, whether or not any
value exists. No configuration prevents that, so "zero credential reads" is not
an achievable requirement and is not the one imposed here. The requirement is
that no credential *value* is ever available to LumiBot. Concretely,
`backtest_runtime/` must prove all five of the following, as blocking CI
checks:

1. **no broker credential value is available to or loaded by LumiBot** — every
   credential-named variable LumiBot reads resolves to nothing, or to
   LumiBot's own hardcoded default;
2. **no credentials are loaded from the process environment or from a `.env` /
   `.env.local` file** — neither the ambient environment nor any dotenv file in
   the working directory, the script directory, or any of their ancestors;
3. **no broker and no live data provider is initialized** —
   `lumibot.credentials.broker` and `.data_source` are both `None` after import;
4. **zero outbound network attempts occur** across a full backtest, under a
   fail-closed socket guard;
5. **repeated offline runs remain deterministic** — an identical rerun produces
   a bit-identical result.

**Credential prohibition.** `backtest_runtime/` never reads, receives, stores,
or accepts a broker credential. Its entry point, **before importing
`lumibot`**, must:

1. delete every credential-named variable from `os.environ`
   (`*_API_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `ALPACA_*`, `TRADIER_*`,
   `IB_*`, `SCHWAB_*`, `COINBASE_*`, `POLYGON_*`, and the rest of the list the
   spike enumerated), and
2. set **`LUMIBOT_DISABLE_DOTENV=1`** in `os.environ`.

**The `.env` mechanism is exactly `LUMIBOT_DISABLE_DOTENV`, and nothing else
works.** `lumibot/credentials.py` reads that flag at module scope
(`_env_flag_enabled`, accepting `1`/`true`/`yes`/`on`) *before* any discovery
runs, and when it is set both discovery walks are skipped and
`dotenv.load_dotenv` is never called at all. The alternative this ADR
originally specified — pointing discovery at a path guaranteed not to exist —
**is not implementable**: the two base directories are `os.path.dirname(
os.path.abspath(sys.argv[0]))` and `os.getcwd()`, neither configurable, and
`find_and_load_dotenv()` walks *upward* from each to the filesystem root. A
run whose working directory is an empty directory therefore still loads a
`.env` from any ancestor. That was measured, not reasoned about: run S5 of the
sentinel proof executes from an empty directory and loads the parent's `.env`
anyway, builds an Alpaca broker object, and makes 58 blocked outbound attempts.
`chdir` is not a suppression mechanism; the flag is.

**The two mechanisms are independent, and both are required.** The flag
protects the `.env` path; the credential scrub protects the process
environment. Neither covers the other: a run with the flag set but no scrub
still hands inherited credentials to LumiBot and connects.

**Evidence.** `docs/library-migration/pre-step-06/dotenv_sentinel_output.txt`,
reproducible via `run_dotenv_sentinel.sh`. Two distinct fake sentinel
credential sets are planted, one in a `.env`/`.env.local` in the working
directory and one exported into the child process environment.

* **Controls** — each omitting one protection: without the flag (S1, S5) or
  without the scrub (P1), the sentinel values load into `os.environ`,
  propagate into `ALPACA_CONFIG`, construct a live broker, and drive dozens of
  blocked connection attempts to `paper-api.alpaca.markets:443` (56–63
  observed; a background retry thread makes the count vary, so only the fact
  of attempting is asserted).
* **Protected runs** (S0/S2/S3/S4 with the dotenv fixtures still in the CWD,
  P2 with the credentials still inherited): no sentinel appears in
  `os.environ` or any LumiBot config; credential-named values sourced from the
  environment are **0**; `broker` and `data_source` are both `None`; outbound
  attempts are **0**; results are bit-identical across repeats and identical
  to the clean baseline — while perturbing one input bar still moves them, so
  the backtest is consuming only the caller-supplied fixture.

The three credential-named variables that do resolve to a value under
protection are LumiBot's own hardcoded `.get()` defaults — `COINBASE_SANDBOX`,
`IB_USE_PAPER_ACCOUNT`, `DATADOWNLOADER_API_KEY_HEADER`. The evidence
distinguishes a default from an environment-sourced value by measurement (key
presence is resolved separately from the value), and asserts that set
**exactly**, so a LumiBot upgrade that resolves a fourth credential-named
variable fails the check rather than being explained in prose.

**Network prohibition.** No live broker, live data provider, or benchmark
fetch is ever initialized. `benchmark_asset=None` and `analyze_backtest=False`
are required arguments of the run, not defaults to rely on. The distribution's
own test suite installs the fail-closed socket guard from
`docs/library-migration/pre-step-06/guards.py` and asserts **zero** outbound
attempts across a full backtest — as a blocking CI check, so a future LumiBot
upgrade that reintroduces autoconnect, or a LumiBot release that renames or
drops `LUMIBOT_DISABLE_DOTENV`, fails the build rather than silently
connecting.

**Forbidden imports.** `backtest_runtime/` must never import
`trading_research` or `trading_paper_runtime`. The main project must never
import `backtest_runtime`, and `paper_runtime` must never import it either.
No live-submission operation, order-authorization path, `paper_books`
accounting module, or scheduler may be reachable from it.

**Allowed imports.** Inside `backtest_runtime/src/` only: `lumibot`,
`pandas`, and the standard library. LumiBot objects (`Order`, `Asset`,
`OrderStatus`) never cross the process boundary — only the serialized DTOs of
Decision 3 do, matching ADR 0002 Decision 2's "no shared Python types" rule.

## Decision 3: a file-based fixture/result contract, not a protocol extension

**Fixture-data contract.** Input is a caller-supplied file containing the
complete bar set plus run parameters — every bar the backtest will ever see,
supplied by the caller. The distribution has no data fetcher of any kind, and
adding one is forbidden without a superseding ADR. This preserves ADR 0002
Decision 5's "no live historical-price data source" posture and the existing
no-look-ahead constraint that `backtesting/engine.py` already satisfies.

**Result DTO contract.** Output is a single serialized result document
carrying exactly what PR 7 must compare: orders, fills, entry/exit timestamps,
prices, quantities, cash, positions, fees, realized/unrealized P&L, equity,
and drawdown — shaped to the existing `backtesting/models.py` records
(`BacktestFill`, `BacktestDailyState`, `BacktestResult`) so PR 7 compares like
with like. Both sides validate the document independently; neither imports the
other's types.

Files, not a JSON Lines protocol: the payloads exceed `paper-runtime.v2`'s
65,536-byte envelope by 5–25× at realistic parity sizes, and a file contract
has no envelope limit, is trivially diffable, and makes a parity run
reproducible from checked-in fixtures alone.

**stdout is not a channel.** LumiBot 4.5.78 writes its startup banner (58
bytes) and ANSI-escaped progress bars to fd 1 — 382–543 bytes per run,
unchanged from the 4.5.74 behavior ADR 0002 already had to work around. The
entry point must capture the real stdout handle and redirect `sys.stdout` to
`sys.stderr` before importing `lumibot`, exactly as
`paper_runtime/src/trading_paper_runtime/__main__.py` does. Because results
travel by file, stdout contamination cannot corrupt them — but the redirect is
still required so log output stays readable and a future switch to a stream
protocol is not silently unsafe.

## Decision 4: PR 6 acceptance criteria, including CI

None of the following is a precondition for *starting* PR 6. Each is a
condition for **merging** it. This ADR's acceptance is what unblocks PR 6; this
list is what PR 6 must deliver.

1. **`backtest_runtime/` exists** as a top-level directory implementing
   Decisions 1–3.
2. **`backtest_runtime/pyproject.toml` exists** and is installable on its own
   via `pip install -e backtest_runtime/`, declaring `lumibot==4.5.78` as a
   base dependency and an explicit `requires-python`.
3. **The distribution has its own tests**, carrying no `importorskip` guard —
   they must fail, not skip, when LumiBot is missing.
4. **A blocking `backtest-runtime-tests` CI job exists** that:
   * installs `backtest_runtime/` from its own `pyproject.toml`, alone, in its
     own environment (never combined with the root project or `paper_runtime`,
     matching the existing never-combine-extras convention);
   * runs `pip check`;
   * asserts the resolved LumiBot version is exactly `4.5.78`;
   * runs the distribution's tests;
   * asserts all five credential-safety properties of Decision 2 — no
     credential value available or loaded, nothing loaded from the environment
     or from a `.env`/`.env.local`, no broker or live data provider
     initialized, zero outbound attempts under the fail-closed guard, and
     determinism across a repeated identical run. It must **not** assert zero
     credential *reads*, which LumiBot makes unconditionally. It must assert
     the benign credential-named defaults as an **exact set**, so a LumiBot
     upgrade that resolves an additional one fails the job; and it must cover
     **both** leak paths, including a case where fake credentials are
     inherited from the process environment, since the `.env` flag does not
     protect that path.
5. **The existing AST boundary is repaired** (below).

The declared Python floor for this distribution is verified by the job's
`actions/setup-python` pin, not by a local observation.

**The existing AST boundary must also be repaired.** `MASTER_PLAN.md` and
`DECISIONS.md` describe the LumiBot import boundary as AST-enforced, but
`test_no_lumibot_import_outside_runtime_package` sits in a file whose
module-level `pytest.importorskip("lumibot")` makes the whole file skip under
`main-tests` (which installs `.[dev]` only). The tree-walking AST test must be
moved into a file that runs with LumiBot absent, so the boundary is enforced
rather than documented. Since `backtest_runtime/` is outside
`src/trading_research/`, that test needs **no new permitted directory** — the
existing "only `runtime/` may import lumibot" rule stays as strict as it is
today, which is a further advantage of this boundary over the in-process
proposal.

## Decision 5: how PR 7 receives parity results

PR 7 runs both engines over one fixture set and compares:

```text
fixture bar set + run parameters (one checked-in file)
        |
        +--> backtesting/engine.py, in the main environment  --> result doc
        |
        +--> backtest_runtime/, in its own environment       --> result doc
                                |
                    PR 7 compares the two documents
```

Neither environment is ever installed alongside the other, so PR 7's
comparator reads two result documents rather than importing two engines. This
is what makes the comparison possible at all under ADR 0002 Decision 1, which
forbids installing the main project beside a LumiBot environment.

## Decision 6: failure isolation and rollback

**Failure isolation.** A crash, hang, dependency break, or LumiBot regression
in `backtest_runtime/` cannot affect the main test suite, the trading desk
process, or `paper_runtime` — nothing imports it, and its CI job is the only
place it runs. It has no credentials, so its worst failure mode is a wrong or
missing research number, never an order.

**Rollback path.** Delete the `backtest_runtime/` directory and its CI job.
No root `pyproject.toml` change is reverted (there was none), no extra is
removed, no `src/` import boundary is widened and then narrowed again, and no
existing test changes behavior. PR 8's removal gate is unaffected:
`backtesting/engine.py` is untouched by this ADR and remains authoritative
until PR 7's evidence says otherwise.

## Conditions that would force reconsideration

* LumiBot resolves cleanly against the root `pyproject.toml` — i.e. `litellm`
  drops its exact `jsonschema==4.23.0` pin (or LumiBot drops
  `google-adk[extensions]`) **and** the `google-genai` wall clears **and** the
  credential-autoconnect behavior of Findings 1–3 is gone. All three, not one.
* A LumiBot release adds a documented, tested "backtest-only, never read
  credentials, never open a socket" import mode. That would make an in-process
  boundary arguable again and should reopen this ADR.
* A LumiBot release renames, removes, or changes the semantics of
  `LUMIBOT_DISABLE_DOTENV`. Decision 2's `.env` suppression rests entirely on
  that flag, and no fallback exists — `chdir` does not work, because discovery
  walks upward to the filesystem root. Decision 4's blocking CI check is
  designed to fail loudly if this happens; the response is to find the
  replacement mechanism and re-verify it with the sentinel proof, not to
  weaken the check.
* PR 7 finds the file-based contract cannot express a parity dimension it
  needs. Extend the DTO contract; do not reach for the credentialed protocol.
* A third distribution proves unsustainable to maintain in CI. The fallback is
  Option A with an explicitly raised envelope cap and chunked transfer — worse
  on credential proximity, and it would need its own ADR.
* The maintenance burden of `backtest_runtime/` exceeds the value of the PR 7
  parity evidence. PR 8 may then decide to keep `backtesting/engine.py`
  authoritative and delete the distribution outright — the rollback path above
  is exactly that.

## Consequences

* The repository will have three distributions: the main project,
  `paper_runtime/` (credentialed, live), and `backtest_runtime/`
  (uncredentialed, offline). Each owns its own LumiBot declaration or none;
  none is installed alongside another.
* The root `pyproject.toml` still gains zero dependencies from any LumiBot
  work, preserving ADR 0002 Decision 1 unchanged.
* ADR 0001's in-process boundary is **not** widened. `runtime/lumibot/`
  remains the only directory under `src/trading_research/` permitted to import
  LumiBot — the AST rule gets stricter enforcement, not a new exception.
* Backtest-mode LumiBot becomes reproducibly installable and testable in CI
  for the first time, replacing a hand-installed-scratch-virtualenv posture
  that no merge gate could verify.
* An offline backtest can no longer reach a broker even by accident, and a
  regression that would let it fails a blocking CI check.
