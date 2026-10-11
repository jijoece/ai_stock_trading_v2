"""OpenTelemetry tracing/metrics SDK wiring (library-migration PR 16).

New capability, not a migration of existing code: no tracing/metrics
implementation existed in this repository before this module (see
`docs/library-migration/COMPONENT_MATRIX.md`'s "Tracing/metrics (spans)"
row). `research/cycle_telemetry.py` and `paper_books/metrics.py` are
domain-specific telemetry (research-cycle status tracking, paper-books
accounting metrics) and are unrelated and unaffected -- this module only
sets up OpenTelemetry's global tracer/meter providers.

Offline-safe by default: `build_tracer_provider`/`build_meter_provider`
register no span/metric exporter unless `console_export=True` is passed
explicitly (or the `TRADING_RESEARCH_OTEL_CONSOLE` environment variable is
set to `"1"`), so building or configuring telemetry never attempts a
network call. There is no OTLP/network exporter in this module -- adding
one is future scope, out of this PR's additive-only bound
(`docs/library-migration/MASTER_PLAN.md` row 16).

Single-owner, thread-safe lifecycle: OpenTelemetry's global
tracer/meter providers can only ever be registered once per process, so
`configure_telemetry` either owns them or refuses to claim it does. It
raises `ExternalTelemetryProviderError` when another party (OpenTelemetry
auto-instrumentation, another library, or the
`OTEL_PYTHON_TRACER_PROVIDER`/`OTEL_PYTHON_METER_PROVIDER` environment
variables) already registered a provider, unless the caller opts into
`adopt_existing_providers=True`; it raises
`TelemetryReconfigurationError` after `shutdown_telemetry`; and both
functions serialize on a module lock so concurrent first calls cannot
leave the globals and this module's stored references disagreeing.

`opentelemetry-sdk` stays an optional `observability` extra
(`pyproject.toml`), not a base dependency: unlike `structlog` (PR 15,
promoted to a base dependency because `logging_config.get_logger` is
imported unconditionally by modules the default `.[dev]` test environment
already exercises), nothing in this repository imports this module
unconditionally -- it is opt-in instrumentation, so a plain `.[dev]`
install must keep working without the OpenTelemetry SDK installed at all
(note: `opentelemetry-api` alone is already a transitive base dependency
via `mcp`, which is why this module's tests guard on `opentelemetry.sdk`
specifically, not just `opentelemetry` -- see
`tests/unit/test_observability.py`).
"""
from __future__ import annotations

import os
import sys
import threading

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

_DEFAULT_SERVICE_NAME = "trading-research"
_CONSOLE_EXPORT_ENV_VAR = "TRADING_RESEARCH_OTEL_CONSOLE"

# Serializes the whole of `configure_telemetry`/`shutdown_telemetry`,
# including the `_configured` check: without it, concurrent first calls
# both pass that check, both build providers, one wins the global
# registration and the other's assignments below land last, leaving the
# stored references pointing at providers that are not the process
# globals (and that `shutdown_telemetry` would then shut down instead of
# the live ones).
_state_lock = threading.Lock()

_configured = False
_shutdown = False
# The providers this module built *and* successfully registered as the
# process globals, i.e. the ones it owns and must shut down. `None` while
# unconfigured, and also when the corresponding global was adopted from an
# external registrant (`adopt_existing_providers=True`) -- an adopted
# provider belongs to whoever registered it and must not be shut down here.
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_adopted_external = False


class TelemetryReconfigurationError(RuntimeError):
    """Raised by `configure_telemetry` after `shutdown_telemetry` has run.

    OpenTelemetry's global `TracerProvider`/`MeterProvider` registration is
    one-shot per process: `trace.set_tracer_provider`/
    `metrics.set_meter_provider` silently refuse (only logging a warning) to
    replace an already-registered provider. Building fresh providers here
    anyway and reporting `is_configured() == True` afterward would be a lie
    -- the real global providers would remain the shut-down originals, so
    new spans/metrics would silently attach to dead providers. There is no
    way to truthfully "reconfigure" telemetry within the same process once
    shut down; callers that need a fresh setup must start a new process.
    """


