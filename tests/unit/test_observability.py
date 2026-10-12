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
touches OpenTelemetry's real global provider registry *in-process* -- the
API silently refuses to override an already-registered provider, so that
one test covers both "sets the global provider" and "a second call is a
no-op" in a single function rather than relying on cross-test ordering of
a shared process-global. Most other tests exercise
`build_tracer_provider`/`build_meter_provider` directly, which never
touch global state.

The remaining lifecycle tests each need a *pristine* global registry --
no provider registered yet, or one registered by a pretend external
library before this module runs -- which is unreachable once anything in
this process has registered one. They therefore run their body in a fresh
subprocess via `_run_lifecycle_script`, assert inside that subprocess
(where the global state actually lives), and let the parent test assert
only on the exit status.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

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


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _run_lifecycle_script(body: str) -> None:
    """Run `body` in a fresh interpreter, failing this test if it exits non-zero.

    The assertions live inside the script because the state under test --
    OpenTelemetry's process-global provider registry plus this module's
    configuration flags -- is per-process and one-shot: it cannot be
    restored between tests in this interpreter, where
    `test_configure_telemetry_registers_global_providers_and_is_idempotent`
    has already consumed it.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        # Drop the environment this module itself reads, so a developer
        # shell that exports any of it cannot change what the script
        # under test sees: console export would attach exporters the
        # assertions do not expect, and either provider environment
        # variable would make `get_*_provider()` load and register an
        # external provider, i.e. silently change the scenario.
        if name
        not in {
            "TRADING_RESEARCH_OTEL_CONSOLE",
            "OTEL_PYTHON_TRACER_PROVIDER",
            "OTEL_PYTHON_METER_PROVIDER",
        }
    }
    # `src` first, and no empty path entry (which would add the CWD).
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(REPO_ROOT / "src"), os.environ.get("PYTHONPATH", "")) if path
    )
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"lifecycle subprocess exited {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )


def test_configure_telemetry_refuses_externally_registered_global_providers():
    """Review thread: `configure_telemetry()` used to report success while
    the globals stayed external and its own providers sat unused."""
    _run_lifecycle_script(
        """
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.trace import TracerProvider

        # Stand in for OpenTelemetry auto-instrumentation, or any library
        # that configures telemetry before this module gets a chance to.
        external_tracer = TracerProvider()
        external_meter = MeterProvider()
        trace.set_tracer_provider(external_tracer)
        metrics.set_meter_provider(external_meter)

        from trading_research import observability

        try:
            observability.configure_telemetry("ours")
        except observability.ExternalTelemetryProviderError as exc:
            message = str(exc)
        else:
            raise AssertionError(
                "configure_telemetry() reported success while both global "
                "providers belonged to an external registrant"
            )

        assert "TracerProvider and MeterProvider" in message, message
        assert "adopt_existing_providers=True" in message, message

        # The external registrant keeps both globals; this module claims
        # nothing and stores no provider that could never be used.
        assert observability.is_configured() is False
        assert observability.uses_adopted_providers() is False
        assert trace.get_tracer_provider() is external_tracer
        assert metrics.get_meter_provider() is external_meter
        assert observability._tracer_provider is None
        assert observability._meter_provider is None

        # An unconfigured shutdown stays a no-op: it must never shut down
        # a provider this module does not own.
        observability.shutdown_telemetry()
        assert external_meter._shutdown is False
        """
    )


def test_configure_telemetry_adopts_external_global_providers_on_request():
    _run_lifecycle_script(
        """
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.trace import TracerProvider

        external_tracer = TracerProvider()
        external_meter = MeterProvider()
        trace.set_tracer_provider(external_tracer)
        metrics.set_meter_provider(external_meter)

        from trading_research import observability

        observability.configure_telemetry("ours", adopt_existing_providers=True)

        # Adoption is explicit about what it means: configured, but with
        # somebody else's providers, which stay exactly as they were.
        assert observability.is_configured() is True
        assert observability.uses_adopted_providers() is True
        assert trace.get_tracer_provider() is external_tracer
        assert metrics.get_meter_provider() is external_meter
        assert observability._tracer_provider is None
        assert observability._meter_provider is None

        observability.shutdown_telemetry()
        assert observability.is_configured() is False
        assert observability.uses_adopted_providers() is False
        # Not ours, so not shut down -- the registrant's telemetry keeps
        # working after this module is torn down.
        assert external_meter._shutdown is False

        # Adoption registered nothing, so OpenTelemetry's one-shot global
        # slot was never consumed and adopting again is still truthful.
        observability.configure_telemetry("ours-again", adopt_existing_providers=True)
        assert observability.is_configured() is True
        assert trace.get_tracer_provider() is external_tracer
        """
    )


def test_configure_telemetry_adopts_only_the_external_global_and_registers_the_other():
    _run_lifecycle_script(
        """
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider

        # Only the meter provider is external this time.
        external_meter = MeterProvider()
        metrics.set_meter_provider(external_meter)

        from trading_research import observability

        try:
            observability.configure_telemetry("ours")
        except observability.ExternalTelemetryProviderError as exc:
            message = str(exc)
        else:
            raise AssertionError(
                "configure_telemetry() reported success while the global "
                "meter provider belonged to an external registrant"
            )

        # The error names the global that is actually external, not both.
        assert "MeterProvider" in message, message
        assert "TracerProvider" not in message, message
        assert observability.is_configured() is False

        observability.configure_telemetry("ours", adopt_existing_providers=True)

        assert observability.is_configured() is True
        assert observability.uses_adopted_providers() is True
        # The external meter provider is adopted untouched...
        assert metrics.get_meter_provider() is external_meter
        assert observability._meter_provider is None
        # ...while the still-unset tracer global is registered from here.
        tracer_provider = trace.get_tracer_provider()
        assert observability._tracer_provider is tracer_provider
        assert tracer_provider.resource.attributes["service.name"] == "ours"

        observability.shutdown_telemetry()
        assert external_meter._shutdown is False

        # This configuration *did* register a provider of its own, so it
        # consumed the one-shot global slot and cannot be redone.
        try:
            observability.configure_telemetry("after", adopt_existing_providers=True)
        except observability.TelemetryReconfigurationError:
            pass
        else:
            raise AssertionError(
                "configure_telemetry() ran again after shutting down a "
                "provider it had registered globally"
            )
        """
    )


def test_concurrent_first_configure_telemetry_calls_are_serialized():
    """Review thread: the `_configured` check and the provider
    construction/registration/assignment after it used to be unprotected,
    so two concurrent first calls could both build and register, leaving
    the stored references pointing at providers that are not the globals."""
    _run_lifecycle_script(
        """
        import threading

        from opentelemetry import metrics, trace

        from trading_research import observability

        inside_build = threading.Event()
        release_build = threading.Event()
        parked = []
        real_build_tracer_provider = observability.build_tracer_provider

        def blocking_build_tracer_provider(resource, *, console_export=False):
            provider = real_build_tracer_provider(resource, console_export=console_export)
            # Park the *first* caller only, deep inside the critical
            # section: past the "already configured" check and holding a
            # freshly built provider that is not registered yet. That is
            # the exact window in which a second first-caller used to be
            # able to build and register one of its own.
            if not parked:
                parked.append(True)
                inside_build.set()
                assert release_build.wait(timeout=30)
            return provider

        observability.build_tracer_provider = blocking_build_tracer_provider

        errors = []

        def configure(service_name, returned=None):
            try:
                observability.configure_telemetry(service_name)
            except BaseException as exc:  # noqa: BLE001
                errors.append(repr(exc))
            if returned is not None:
                returned.set()

        first = threading.Thread(target=configure, args=("first-service",))
        first.start()
        assert inside_build.wait(timeout=30)

        second_returned = threading.Event()
        second = threading.Thread(target=configure, args=("second-service", second_returned))
        second.start()
        # While the first call is still inside configure_telemetry(), the
        # second must not be able to get through it.
        assert not second_returned.wait(timeout=1.0), (
            "configure_telemetry() is not serialized: a second first-call "
            "returned while the first was still mid-configuration"
        )

        release_build.set()
        first.join(timeout=30)
        second.join(timeout=30)
        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == [], errors

        # Exactly one call configured telemetry, and what this module
        # stored is what is actually registered globally.
        assert observability.is_configured() is True
        assert observability.uses_adopted_providers() is False
        tracer_provider = trace.get_tracer_provider()
        meter_provider = metrics.get_meter_provider()
        assert observability._tracer_provider is tracer_provider
        assert observability._meter_provider is meter_provider
        assert tracer_provider.resource.attributes["service.name"] == "first-service"

        # ...so shutdown shuts down those same global providers, rather
        # than a losing call's unused ones.
        observability.shutdown_telemetry()
        assert observability.is_configured() is False
        assert meter_provider._shutdown is True
        """
    )


def test_split_registration_keeps_the_winning_meter_provider_owned():
    """Review thread: when an external registrant takes the tracer global
    between the pre-check and `_register_tracer_provider`, the meter
    provider this module *did* register used to be discarded unrecorded --
    `shutdown_telemetry()` became a no-op while that process-global
    provider's `PeriodicExportingMetricReader` kept exporting."""
    _run_lifecycle_script(
        """
        import threading

        from opentelemetry import metrics, trace
        from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

        from trading_research import observability

        class RecordingSpanProcessor(SpanProcessor):
            def __init__(self):
                self.shut_down = False

            def shutdown(self):
                self.shut_down = True

        # The external registrant's own provider, instrumented so the
        # assertions below can tell whether this module ever shut down a
        # provider that is not its to shut down.
        external_processor = RecordingSpanProcessor()
        external_tracer = TracerProvider()
        external_tracer.add_span_processor(external_processor)

        real_set_tracer_provider = trace.set_tracer_provider
        at_registration = threading.Barrier(2)

        def racing_set_tracer_provider(provider):
            # Reached from inside `_register_tracer_provider`, i.e. after
            # configure_telemetry() passed its external-provider
            # pre-check. Hand over to the competing thread here and wait
            # for it to finish registering, which makes the review
            # thread's race window deterministic: the external provider
            # always takes the one-shot global slot first, so this
            # module's tracer always loses.
            at_registration.wait(timeout=30)
            at_registration.wait(timeout=30)
            real_set_tracer_provider(provider)

        observability.trace.set_tracer_provider = racing_set_tracer_provider

        def external_registrant():
            at_registration.wait(timeout=30)
            real_set_tracer_provider(external_tracer)
            at_registration.wait(timeout=30)

        competitor = threading.Thread(target=external_registrant)
        competitor.start()
        try:
            # `console_export=True` is the case the review thread called
            # out: the meter provider that wins carries a live
            # `PeriodicExportingMetricReader` background export thread.
            try:
                observability.configure_telemetry("ours", console_export=True)
            except observability.ExternalTelemetryProviderError as exc:
                message = str(exc)
            else:
                raise AssertionError(
                    "configure_telemetry() reported success after losing "
                    "the tracer global to an external registrant"
                )
        finally:
            observability.trace.set_tracer_provider = real_set_tracer_provider
            competitor.join(timeout=30)
            assert not competitor.is_alive()

        # Half-success is still reported as failure, and named precisely:
        # only the tracer global is external.
        assert "TracerProvider" in message, message
        assert "MeterProvider" not in message, message
        assert observability.is_configured() is False

        # The tracer this module built lost and was disowned; the
        # external one is untouched and still the global.
        assert observability._tracer_provider is None
        assert trace.get_tracer_provider() is external_tracer

        # The meter this module registered *won*, so it stays owned --
        # this is the regression: it used to be dropped on the floor.
        owned_meter = observability._meter_provider
        assert owned_meter is not None
        assert metrics.get_meter_provider() is owned_meter
        assert owned_meter._all_metric_readers, "expected the live console metric reader"
        assert owned_meter._shutdown is False

        # So shutdown is not a no-op: it tears down exactly the provider
        # this module registered, and nothing else.
        observability.shutdown_telemetry()
        assert owned_meter._shutdown is True
        assert external_processor.shut_down is False, (
            "shutdown_telemetry() shut down an external registrant's provider"
        )

        # A global slot *was* consumed from here, so reconfiguration in
        # this process is impossible and says so.
        assert observability._shutdown is True
        try:
            observability.configure_telemetry("after", adopt_existing_providers=True)
        except observability.TelemetryReconfigurationError:
            pass
        else:
            raise AssertionError(
                "configure_telemetry() ran again after shutting down a "
                "provider it had registered globally"
            )
        """
    )


