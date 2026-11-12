"""HTTP routes for the auth area. OWNED BY: auth area (A).

The app factory includes ``router``; define full paths here (e.g. "/v1/...").
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["auth"])
