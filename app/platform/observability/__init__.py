"""Observability: logging, metrics, tracing, health endpoints.

OWNED BY THE OBSERVABILITY/DEPLOYMENTS AREA. ``setup`` is called once by the
app factory before routers are included.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.platform.config import PlatformSettings


def setup(app: FastAPI, settings: PlatformSettings) -> None:
    """Install logging, request-id/metrics middleware and tracing (stub)."""
    return None
