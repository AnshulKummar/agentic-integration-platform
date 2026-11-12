"""HTTP routes for the evals area. OWNED BY: evals area (E).

The app factory includes ``router``; define full paths here (e.g. "/v1/...").
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["evals"])
