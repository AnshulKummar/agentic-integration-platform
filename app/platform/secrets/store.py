"""Database-backed ``SecretStore`` (envelope-encrypted, per tenant)."""

from __future__ import annotations

import builtins
import logging
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.audit import record_audit
from app.platform.auth.rls import allow_cross_tenant
from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.errors import Conflict, NotFound, PlatformError, ValidationFailed
from app.platform.interfaces import SecretMetadata
from app.platform.secrets.crypto import (
    Envelope,
    Fingerprinter,
    KeyProvider,
    SecretDecryptionError,
    build_aad,
    open_envelope,
    provider_from_settings,
    rewrap,
    seal,
)
from app.platform.secrets.models import SecretRecord
from app.platform.secrets.redaction import SECRET_REGISTRY, SecretRegistry

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
MAX_VALUE_BYTES = 64 * 1024


class SecretUnreadable(PlatformError):
    """Stored secret cannot be decrypted (wrong master key or tampered row)."""

    status = 500
    code = "secret_unreadable"


def validate_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValidationFailed("secret name must be 1-128 chars of letters, digits, '_', '.', '-'")
    return name


def validate_value(value: str) -> str:
    if not value:
        raise ValidationFailed("secret value must not be empty")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValidationFailed(f"secret value exceeds {MAX_VALUE_BYTES} bytes")
    return value


def to_metadata(record: SecretRecord) -> SecretMetadata:
    return SecretMetadata(
        name=record.name,
        fingerprint=record.fingerprint,
        version=record.version,
        description=record.description,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _envelope(record: SecretRecord) -> Envelope:
    return Envelope(
        ciphertext=record.ciphertext, nonce=record.nonce, wrapped_key=record.wrapped_key, kek_id=record.kek_id
    )


class DbSecretStore:
    """Implements ``interfaces.SecretStore``. Every operation runs in a tenant-bound session.

    ``put``/``delete`` write an audit row (name, version, fingerprint, never the
    value) in the same transaction; ``actor_id`` attributes it.
    """

    def __init__(
        self,
        db: Database,
        settings: PlatformSettings,
        *,
        key_provider: KeyProvider | None = None,
        registry: SecretRegistry | None = None,
    ) -> None:
        self.db = db
        self.provider = key_provider or provider_from_settings(settings)
        self._fingerprint = Fingerprinter(settings.master_key_bytes())
        self._registry = registry or SECRET_REGISTRY

    def __repr__(self) -> str:
        return f"DbSecretStore(provider={self.provider!r})"

    @staticmethod
    async def _get(session: AsyncSession, tenant_id: str, name: str) -> SecretRecord | None:
        stmt = select(SecretRecord).where(SecretRecord.tenant_id == tenant_id, SecretRecord.name == name)
        result = await session.execute(stmt.with_for_update())
        return result.scalar_one_or_none()

    async def put(
        self, tenant_id: str, name: str, value: str, *, description: str = "", actor_id: str | None = None
    ) -> SecretMetadata:
        validate_name(name)
        validate_value(value)
        self._registry.register(value)
        fingerprint = self._fingerprint(tenant_id, value)
        async with self.db.session(tenant_id) as session:
            record = await self._get(session, tenant_id, name)
            version = 1 if record is None else record.version + 1
            envelope = await seal(self.provider, value, build_aad(tenant_id, name, version))
            if record is None:
                record = SecretRecord(tenant_id=tenant_id, name=name)
                session.add(record)
            record.version = version
            record.ciphertext = envelope.ciphertext
            record.nonce = envelope.nonce
            record.wrapped_key = envelope.wrapped_key
            record.kek_id = envelope.kek_id
            record.fingerprint = fingerprint
            record.description = description
            record_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="secret.created" if version == 1 else "secret.rotated",
                target_type="secret",
                target_id=name,
                detail={"name": name, "version": version, "fingerprint": fingerprint},
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise Conflict(f"secret {name!r} was written concurrently; retry") from exc
            await session.refresh(record)
            log.info("secret stored", extra={"tenant_id": tenant_id, "secret": name, "version": version})
            return to_metadata(record)

    async def reveal(self, tenant_id: str, name: str) -> str:
        async with self.db.session(tenant_id) as session:
            record = (
                await session.execute(
                    select(SecretRecord).where(SecretRecord.tenant_id == tenant_id, SecretRecord.name == name)
                )
            ).scalar_one_or_none()
            if record is None:
                raise NotFound(f"secret {name!r} not found")
            aad = build_aad(record.tenant_id, record.name, record.version)
            try:
                value = await open_envelope(self.provider, _envelope(record), aad)
            except SecretDecryptionError as exc:
                log.error("secret %r (tenant %s) could not be decrypted: %s", name, tenant_id, exc)
                raise SecretUnreadable(f"secret {name!r} could not be decrypted") from exc
        self._registry.register(value)
        return value

    async def delete(self, tenant_id: str, name: str, *, actor_id: str | None = None) -> None:
        async with self.db.session(tenant_id) as session:
            record = await self._get(session, tenant_id, name)
            if record is None:
                raise NotFound(f"secret {name!r} not found")
            record_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="secret.deleted",
                target_type="secret",
                target_id=name,
                detail={"name": name, "version": record.version, "fingerprint": record.fingerprint},
            )
            await session.delete(record)
            await session.commit()

    async def get_metadata(self, tenant_id: str, name: str) -> SecretMetadata:
        async with self.db.session(tenant_id) as session:
            record = (
                await session.execute(
                    select(SecretRecord).where(SecretRecord.tenant_id == tenant_id, SecretRecord.name == name)
                )
            ).scalar_one_or_none()
            if record is None:
                raise NotFound(f"secret {name!r} not found")
            return to_metadata(record)

    async def exists(self, tenant_id: str, names: builtins.list[str]) -> set[str]:
        """Subset of ``names`` that exist for the tenant (no decryption)."""
        if not names:
            return set()
        async with self.db.session(tenant_id) as session:
            stmt = select(SecretRecord.name).where(SecretRecord.tenant_id == tenant_id, SecretRecord.name.in_(names))
            return set((await session.execute(stmt)).scalars().all())

    async def list(self, tenant_id: str) -> builtins.list[SecretMetadata]:
        async with self.db.session(tenant_id) as session:
            stmt = select(SecretRecord).where(SecretRecord.tenant_id == tenant_id).order_by(SecretRecord.name)
            return [to_metadata(r) for r in (await session.execute(stmt)).scalars().all()]


