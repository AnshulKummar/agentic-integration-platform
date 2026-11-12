"""Build live connectors from stored ``ConnectionRecord`` rows."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select

from app.platform.config import PlatformSettings
from app.platform.connectors.http import HttpConnector
from app.platform.connectors.models import ConnectionRecord
from app.platform.db import Database
from app.platform.errors import Conflict, NotFound
from app.platform.interfaces import AuthConfig, ConnectorConfig, SecretStore


def auth_from_json(data: dict[str, Any]) -> AuthConfig:
    return AuthConfig(
        kind=data.get("kind", "none"),
        secrets=dict(data.get("secrets") or {}),
        token_url=data.get("token_url"),
        scopes=tuple(data.get("scopes") or ()),
        header_name=data.get("header_name") or "X-API-Key",
    )


def auth_to_json(auth: AuthConfig) -> dict[str, Any]:
    return {
        "kind": auth.kind,
        "secrets": dict(auth.secrets),
        "token_url": auth.token_url,
        "scopes": list(auth.scopes),
        "header_name": auth.header_name,
    }


def config_from_record(record: ConnectionRecord) -> ConnectorConfig:
    return ConnectorConfig(
        system_id=record.system_id,
        base_url=record.base_url,
        spec=record.spec,
        auth=auth_from_json(record.auth),
        tool_prefix=record.tool_prefix,
        health_path=record.health_path or "/health",
    )


async def load_connection(db: Database, tenant_id: str, system_id: str) -> ConnectionRecord:
    async with db.session(tenant_id) as session:
        stmt = select(ConnectionRecord).where(
            ConnectionRecord.tenant_id == tenant_id, ConnectionRecord.system_id == system_id
        )
        record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise NotFound(f"connection {system_id!r} not found")
    return record


async def build_connector(
    db: Database,
    secret_store: SecretStore,
    settings: PlatformSettings,
    tenant_id: str,
    system_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    allow_disabled: bool = False,
) -> HttpConnector:
    """A connector for the tenant's stored connection. Caller must ``aclose()`` it.

    Disabled connections raise ``Conflict`` unless ``allow_disabled`` (health probes).
    """
    record = await load_connection(db, tenant_id, system_id)
    if not record.enabled and not allow_disabled:
        raise Conflict(f"connection {system_id!r} is disabled", code="connection_disabled")
    return HttpConnector(config_from_record(record), secret_store, tenant_id, settings, transport)
