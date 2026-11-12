"""Per-request log context (request id, tenant id) carried in contextvars.

The middleware installs a mutable ``RequestContext`` for each request; the
``bind_principal_to_log_context`` dependency fills in the tenant once the
caller is authenticated. Because the object is shared (not copied), the
middleware's own access-log line also sees the tenant id.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.platform.principal import Principal, current_principal


@dataclass
class RequestContext:
    request_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None


_ctx: ContextVar[RequestContext | None] = ContextVar("pmaip_request_context", default=None)


def current_context() -> RequestContext | None:
    return _ctx.get()


def push_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _ctx.set(ctx)


def pop_context(token: Token[RequestContext | None]) -> None:
    _ctx.reset(token)


def set_log_tenant(tenant_id: str | None, user_id: str | None = None) -> None:
    """Attach the tenant (and user) to every log line for the rest of this request/task."""
    ctx = _ctx.get()
    if ctx is None:
        _ctx.set(RequestContext(tenant_id=tenant_id, user_id=user_id))
        return
    ctx.tenant_id = tenant_id
    if user_id is not None:
        ctx.user_id = user_id


async def bind_principal_to_log_context(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    """Router dependency: authenticate and tag logs with the caller's tenant.

    Other areas can add ``dependencies=[Depends(bind_principal_to_log_context)]``
    to their routers (or call ``set_log_tenant``) to get tenant_id in their logs.
    """
    set_log_tenant(principal.tenant_id, principal.user_id)
    return principal
