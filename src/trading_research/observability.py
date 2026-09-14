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

_configured = False
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def _console_export_from_env() -> bool:
    return os.environ.get(_CONSOLE_EXPORT_ENV_VAR, "") == "1"


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


def configure_telemetry(
    service_name: str = _DEFAULT_SERVICE_NAME,
    *,
    console_export: bool | None = None,
) -> None:
    """Register global `TracerProvider`/`MeterProvider` for process-wide use.

    Idempotent, mirroring `logging_config.configure_logging`'s
    idempotent-reconfiguration contract: a second call is a no-op rather
    than attempting to register a second global provider (which the
    OpenTelemetry API itself would refuse and warn about).

    `console_export` defaults to the `TRADING_RESEARCH_OTEL_CONSOLE`
    environment variable (`"1"` enables it) when not passed explicitly, so
    tests and default runs stay offline-safe without any code change.
    """
    global _configured, _tracer_provider, _meter_provider
    if _configured:
        return

    if console_export is None:
        console_export = _console_export_from_env()

    resource = build_resource(service_name)
    _tracer_provider = build_tracer_provider(resource, console_export=console_export)
    _meter_provider = build_meter_provider(resource, console_export=console_export)
    trace.set_tracer_provider(_tracer_provider)
    metrics.set_meter_provider(_meter_provider)
    _configured = True


def is_configured() -> bool:
    """Whether `configure_telemetry` has already run in this process."""
    return _configured


def shutdown_telemetry() -> None:
    """Flush and shut down the providers `configure_telemetry` registered.

    `PeriodicExportingMetricReader` (used when `console_export=True`) owns a
    background export thread that otherwise keeps running -- and, at
    interpreter shutdown, can race a closed stdout -- until this is called.
    A no-op if `configure_telemetry` was never called.

    Shuts down the concrete SDK provider objects `configure_telemetry`
    itself built and kept a reference to, rather than re-fetching through
    `trace.get_tracer_provider()`/`metrics.get_meter_provider()`: those
    return the API's abstract provider type, which declares no `shutdown()`
    method (only the SDK implementation does).
    """
    global _configured, _tracer_provider, _meter_provider
    if not _configured:
        return
    assert _tracer_provider is not None
    assert _meter_provider is not None
    _tracer_provider.shutdown()
    _meter_provider.shutdown()
    _tracer_provider = None
    _meter_provider = None
    _configured = False
