"""HTTP routes for the observability area. OWNED BY: deployments/observability area (D).

    GET /healthz   liveness: the process is up (no dependencies checked)
    GET /readyz    readiness: DB reachable (and docker binary present when sandbox_backend='docker');
                   503 with reasons otherwise
    GET /metrics   Prometheus exposition of metrics.REGISTRY
"""

from __future__ import annotations

import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.deps import get_database, get_settings
from app.platform.errors import NotFound
from app.platform.observability.metrics import REGISTRY

log = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    db: Annotated[Database, Depends(get_database)], settings: Annotated[PlatformSettings, Depends(get_settings)]
) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await db.ping()
        checks["database"] = "ok"
    except Exception as exc:
        # Report the failure type only: driver messages can include the DSN.
        log.warning("readiness: database ping failed", exc_info=True)
        checks["database"] = f"unreachable ({type(exc).__name__})"
    if settings.sandbox_backend == "docker":
        checks["docker"] = "ok" if shutil.which("docker") else "docker binary not found on PATH"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "unavailable", "checks": checks}, status_code=200 if ready else 503
    )


@router.get("/metrics")
async def metrics(settings: Annotated[PlatformSettings, Depends(get_settings)]) -> Response:
    """Prometheus scrape endpoint. UNAUTHENTICATED by design (scrapers): it MUST be
    restricted at the ingress / network policy (internal scrape network only)."""
    if not settings.metrics_enabled:
        raise NotFound("metrics are disabled")
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