def test_split_registration_keeps_the_winning_tracer_provider_owned():
    """The mirror of the review thread's case: this module wins the tracer
    global and loses the meter global."""
    _run_lifecycle_script(
        """
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.trace import SpanProcessor

        from trading_research import observability

        class RecordingSpanProcessor(SpanProcessor):
            def __init__(self):
                self.shut_down = False

            def shutdown(self):
                self.shut_down = True

        our_processor = RecordingSpanProcessor()
        real_build_tracer_provider = observability.build_tracer_provider

        def instrumented_build_tracer_provider(resource, *, console_export=False):
            provider = real_build_tracer_provider(resource, console_export=console_export)
            provider.add_span_processor(our_processor)
            return provider

        observability.build_tracer_provider = instrumented_build_tracer_provider

        # Lose the meter global the same way, without threads: an
        # external registrant taking the slot immediately before this
        # module's own call is exactly the outcome of the race window,
        # and is deterministic.
        external_meter = MeterProvider()
        real_set_meter_provider = metrics.set_meter_provider

        def racing_set_meter_provider(provider):
            real_set_meter_provider(external_meter)
            real_set_meter_provider(provider)

        observability.metrics.set_meter_provider = racing_set_meter_provider
        try:
            try:
                observability.configure_telemetry("ours", console_export=True)
            except observability.ExternalTelemetryProviderError as exc:
                message = str(exc)
            else:
                raise AssertionError(
                    "configure_telemetry() reported success after losing "
                    "the meter global to an external registrant"
                )
        finally:
            observability.metrics.set_meter_provider = real_set_meter_provider
            observability.build_tracer_provider = real_build_tracer_provider

        assert "MeterProvider" in message, message
        assert "TracerProvider" not in message, message
        assert observability.is_configured() is False

        assert observability._meter_provider is None
        assert metrics.get_meter_provider() is external_meter

        owned_tracer = observability._tracer_provider
        assert owned_tracer is not None
        assert trace.get_tracer_provider() is owned_tracer

        observability.shutdown_telemetry()
        assert our_processor.shut_down is True
        assert external_meter._shutdown is False, (
            "shutdown_telemetry() shut down an external registrant's provider"
        )
        assert observability._shutdown is True
        """
    )


