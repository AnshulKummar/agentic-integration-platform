"""HTTP routes for the deployments area. OWNED BY: deployments/observability area (D).

The app factory includes ``router``; define full paths here (e.g. "/v1/...").
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["deployments"])
