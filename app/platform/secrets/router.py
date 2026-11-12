"""HTTP routes for the secrets area. OWNED BY: secrets/connectors area (B).

The app factory includes ``router``; define full paths here (e.g. "/v1/...").
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["secrets"])
