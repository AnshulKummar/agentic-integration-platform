"""ASGI entry point: ``uvicorn app.platform.main:app``."""

from app.platform.app import create_app

app = create_app()
