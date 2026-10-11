# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/16-opentelemetry-tracing`
- Reviewed HEAD: `94397bc8cb4eac19fd9f2e64f78e00cf8f9e065e`
- Subject: PR 16: wire in OpenTelemetry as new, additive, offline-safe instrumentation
- Review scope: INCREMENTAL
- Fix round: 2
- Trigger: GitHub review threads on PR #34
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `9732aa1bc3571f772316231dc71e4dcdd7fec12c`
- Prior round: the P1 JSON-exception-secret finding and the P2
  configure-after-shutdown finding recorded at HEAD
  `8f73889c146a78887415ae2731654539d76dd16e` were fixed in
  `6c9019b5542d6db57d58efae0711871f5dd672db`; both remain fixed at the
  HEAD reviewed here, and this round's two findings are new and distinct

## Resolution

Both P2 findings below are fixed in
`9732aa1bc3571f772316231dc71e4dcdd7fec12c`
(`src/trading_research/observability.py`,
`tests/unit/test_observability.py`). The finding bodies are kept verbatim
as the record of what was reviewed and confirmed.

1. External global providers -- `configure_telemetry()` now refuses to
   report success it cannot deliver. A pre-registration check plus a
   post-registration identity check against
   `trace.get_tracer_provider()`/`metrics.get_meter_provider()` (the only
   reliable signal, since the API refuses replacement by log line alone)
   decide whether this module actually owns each global. If another party
   owns one, it raises the new `ExternalTelemetryProviderError`;
   `adopt_existing_providers=True` is the explicit opt-in to run under
   the already-registered provider(s), which are left untouched,
   excluded from `shutdown_telemetry()`, and reported by the new
   `uses_adopted_providers()`. A provider that loses a registration race
   is shut down rather than leaked.
2. Concurrent initialization -- a module `threading.Lock` now serializes
   `configure_telemetry`/`shutdown_telemetry` in full, including the
   `_configured` check, so exactly one concurrent first call builds and
   registers providers and the rest are no-ops; the stored references
   are therefore always the process globals.
   `is_configured`/`uses_adopted_providers` stay lock-free single-`bool`
   reads so a status query never blocks on someone else's call.

Regression coverage runs each lifecycle scenario in a fresh subprocess
with a hermetic environment, because OpenTelemetry's global registry is
per-process and one-shot and is already consumed by the existing
in-process test: external-provider refusal, full adoption,
adopt-one/register-the-other, post-shutdown reconfiguration behavior in
each case, and a deterministic two-thread serialization test asserting
global identity and that shutdown shuts down the global providers.

Validation after the fix:

- `pytest tests/` with the opt-in gates disabled -- 3454 passed,
  57 skipped.
- `pytest tests/unit/test_observability.py
  tests/unit/test_observability_import_boundary.py` -- 15 passed.
- `scripts/check_links.sh` -- 184 OK, 0 errors.
- `nox -s ci` (`tests`, `paper_tests`, `safety_typecheck`,
  `migration_smoke`) -- all succeeded on this change set.
- No broker access, credentialed tests, schedulers, model calls, or order
  operations were performed.

## Findings

### [P2] Confirmed: `configure_telemetry()` reports success while externally registered global providers stay in place

Commit: 8f73889c146a78887415ae2731654539d76dd16e

Location: src/trading_research/observability.py:111

Concern: If OpenTelemetry auto-instrumentation or another library has
already registered the global tracer/meter providers,
`configure_telemetry()` may still mark itself configured and store
providers that never become global.

Evidence: CONFIRMED by a fresh-subprocess probe that registered an
external `TracerProvider`/`MeterProvider` before importing this module,
then called `configure_telemetry("probe-service")`. The call returned
normally (exit code 0) and printed:

```
is_configured True
global_tracer_is_external True
global_meter_is_external True
stored_tracer_is_global False
stored_meter_is_global False
```

with only `Overriding of current TracerProvider is not allowed` /
`Overriding of current MeterProvider is not allowed` on stderr --
OpenTelemetry's `set_tracer_provider`/`set_meter_provider` log a warning
and keep the existing global. So `is_configured()` claimed success while
both globals belonged to the external registrant and the two providers
this module stored and would later `shutdown()` could never receive a
span or metric. This is the same class of lie `TelemetryReconfigurationError`
was added to prevent after shutdown, on the not-yet-covered
pre-registration path.

Potential impact if confirmed: Under `opentelemetry-instrument` or
alongside any library that configures OpenTelemetry first, callers
believe this module's resource/exporter settings are in effect while
spans and metrics go to the foreign provider; `shutdown_telemetry()` then
flushes and shuts down the wrong (unused) providers and leaves the live
global ones running.

Investigation and conditional remediation: Verify provider identity in a
fresh subprocess with pre-registered providers. If confirmed, fail
explicitly or implement a defined existing-provider policy, and test it.

Validation: Subprocess regression tests asserting the raised error, the
untouched external globals, and the documented adoption policy. Run
`nox -s tests -- tests/unit/test_observability.py`, then `nox -s ci`.

### [P2] Confirmed: concurrent first calls to `configure_telemetry()` leave globals and stored provider references inconsistent

Commit: 8f73889c146a78887415ae2731654539d76dd16e

Location: src/trading_research/observability.py:131

Concern: Concurrent first calls to `configure_telemetry()` may interleave
because the `_configured` check and the provider construction/
registration/assignment that follow it are not protected by any lock.

Evidence: CONFIRMED by a fresh-subprocess probe with deterministic
synchronization: `build_tracer_provider` was wrapped so that both threads
block on a 2-thread `threading.Barrier` after passing the `_configured`
guard and building their own provider. Two threads then called
`configure_telemetry("svc-a")` and `configure_telemetry("svc-b")`. No
exception was raised in either thread, and the probe printed:

```
errors []
is_configured True
stored_tracer_is_global False
stored_meter_is_global False
stored_tracer_service svc-a
global_tracer_service svc-b
```

Both calls passed the unguarded `_configured` check, both built
providers, one won the global registration (`svc-b`) and the other's
assignment to `_tracer_provider`/`_meter_provider` landed last (`svc-a`),
so the module's stored references are not the process globals.

Potential impact if confirmed: In a process that configures telemetry
from more than one thread, the globals carry one service resource while
`is_configured()` reports success and `shutdown_telemetry()` shuts down
the losing providers -- leaving the real global providers live (including
a `PeriodicExportingMetricReader` export thread under
`console_export=True`) while the module believes telemetry is torn down.

Investigation and conditional remediation: Verify with deterministic
synchronization around provider construction/registration. If confirmed,
serialize configuration and shutdown and test global identity plus
shutdown behavior.

Validation: Subprocess regression test asserting serialization, that the
stored providers are the globals, which service name won, and that
`shutdown_telemetry()` shuts down the global provider. Run
`nox -s tests -- tests/unit/test_observability.py`, then `nox -s ci`.

Tests or diagnostics run:

- Fresh-subprocess external-provider probe — confirmed finding 1.
- Fresh-subprocess barrier-synchronized concurrency probe — confirmed
  finding 2.
- No broker access, credentialed tests, schedulers, model calls, or order
  operations were performed.
