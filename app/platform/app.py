"""App factory. ``create_app(settings)`` is the only way the API is built."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.platform import observability, runtime
from app.platform.auth.router import router as auth_router
from app.platform.config import PlatformSettings
from app.platform.connectors.router import router as connectors_router
from app.platform.db import Database
from app.platform.deployments.router import router as deployments_router
from app.platform.errors import install_error_handlers
from app.platform.evals.router import router as evals_router
from app.platform.integrations.router import router as integrations_router
from app.platform.observability.router import router as observability_router
from app.platform.secrets.redaction import install_redaction
from app.platform.secrets.router import router as secrets_router
from app.platform.secrets.store import DbSecretStore

ROUTERS = (
    observability_router,
    auth_router,
    integrations_router,
    secrets_router,
    connectors_router,
    deployments_router,
    evals_router,
)


def create_app(settings: PlatformSettings | None = None) -> FastAPI:
    settings = (settings or PlatformSettings.from_env()).validate_runtime()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_schema:
            await db.create_all()
        try:
            yield
        finally:
            await db.dispose()

    app = FastAPI(
        title="PMAIP Integration Platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.db = db
    app.state.secret_store = DbSecretStore(db, settings)
    runtime.install()

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_error_handlers(app)
    observability.setup(app, settings)
    install_redaction()
    for router in ROUTERS:
        app.include_router(router)
    return app
