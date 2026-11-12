"""FastAPI dependency giving routes (any area) the process's ``DbSecretStore``.

The app factory does not create a store (shared file, owned elsewhere), so it
is built lazily on first use and cached on ``app.state.secret_store``. It is
rebuilt if ``app.state.db`` was swapped (tests point the app at their database).
"""

from __future__ import annotations

from fastapi import Request

from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.secrets.store import DbSecretStore


def get_secret_store(request: Request) -> DbSecretStore:
    state = request.app.state
    db: Database = state.db
    settings: PlatformSettings = state.settings
    existing = getattr(state, "secret_store", None)
    if isinstance(existing, DbSecretStore) and existing.db is db:
        return existing
    store = DbSecretStore(db, settings)
    state.secret_store = store
    return store