class ExternalTelemetryProviderError(RuntimeError):
    """Raised by `configure_telemetry` when another party owns the globals.

    Same underlying one-shot constraint as
    `TelemetryReconfigurationError`, on the other path into it: if
    OpenTelemetry auto-instrumentation (`opentelemetry-instrument`),
    another library, or the `OTEL_PYTHON_TRACER_PROVIDER`/
    `OTEL_PYTHON_METER_PROVIDER` environment variables already registered
    a global provider, `trace.set_tracer_provider`/
    `metrics.set_meter_provider` only log "Overriding of current
    TracerProvider is not allowed" and leave the external provider in
    place. Reporting `is_configured() == True` anyway would mean this
    module's resource and exporter settings are silently not in effect,
    its stored providers could never receive a span or metric, and
    `shutdown_telemetry` would flush and shut those unused providers down
    while the live global ones keep running.

    Callers that deliberately run under externally configured telemetry
    can pass `adopt_existing_providers=True` to use the already-registered
    provider(s) as-is instead of getting this error.
    """


def _console_export_from_env() -> bool:
    return os.environ.get(_CONSOLE_EXPORT_ENV_VAR, "") == "1"


def _unset_global_provider_proxy_types() -> tuple[type | None, type | None]:
    """Return the API's "nothing registered yet" proxy provider classes.

    `trace.get_tracer_provider()`/`metrics.get_meter_provider()` return a
    proxy provider while no global provider has been registered, so a
    non-proxy return value means some other party owns the global. Both
    proxy classes are `opentelemetry-api` implementation details
    (`_ProxyMeterProvider` is private outright) and this module's pin
    allows any 1.x (`pyproject.toml`), so they are looked up defensively:
    a name that moves in a future 1.x yields `None` rather than an
    ImportError at module import, and `configure_telemetry` then relies
    solely on its authoritative post-registration identity check.
    """
    proxy_tracer = getattr(trace, "ProxyTracerProvider", None)
    proxy_meter = getattr(getattr(metrics, "_internal", None), "_ProxyMeterProvider", None)
    return (
        proxy_tracer if isinstance(proxy_tracer, type) else None,
        proxy_meter if isinstance(proxy_meter, type) else None,
    )


def _external_global_providers() -> tuple[bool, bool]:
    """Best-effort `(tracer, meter)` "is this global already external?" pair.

    Lets `configure_telemetry` fail (or adopt) *before* registering
    anything, which is the common case: auto-instrumentation registers
    both providers at process start, long before any call here. It is
    best-effort only -- `False` also means "could not tell" (see
    `_unset_global_provider_proxy_types`) -- so the post-registration
    identity check in `_register_tracer_provider`/
    `_register_meter_provider` remains the authoritative signal.

    Note `get_tracer_provider()`/`get_meter_provider()` themselves load
    and permanently register the provider named by
    `OTEL_PYTHON_TRACER_PROVIDER`/`OTEL_PYTHON_METER_PROVIDER` when those
    are set; that is an explicit operator request for an external
    provider, which this check then correctly reports as external.
    """
    proxy_tracer, proxy_meter = _unset_global_provider_proxy_types()
    tracer_external = proxy_tracer is not None and not isinstance(
        trace.get_tracer_provider(), proxy_tracer
    )
    meter_external = proxy_meter is not None and not isinstance(
        metrics.get_meter_provider(), proxy_meter
    )
    return tracer_external, meter_external


def _external_provider_message(tracer_external: bool, meter_external: bool) -> str:
    owned = [
        name
        for name, external in (("TracerProvider", tracer_external), ("MeterProvider", meter_external))
        if external
    ]
    return (
        "configure_telemetry() will not report success while another party "
        f"owns the global {' and '.join(owned)}: OpenTelemetry's global "
        "provider registration is one-shot per process, so this module's "
        "providers would never receive any span or metric. Pass "
        "adopt_existing_providers=True to use the already-registered "
        "provider(s) as-is instead."
    )


def build_resource(service_name: str = _DEFAULT_SERVICE_NAME) -> Resource:
    """Build the `service.name`-tagged `Resource` shared by both providers."""
    return Resource.create({"service.name": service_name})


