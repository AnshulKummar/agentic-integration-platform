"""Optional OpenTelemetry tracing. No hard dependency: if ``otel_endpoint`` is
unset or the ``opentelemetry`` packages are not importable, tracing is disabled
and that is logged once per process."""

from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

from app.platform.config import PlatformSettings

log = logging.getLogger(__name__)

_disabled_logged = False


def _log_disabled_once(reason: str) -> None:
    global _disabled_logged
    if not _disabled_logged:
        _disabled_logged = True
        log.info("tracing disabled: %s", reason, extra={"tracing": "disabled"})


def setup_tracing(app: FastAPI, settings: PlatformSettings) -> bool:
    """Returns True if tracing was enabled."""
    if not settings.otel_endpoint:
        _log_disabled_once("PLATFORM_OTEL_ENDPOINT is not set")
        return False
    try:
        trace = importlib.import_module("opentelemetry.trace")
        sdk_trace = importlib.import_module("opentelemetry.sdk.trace")
        export = importlib.import_module("opentelemetry.sdk.trace.export")
        resources = importlib.import_module("opentelemetry.sdk.resources")
        otlp = importlib.import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        fastapi_inst = importlib.import_module("opentelemetry.instrumentation.fastapi")
    except ImportError as exc:
        _log_disabled_once(f"opentelemetry packages not installed ({exc.name})")
        return False
    provider = sdk_trace.TracerProvider(resource=resources.Resource.create({"service.name": "pmaip-platform"}))
    provider.add_span_processor(export.BatchSpanProcessor(otlp.OTLPSpanExporter(endpoint=settings.otel_endpoint)))
    trace.set_tracer_provider(provider)
    fastapi_inst.FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    log.info("tracing enabled", extra={"tracing": "enabled"})
    return True