async def rewrap_data_keys(db: Database, source: KeyProvider, target: KeyProvider, *, batch_size: int = 200) -> int:
    """Master-key rotation: re-wrap every data key (all tenants) under ``target``'s KEK.

    ``source`` must be able to unwrap the current rows (e.g. ``LocalKeyProvider(old)``
    or ``LocalKeyProvider(new, retired=[old])``). Ciphertexts and nonces are not
    touched, so values are unchanged. Rows already under ``target.key_id`` are
    skipped, which makes the operation resumable. Returns the number re-wrapped.

    Operational order: run this with old+new keys, then deploy the new
    PLATFORM_MASTER_KEY. Note the fingerprint key is derived from the master key,
    so fingerprints of secrets written after the switch use the new key.
    """
    count = 0
    while True:
        async with db.session() as session:  # unscoped on purpose: spans all tenants
            allow_cross_tenant(session)  # explicit RLS bypass on PostgreSQL
            stmt = (
                select(SecretRecord)
                .where(SecretRecord.kek_id != target.key_id)
                .order_by(SecretRecord.id)
                .limit(batch_size)
                .with_for_update()
            )
            rows = list((await session.execute(stmt)).scalars().all())
            if not rows:
                return count
            for record in rows:
                aad = build_aad(record.tenant_id, record.name, record.version)
                new = await rewrap(source, target, _envelope(record), aad)
                record.wrapped_key = new.wrapped_key
                record.kek_id = new.kek_id
            await session.commit()
            count += len(rows)
            log.info("re-wrapped %d secret data keys", len(rows))
