"""Fixtures for the secrets area."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.secrets.redaction import SecretRegistry
from app.platform.secrets.store import DbSecretStore


@pytest.fixture
def registry() -> Iterator[SecretRegistry]:
    reg = SecretRegistry()
    yield reg
    reg.clear()


@pytest.fixture
def store(db: Database, settings: PlatformSettings, registry: SecretRegistry) -> DbSecretStore:
    return DbSecretStore(db, settings, registry=registry)
