"""Behavioral tests for `observability.py` (library-migration PR 16).

`opentelemetry-sdk` stays an optional `observability` extra (unlike
`structlog`, promoted to a base dependency in PR 15 -- see DECISIONS.md
D13) since no production module imports it unconditionally
(`tests/unit/test_observability_import_boundary.py` enforces that). This
file therefore skips without the extra installed, the same pattern as
`test_indicators.py`/`test_analytics_parity.py`.

Guarded on `opentelemetry.sdk` specifically, not just `opentelemetry`: the
`mcp` package (a base dependency) requires `opentelemetry-api` on its own,
so the top-level `opentelemetry` package -- the API only, no SDK -- is
already importable under a plain `.[dev]` install. `opentelemetry.sdk`
(this module's actual `TracerProvider`/`MeterProvider` dependency) is only
present with the `observability` extra installed, matching the CI
`dependency-extras-smoke` job's own `import opentelemetry.sdk` check.

Only `test_configure_telemetry_registers_global_providers_and_is_idempotent`
touches OpenTelemetry's real global provider registry -- the API silently
refuses to override an already-registered provider, so that one test
covers both "sets the global provider" and "a second call is a no-op" in a
single function rather than relying on cross-test ordering of a shared
process-global. Every other test exercises `build_tracer_provider`/
`build_meter_provider` directly, which never touch global state.
"""
from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import metrics, trace  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402

from trading_research import observability  # noqa: E402


def test_build_resource_sets_service_name():
    resource = observability.build_resource("custom-service")
    assert resource.attributes["service.name"] == "custom-service"


def test_build_resource_defaults_to_trading_research():
    resource = observability.build_resource()
    assert resource.attributes["service.name"] == "trading-research"


def test_build_tracer_provider_default_is_offline_safe(capsys):
    provider = observability.build_tracer_provider(observability.build_resource("svc"))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("op"):
        pass
    provider.force_flush()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_build_tracer_provider_console_export_writes_span(capsys):
    provider = observability.build_tracer_provider(observability.build_resource("svc"), console_export=True)
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("my-traced-span"):
        pass
    provider.force_flush()

    captured = capsys.readouterr()
    assert "my-traced-span" in captured.out


def test_build_meter_provider_default_is_offline_safe(capsys):
    provider = observability.build_meter_provider(observability.build_resource("svc"))
    meter = provider.get_meter("test")
    counter = meter.create_counter("requests_total")
    counter.add(1)
    provider.force_flush()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_build_meter_provider_console_export_writes_metric(capsys):
    provider = observability.build_meter_provider(observability.build_resource("svc"), console_export=True)
    try:
        meter = provider.get_meter("test")
        counter = meter.create_counter("requests_total")
        counter.add(1)
        provider.force_flush()

        captured = capsys.readouterr()
        assert "requests_total" in captured.out
    finally:
        # `console_export=True` attaches a `PeriodicExportingMetricReader`,
        # which owns a background export thread; shut it down explicitly so
        # it does not linger past this test and race a closed stream at
        # interpreter exit.
        provider.shutdown()


def test_configure_telemetry_registers_global_providers_and_is_idempotent():
    assert observability.is_configured() is False

    observability.configure_telemetry(service_name="trading-research-test", console_export=False)
    assert observability.is_configured() is True

    tracer_provider = trace.get_tracer_provider()
    assert isinstance(tracer_provider, TracerProvider)
    assert tracer_provider.resource.attributes["service.name"] == "trading-research-test"

    meter_provider = metrics.get_meter_provider()
    assert isinstance(meter_provider, MeterProvider)

    # A second call is a no-op: OpenTelemetry itself refuses to re-register
    # a global provider, so this also proves configure_telemetry does not
    # error or attempt to override it.
    observability.configure_telemetry(service_name="different-service", console_export=True)
    assert trace.get_tracer_provider() is tracer_provider
    assert metrics.get_meter_provider() is meter_provider

    observability.shutdown_telemetry()
    assert observability.is_configured() is False

    # A shutdown-then-nothing-configured call is a safe no-op.
    observability.shutdown_telemetry()

    # OpenTelemetry's global providers are one-shot per process: once shut
    # down, they cannot be replaced. configure_telemetry must say so
    # explicitly rather than silently building fresh providers that could
    # never actually become the process-global ones, while is_configured()
    # falsely reports success and the real global providers stay attached
    # to the shut-down originals.
    with pytest.raises(observability.TelemetryReconfigurationError):
        observability.configure_telemetry(service_name="post-shutdown-service", console_export=False)
    assert observability.is_configured() is False
    assert trace.get_tracer_provider() is tracer_provider
    assert metrics.get_meter_provider() is meter_provider
    assert tracer_provider.resource.attributes["service.name"] == "trading-research-test"