def test_configure_telemetry_after_a_lost_race_describes_only_the_external_global():
    """A retry after a partial registration must treat the global this
    module already owns as its own, not as somebody else's."""
    _run_lifecycle_script(
        """
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.trace import TracerProvider

        from trading_research import observability

        external_tracer = TracerProvider()
        real_set_tracer_provider = trace.set_tracer_provider

        def racing_set_tracer_provider(provider):
            real_set_tracer_provider(external_tracer)
            real_set_tracer_provider(provider)

        observability.trace.set_tracer_provider = racing_set_tracer_provider
        try:
            try:
                observability.configure_telemetry("ours")
            except observability.ExternalTelemetryProviderError:
                pass
            else:
                raise AssertionError("expected the tracer registration to lose")
        finally:
            observability.trace.set_tracer_provider = real_set_tracer_provider

        owned_meter = observability._meter_provider
        assert owned_meter is not None

        # Both globals are now set, but only the tracer belongs to
        # another party. A plain retry must still refuse -- and must name
        # only that one, rather than claiming the meter provider it
        # registered itself is external too.
        try:
            observability.configure_telemetry("retry")
        except observability.ExternalTelemetryProviderError as exc:
            message = str(exc)
        else:
            raise AssertionError(
                "configure_telemetry() reported success while the tracer "
                "global belonged to an external registrant"
            )
        assert "TracerProvider" in message, message
        assert "MeterProvider" not in message, message

        # The refused retry changed nothing: no provider was rebuilt, and
        # the one this module owns is still the global.
        assert observability._meter_provider is owned_meter
        assert metrics.get_meter_provider() is owned_meter
        assert owned_meter._shutdown is False
        assert observability.is_configured() is False

        # Opting in adopts the external tracer *alongside* the meter
        # provider that is already owned here, which is the one coherent
        # outcome left once the one-shot slots are spoken for.
        observability.configure_telemetry("retry", adopt_existing_providers=True)
        assert observability.is_configured() is True
        assert observability.uses_adopted_providers() is True
        assert trace.get_tracer_provider() is external_tracer
        assert observability._tracer_provider is None
        assert observability._meter_provider is owned_meter

        observability.shutdown_telemetry()
        assert owned_meter._shutdown is True
        """
    )


