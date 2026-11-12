"""Append-only audit trail. Call inside the same transaction as the change."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.core_models import AuditEvent


def record_audit(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str | None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Stage an audit row (flushed/committed with the caller's transaction).

    ``detail`` must never contain secret values; pass names/fingerprints only.
    """
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail or {},
    )
    session.add(event)
    return event