def build_tracer_provider(resource: Resource, *, console_export: bool = False) -> TracerProvider:
    """Build a `TracerProvider`. No span processor is attached unless
    `console_export` is set, so a default-built provider exports nothing --
    spans can be created and ended safely with no network or filesystem
    side effect.
    """
    provider = TracerProvider(resource=resource)
    if console_export:
        # Bind `sys.stdout` here, at call time, rather than relying on
        # `ConsoleSpanExporter`'s own `out=sys.stdout` default -- that
        # default is evaluated once, when `opentelemetry` is first
        # imported, so it would permanently hold whatever stream
        # `sys.stdout` was at that moment, not the current one (e.g. a
        # test's `capsys`/`capfd` redirection, made after import).
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout)))
    return provider


def build_meter_provider(resource: Resource, *, console_export: bool = False) -> MeterProvider:
    """Build a `MeterProvider`. No metric reader is attached unless
    `console_export` is set, so a default-built provider exports nothing.
    """
    readers: list[MetricReader] = []
    if console_export:
        # See `build_tracer_provider` above for why `out=sys.stdout` is
        # bound explicitly here rather than left to `ConsoleMetricExporter`'s
        # own import-time-bound default.
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(out=sys.stdout)))
    return MeterProvider(resource=resource, metric_readers=readers)


def _register_tracer_provider(
    resource: Resource, *, console_export: bool
) -> TracerProvider | None:
    """Register a fresh `TracerProvider` globally; return it only if it won.

    Identity against `trace.get_tracer_provider()` after the call is the
    only reliable "did this actually become the global provider" signal:
    the API refuses to replace an existing global and reports that by log
    line alone, not by return value or exception. A provider that lost is
    shut down right away rather than returned -- it is an orphan that can
    never receive a span, and under `console_export=True` it owns a live
    span processor -- and `None` is returned to say "the global is
    external".
    """
    provider = build_tracer_provider(resource, console_export=console_export)
    trace.set_tracer_provider(provider)
    if trace.get_tracer_provider() is provider:
        return provider
    provider.shutdown()
    return None


def _register_meter_provider(resource: Resource, *, console_export: bool) -> MeterProvider | None:
    """Register a fresh `MeterProvider` globally; return it only if it won.

    See `_register_tracer_provider`; shutting down a provider that lost
    matters even more here, since `console_export=True` attaches a
    `PeriodicExportingMetricReader` with a background export thread.
    """
    provider = build_meter_provider(resource, console_export=console_export)
    metrics.set_meter_provider(provider)
    if metrics.get_meter_provider() is provider:
        return provider
    provider.shutdown()
    return None