def test_configure_telemetry_registers_nothing_when_provider_construction_fails():
    """A construction failure must not consume a one-shot global slot:
    both providers are built before either is registered, so the call
    stays fully retryable."""
    _run_lifecycle_script(
        """
        from opentelemetry import metrics, trace

        from trading_research import observability

        proxy_tracer, proxy_meter = observability._unset_global_provider_proxy_types()
        assert proxy_tracer is not None and proxy_meter is not None

        real_build_meter_provider = observability.build_meter_provider

        def failing_build_meter_provider(resource, *, console_export=False):
            raise RuntimeError("meter provider construction failed")

        observability.build_meter_provider = failing_build_meter_provider
        try:
            try:
                observability.configure_telemetry("probe", console_export=True)
            except RuntimeError as exc:
                assert "meter provider construction failed" in str(exc), exc
            else:
                raise AssertionError("expected the construction failure to propagate")
        finally:
            observability.build_meter_provider = real_build_meter_provider

        # Nothing was registered, nothing is owned, nothing is claimed.
        assert observability.is_configured() is False
        assert observability._tracer_provider is None
        assert observability._meter_provider is None
        assert isinstance(trace.get_tracer_provider(), proxy_tracer)
        assert isinstance(metrics.get_meter_provider(), proxy_meter)

        # Shutdown has nothing to do, and must not burn the lifecycle.
        observability.shutdown_telemetry()
        assert observability._shutdown is False

        # The retry therefore gets the configuration it asked for.
        observability.configure_telemetry("retry-service")
        assert observability.is_configured() is True
        assert observability.uses_adopted_providers() is False
        tracer_provider = trace.get_tracer_provider()
        assert observability._tracer_provider is tracer_provider
        assert observability._meter_provider is metrics.get_meter_provider()
        assert tracer_provider.resource.attributes["service.name"] == "retry-service"

        observability.shutdown_telemetry()
        """
    )
