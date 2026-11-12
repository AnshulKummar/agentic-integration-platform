"""Observability: logging, metrics, tracing, health endpoints.

OWNED BY THE OBSERVABILITY/DEPLOYMENTS AREA. ``setup`` is called once by the
app factory before routers are included.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.platform.config import PlatformSettings
from app.platform.observability.logs import configure_logging
from app.platform.observability.middleware import ObservabilityMiddleware
from app.platform.observability.tracing import setup_tracing


def setup(app: FastAPI, settings: PlatformSettings) -> None:
    """Install structured logging, request-id/metrics middleware and (optional) tracing."""
    configure_logging(settings.log_level, json_lines=settings.log_json)
    app.add_middleware(ObservabilityMiddleware, metrics_enabled=settings.metrics_enabled)
    setup_tracing(app, settings)