def configure_telemetry(
    service_name: str = _DEFAULT_SERVICE_NAME,
    *,
    console_export: bool | None = None,
    adopt_existing_providers: bool = False,
) -> None:
    """Register global `TracerProvider`/`MeterProvider` for process-wide use.

    Idempotent while still configured, mirroring
    `logging_config.configure_logging`'s idempotent-reconfiguration
    contract: a second call before shutdown is a no-op rather than
    attempting to register a second global provider (which the
    OpenTelemetry API itself would refuse and warn about). After
    `shutdown_telemetry()` has run, this raises `TelemetryReconfigurationError`
    instead of silently rebuilding providers that could never actually
    become the process-global ones -- see that exception's docstring.

    Serialized with `shutdown_telemetry()` on a module lock, so that
    concurrent first calls cannot both pass the "already configured"
    check: exactly one call builds and registers the providers and the
    others return as no-ops, which keeps the stored provider references
    and the process globals the same objects.

    Raises `ExternalTelemetryProviderError` if some other party already
    registered a global tracer or meter provider, rather than reporting
    success while this module's providers sit unused outside the global
    registry -- see that exception's docstring. Pass
    `adopt_existing_providers=True` to instead use whatever is already
    registered: each external global is left untouched and excluded from
    `shutdown_telemetry()` (it belongs to its registrant), while any
    global still unset is registered from here as usual.

    `console_export` defaults to the `TRADING_RESEARCH_OTEL_CONSOLE`
    environment variable (`"1"` enables it) when not passed explicitly, so
    tests and default runs stay offline-safe without any code change.
    """
    global _configured, _tracer_provider, _meter_provider, _adopted_external
    with _state_lock:
        if _configured:
            return
        if _shutdown:
            raise TelemetryReconfigurationError(
                "configure_telemetry() cannot run again in this process: "
                "shutdown_telemetry() has already run, and OpenTelemetry's "
                "global providers cannot be re-registered."
            )

        if console_export is None:
            console_export = _console_export_from_env()

        tracer_external, meter_external = _external_global_providers()
        if (tracer_external or meter_external) and not adopt_existing_providers:
            raise ExternalTelemetryProviderError(
                _external_provider_message(tracer_external, meter_external)
            )

        resource = build_resource(service_name)
        tracer_provider = (
            None
            if tracer_external
            else _register_tracer_provider(resource, console_export=console_export)
        )
        meter_provider = (
            None
            if meter_external
            else _register_meter_provider(resource, console_export=console_export)
        )

        if not adopt_existing_providers and (tracer_provider is None or meter_provider is None):
            # The pre-check saw an unset global but registration still
            # lost it, so a provider was registered from outside this
            # module between the two -- another library on another thread,
            # since this module's own calls are serialized by
            # `_state_lock`. Keep the fail-explicitly contract rather than
            # reporting a success that only half happened. Nothing is left
            # to clean up for the provider that lost (already shut down by
            # its `_register_*` helper); a provider that *won* before the
            # other lost stays registered as the process global, where it
            # is harmless -- it exports nothing unless `console_export`
            # was requested -- but it is deliberately not stored or shut
            # down here, since a shut-down global provider would silently
            # swallow everyone's spans.
            raise ExternalTelemetryProviderError(
                _external_provider_message(tracer_provider is None, meter_provider is None)
            )

        _tracer_provider = tracer_provider
        _meter_provider = meter_provider
        _adopted_external = tracer_provider is None or meter_provider is None
        _configured = True


def is_configured() -> bool:
    """Whether `configure_telemetry` has already run in this process.

    Deliberately reads `_configured` without taking `_state_lock`: it is a
    single `bool` read (the lock guards the compound state *updates*), and
    a status query must not block for the duration of someone else's
    `configure_telemetry`/`shutdown_telemetry` call.
    """
    return _configured


def uses_adopted_providers() -> bool:
    """Whether the current configuration adopted an external global provider.

    `True` only after a `configure_telemetry(adopt_existing_providers=True)`
    call that actually found an externally registered global, i.e. when
    this module's resource/exporter settings are (at least partly) not the
    ones in effect, and `shutdown_telemetry()` will leave that global
    running. Read without the lock, for the reason in `is_configured`.
    """
    return _adopted_external


def shutdown_telemetry() -> None:
    """Flush and shut down the providers `configure_telemetry` registered.

    `PeriodicExportingMetricReader` (used when `console_export=True`) owns a
    background export thread that otherwise keeps running -- and, at
    interpreter shutdown, can race a closed stdout -- until this is called.
    A no-op if `configure_telemetry` was never called.

    Shuts down the concrete SDK provider objects `configure_telemetry`
    itself built and registered as the process globals, rather than
    re-fetching through `trace.get_tracer_provider()`/
    `metrics.get_meter_provider()`: those return the API's abstract
    provider type, which declares no `shutdown()` method (only the SDK
    implementation does). Providers adopted from an external registrant
    (`adopt_existing_providers=True`) are skipped -- they belong to
    whoever registered them, and shutting down a global this module does
    not own would silently kill its owner's telemetry.

    Serialized with `configure_telemetry` on the same module lock.
    """
    global _configured, _shutdown, _tracer_provider, _meter_provider, _adopted_external
    with _state_lock:
        if not _configured:
            return
        if _tracer_provider is not None:
            _tracer_provider.shutdown()
        if _meter_provider is not None:
            _meter_provider.shutdown()
        # Only a provider actually registered from here consumes
        # OpenTelemetry's one-shot global slot, so only then is
        # reconfiguration in this process impossible (and
        # `TelemetryReconfigurationError` the truthful answer). An
        # adoption-only configuration registered nothing and burnt
        # nothing, so it stays re-runnable.
        if _tracer_provider is not None or _meter_provider is not None:
            _shutdown = True
        _tracer_provider = None
        _meter_provider = None
        _adopted_external = False
        _configured = False
