"""HTTP routes for tenant connections to external systems."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.audit import record_audit
from app.platform.config import PlatformSettings
from app.platform.connectors.http import REQUIRED_SECRETS, tool_definition, tools_from_spec
from app.platform.connectors.models import ConnectionRecord
from app.platform.connectors.registry import auth_from_json, auth_to_json, build_connector
from app.platform.db import Database
from app.platform.deps import get_database, get_settings, get_tenant_session
from app.platform.errors import NotFound, ValidationFailed
from app.platform.interfaces import AuthConfig, AuthKind
from app.platform.principal import Principal, Role, current_principal, require_roles
from app.platform.secrets.deps import get_secret_store
from app.platform.secrets.store import NAME_RE, DbSecretStore

router = APIRouter(tags=["connectors"])

SYSTEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,99}$")
PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
HEADER_RE = re.compile(r"^[A-Za-z0-9\-]{1,100}$")


class AuthIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AuthKind = "none"
    #: logical key -> secret NAME (never a value), e.g. {"api_key": "esign_api_key"}.
    secrets: dict[str, str] = Field(default_factory=dict)
    token_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    header_name: str = "X-API-Key"


class ConnectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=500)
    #: Inline OpenAPI 3 document (fetching spec_url server-side is deliberately not supported).
    spec: dict[str, Any]
    auth: AuthIn = Field(default_factory=AuthIn)
    tool_prefix: str = ""
    health_path: str = Field(default="/health", max_length=200)
    enabled: bool = True


class ConnectionOut(BaseModel):
    system_id: str
    base_url: str
    auth: AuthIn
    tool_prefix: str
    health_path: str
    enabled: bool
    tool_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, record: ConnectionRecord) -> ConnectionOut:
        prefix = record.tool_prefix or record.system_id
        return cls(
            system_id=record.system_id,
            base_url=record.base_url,
            auth=AuthIn.model_validate(auth_to_json(auth_from_json(record.auth))),
            tool_prefix=record.tool_prefix,
            health_path=record.health_path,
            enabled=record.enabled,
            tool_count=len(tools_from_spec(record.spec, prefix)),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ToolsOut(BaseModel):
    system_id: str
    tools: list[dict[str, Any]]


class HealthOut(BaseModel):
    system_id: str
    ok: bool
    latency_ms: float
    detail: str


def _validate_http_url(value: str, field: str) -> None:
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, TypeError) as exc:
        raise ValidationFailed(f"{field} is not a valid URL") from exc
    if url.scheme not in ("http", "https") or not url.host:
        raise ValidationFailed(f"{field} must be an absolute http(s) URL")
    if url.userinfo:
        raise ValidationFailed(f"{field} must not embed credentials; reference secrets in auth.secrets")


async def _validate(system_id: str, body: ConnectionIn, store: DbSecretStore, tenant_id: str) -> AuthConfig:
    if not SYSTEM_ID_RE.match(system_id):
        raise ValidationFailed("system_id must be 1-100 chars of a-z, 0-9, '_' or '-'")
    _validate_http_url(body.base_url, "base_url")
    if body.tool_prefix and not PREFIX_RE.match(body.tool_prefix):
        raise ValidationFailed("tool_prefix must match ^[a-z][a-z0-9_]{0,63}$")
    if not body.health_path.startswith("/"):
        raise ValidationFailed("health_path must start with '/'")
    tools_from_spec(body.spec, body.tool_prefix or system_id)  # raises 422 if unusable
    auth = body.auth
    missing = [k for k in REQUIRED_SECRETS[auth.kind] if k not in auth.secrets]
    if missing:
        raise ValidationFailed(f"auth kind {auth.kind!r} needs secrets for: {', '.join(missing)}")
    if auth.kind == "oauth2_client_credentials":
        if not auth.token_url:
            raise ValidationFailed("oauth2_client_credentials needs token_url")
        if not auth.token_url.startswith("/"):
            _validate_http_url(auth.token_url, "auth.token_url")
    if not HEADER_RE.match(auth.header_name):
        raise ValidationFailed("auth.header_name is not a valid header name")
    for logical, secret_name in auth.secrets.items():
        if not NAME_RE.match(secret_name):
            # Do not echo the value: it may be a secret pasted by mistake.
            raise ValidationFailed(f"auth.secrets[{logical!r}] must be the NAME of a stored secret")
    names = sorted(set(auth.secrets.values()))
    unknown = sorted(set(names) - await store.exists(tenant_id, names))
    if unknown:
        raise ValidationFailed(f"unknown secret name(s): {', '.join(unknown)}; create them via PUT /v1/secrets")
    return AuthConfig(
        kind=auth.kind,
        secrets=dict(auth.secrets),
        token_url=auth.token_url,
        scopes=tuple(auth.scopes),
        header_name=auth.header_name,
    )


async def _get_record(session: AsyncSession, tenant_id: str, system_id: str) -> ConnectionRecord | None:
    stmt = select(ConnectionRecord).where(
        ConnectionRecord.tenant_id == tenant_id, ConnectionRecord.system_id == system_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _require_record(session: AsyncSession, tenant_id: str, system_id: str) -> ConnectionRecord:
    record = await _get_record(session, tenant_id, system_id)
    if record is None:
        raise NotFound(f"connection {system_id!r} not found")
    return record


@router.put("/v1/connections/{system_id}", response_model=ConnectionOut)
async def put_connection(
    system_id: str,
    body: ConnectionIn,
    principal: Annotated[Principal, Depends(require_roles(Role.ENGINEER))],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
    store: Annotated[DbSecretStore, Depends(get_secret_store)],
) -> ConnectionOut:
    """Create or replace a connection (admin or engineer)."""
    auth = await _validate(system_id, body, store, principal.tenant_id)
    record = await _get_record(session, principal.tenant_id, system_id)
    created = record is None
    if record is None:
        record = ConnectionRecord(tenant_id=principal.tenant_id, system_id=system_id)
        session.add(record)
    record.base_url = body.base_url.rstrip("/")
    record.spec = body.spec
    record.auth = auth_to_json(auth)
    record.tool_prefix = body.tool_prefix
    record.health_path = body.health_path
    record.enabled = body.enabled
    record_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="connection.created" if created else "connection.updated",
        target_type="connection",
        target_id=system_id,
        detail={
            "base_url": record.base_url,
            "auth_kind": auth.kind,
            "secret_names": sorted(auth.secrets.values()),
            "enabled": body.enabled,
        },
    )
    await session.commit()
    await session.refresh(record)
    return ConnectionOut.of(record)


@router.get("/v1/connections", response_model=list[ConnectionOut])
async def list_connections(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[ConnectionOut]:
    stmt = (
        select(ConnectionRecord)
        .where(ConnectionRecord.tenant_id == principal.tenant_id)
        .order_by(ConnectionRecord.system_id)
    )
    return [ConnectionOut.of(r) for r in (await session.execute(stmt)).scalars().all()]


@router.get("/v1/connections/{system_id}", response_model=ConnectionOut)
async def get_connection(
    system_id: str,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> ConnectionOut:
    return ConnectionOut.of(await _require_record(session, principal.tenant_id, system_id))


@router.get("/v1/connections/{system_id}/tools", response_model=ToolsOut)
async def list_tools(
    system_id: str,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> ToolsOut:
    record = await _require_record(session, principal.tenant_id, system_id)
    tools = tools_from_spec(record.spec, record.tool_prefix or record.system_id)
    return ToolsOut(system_id=system_id, tools=[tool_definition(t) for t in tools])


@router.post("/v1/connections/{system_id}/health", response_model=HealthOut)
async def probe_health(
    system_id: str,
    principal: Annotated[Principal, Depends(current_principal)],
    db: Annotated[Database, Depends(get_database)],
    settings: Annotated[PlatformSettings, Depends(get_settings)],
    store: Annotated[DbSecretStore, Depends(get_secret_store)],
) -> HealthOut:
    connector = await build_connector(db, store, settings, principal.tenant_id, system_id, allow_disabled=True)
    try:
        status = await connector.health()
    finally:
        await connector.aclose()
    return HealthOut(system_id=system_id, ok=status.ok, latency_ms=round(status.latency_ms, 2), detail=status.detail)


@router.delete("/v1/connections/{system_id}", status_code=204)
async def delete_connection(
    system_id: str,
    principal: Annotated[Principal, Depends(require_roles(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> Response:
    record = await _require_record(session, principal.tenant_id, system_id)
    record_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="connection.deleted",
        target_type="connection",
        target_id=system_id,
    )
    await session.delete(record)
    await session.commit()
    return Response(status_code=204)
