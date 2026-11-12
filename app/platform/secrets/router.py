"""HTTP routes for tenant secrets. Values are write-only: no route ever returns one."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.platform.interfaces import SecretMetadata
from app.platform.principal import Principal, Role, current_principal, require_roles
from app.platform.secrets.deps import get_secret_store
from app.platform.secrets.store import DbSecretStore, validate_name

router = APIRouter(tags=["secrets"])


class SecretPutIn(BaseModel):
    # The value is never echoed: length/emptiness are checked in the store, which
    # raises messages that do not include the input (FastAPI 422s would echo it).
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    value: str
    description: str = Field(default="", max_length=500)


class SecretOut(BaseModel):
    name: str
    fingerprint: str
    version: int
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, meta: SecretMetadata) -> SecretOut:
        return cls(
            name=meta.name,
            fingerprint=meta.fingerprint,
            version=meta.version,
            description=meta.description,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )


@router.put("/v1/secrets/{name}", response_model=SecretOut)
async def put_secret(
    name: str,
    body: SecretPutIn,
    principal: Annotated[Principal, Depends(require_roles(Role.ENGINEER))],
    store: Annotated[DbSecretStore, Depends(get_secret_store)],
) -> SecretOut:
    """Create or rotate a secret (admin or engineer). Returns metadata only."""
    validate_name(name)
    meta = await store.put(
        principal.tenant_id, name, body.value, description=body.description, actor_id=principal.user_id
    )
    return SecretOut.of(meta)


@router.get("/v1/secrets", response_model=list[SecretOut])
async def list_secrets(
    principal: Annotated[Principal, Depends(current_principal)],
    store: Annotated[DbSecretStore, Depends(get_secret_store)],
) -> list[SecretOut]:
    return [SecretOut.of(m) for m in await store.list(principal.tenant_id)]


@router.delete("/v1/secrets/{name}", status_code=204)
async def delete_secret(
    name: str,
    principal: Annotated[Principal, Depends(require_roles(Role.ADMIN))],
    store: Annotated[DbSecretStore, Depends(get_secret_store)],
) -> Response:
    await store.delete(principal.tenant_id, name, actor_id=principal.user_id)
    return Response(status_code=204)
