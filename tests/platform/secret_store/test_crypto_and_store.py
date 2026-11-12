"""Envelope encryption, AAD binding, tenant isolation, rotation and KEK rotation."""

from __future__ import annotations

import base64
import secrets

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.platform.config import PlatformSettings, generate_master_key
from app.platform.core_models import AuditEvent, Tenant
from app.platform.db import Database
from app.platform.errors import NotFound, ValidationFailed
from app.platform.interfaces import SecretStore
from app.platform.secrets.crypto import (
    Envelope,
    Fingerprinter,
    LocalKeyProvider,
    SecretDecryptionError,
    build_aad,
    open_envelope,
    seal,
)
from app.platform.secrets.models import SecretRecord
from app.platform.secrets.store import DbSecretStore, SecretUnreadable, rewrap_data_keys


def _key() -> bytes:
    return secrets.token_bytes(32)


async def test_seal_open_round_trip() -> None:
    provider = LocalKeyProvider(_key())
    aad = build_aad("t1", "crm_secret", 1)
    env = await seal(provider, "s3cr3t-välue", aad)
    assert b"s3cr3t" not in env.ciphertext
    assert env.kek_id == provider.key_id and len(env.nonce) == 12
    assert await open_envelope(provider, env, aad) == "s3cr3t-välue"


async def test_each_seal_uses_fresh_data_key_and_nonce() -> None:
    provider = LocalKeyProvider(_key())
    aad = build_aad("t1", "n", 1)
    a, b = await seal(provider, "same", aad), await seal(provider, "same", aad)
    assert a.ciphertext != b.ciphertext and a.wrapped_key != b.wrapped_key and a.nonce != b.nonce


@pytest.mark.parametrize("other_aad", [("t2", "n", 1), ("t1", "other", 1), ("t1", "n", 2)])
async def test_aad_mismatch_fails(other_aad: tuple[str, str, int]) -> None:
    provider = LocalKeyProvider(_key())
    env = await seal(provider, "value", build_aad("t1", "n", 1))
    with pytest.raises(SecretDecryptionError):
        await open_envelope(provider, env, build_aad(*other_aad))


async def test_tampered_ciphertext_or_wrapped_key_fails() -> None:
    provider = LocalKeyProvider(_key())
    aad = build_aad("t1", "n", 1)
    env = await seal(provider, "value", aad)
    flipped = bytes([env.ciphertext[0] ^ 1]) + env.ciphertext[1:]
    with pytest.raises(SecretDecryptionError):
        await open_envelope(provider, Envelope(flipped, env.nonce, env.wrapped_key, env.kek_id), aad)
    bad_wrap = env.wrapped_key[:-1] + bytes([env.wrapped_key[-1] ^ 1])
    with pytest.raises(SecretDecryptionError):
        await open_envelope(provider, Envelope(env.ciphertext, env.nonce, bad_wrap, env.kek_id), aad)


async def test_wrong_master_key_fails() -> None:
    aad = build_aad("t1", "n", 1)
    env = await seal(LocalKeyProvider(_key()), "value", aad)
    other = LocalKeyProvider(_key())
    with pytest.raises(SecretDecryptionError):
        await open_envelope(other, Envelope(env.ciphertext, env.nonce, env.wrapped_key, other.key_id), aad)
    with pytest.raises(SecretDecryptionError, match="not available"):
        await open_envelope(other, env, aad)


def test_provider_and_fingerprinter_never_show_key_material() -> None:
    key = _key()
    assert key.hex() not in repr(LocalKeyProvider(key))
    fp = Fingerprinter(key)
    assert fp("t1", "value") == fp("t1", "value")
    assert fp("t1", "value") != fp("t2", "value")  # no cross-tenant correlation
    assert len(fp("t1", "value")) == 12


async def test_store_conforms_and_round_trips(store: DbSecretStore, tenant: Tenant) -> None:
    assert isinstance(store, SecretStore)
    meta = await store.put(tenant.id, "crm_client_secret", "value-1", description="CRM OAuth secret")
    assert meta.version == 1 and len(meta.fingerprint) == 12
    assert await store.reveal(tenant.id, "crm_client_secret") == "value-1"
    assert [m.name for m in await store.list(tenant.id)] == ["crm_client_secret"]


async def test_rotation_bumps_version_and_keeps_latest_value(
    store: DbSecretStore, tenant: Tenant, db: Database
) -> None:
    first = await store.put(tenant.id, "api", "value-one")
    second = await store.put(tenant.id, "api", "value-two", description="rotated")
    assert second.version == 2 and second.fingerprint != first.fingerprint
    assert second.created_at == first.created_at
    assert await store.reveal(tenant.id, "api") == "value-two"
    async with db.session() as s:
        actions = [a.action for a in (await s.execute(select(AuditEvent).order_by(AuditEvent.created_at))).scalars()]
    assert actions == ["secret.created", "secret.rotated"]


async def test_cross_tenant_reads_and_moved_ciphertext_fail(
    store: DbSecretStore, tenant: Tenant, other_tenant: Tenant, db: Database
) -> None:
    await store.put(tenant.id, "shared_name", "tenant-a-value")
    with pytest.raises(NotFound):
        await store.reveal(other_tenant.id, "shared_name")
    assert await store.list(other_tenant.id) == []
    with pytest.raises(NotFound):
        await store.delete(other_tenant.id, "shared_name")
    # An attacker with DB write access copies tenant A's envelope onto tenant B's row.
    await store.put(other_tenant.id, "shared_name", "tenant-b-value")
    async with db.session() as s:
        rows = {r.tenant_id: r for r in (await s.execute(select(SecretRecord))).scalars()}
        a, b = rows[tenant.id], rows[other_tenant.id]
        b.ciphertext, b.nonce, b.wrapped_key, b.version = a.ciphertext, a.nonce, a.wrapped_key, a.version
        await s.commit()
    with pytest.raises(SecretUnreadable):
        await store.reveal(other_tenant.id, "shared_name")
    assert await store.reveal(tenant.id, "shared_name") == "tenant-a-value"


async def test_plaintext_never_stored_or_audited(store: DbSecretStore, tenant: Tenant, db: Database) -> None:
    value = "plaintext-marker-" + secrets.token_hex(8)
    meta = await store.put(tenant.id, "marker", value)
    await store.delete(tenant.id, "marker", actor_id="u1")
    await store.put(tenant.id, "marker", value)
    async with db.session() as s:
        for record in (await s.execute(select(SecretRecord))).scalars():
            for blob in (record.ciphertext, record.wrapped_key, record.nonce):
                assert value.encode() not in blob
        audits = list((await s.execute(select(AuditEvent))).scalars())
    assert audits and all(value not in str(a.detail) for a in audits)
    assert {a.detail["fingerprint"] for a in audits} == {meta.fingerprint}


async def test_validation(store: DbSecretStore, tenant: Tenant) -> None:
    with pytest.raises(ValidationFailed):
        await store.put(tenant.id, "bad name!", "value")
    with pytest.raises(ValidationFailed):
        await store.put(tenant.id, "ok", "")
    with pytest.raises(ValidationFailed) as err:
        await store.put(tenant.id, "ok", "x" * (64 * 1024 + 1))
    assert "xxxx" not in err.value.message


async def test_kek_rotation_rewraps_without_touching_ciphertext(
    db: Database, settings: PlatformSettings, tenant: Tenant, other_tenant: Tenant
) -> None:
    old_store = DbSecretStore(db, settings)
    await old_store.put(tenant.id, "a", "alpha-value")
    await old_store.put(other_tenant.id, "b", "bravo-value")
    async with db.session() as s:
        before = {
            r.name: (r.ciphertext, r.nonce, r.wrapped_key) for r in (await s.execute(select(SecretRecord))).scalars()
        }

    new_master = generate_master_key()
    old_provider = LocalKeyProvider(settings.master_key_bytes())
    new_provider = LocalKeyProvider(base64.urlsafe_b64decode(new_master), retired=[settings.master_key_bytes()])
    assert await rewrap_data_keys(db, old_provider, new_provider, batch_size=1) == 2
    assert await rewrap_data_keys(db, old_provider, new_provider) == 0  # resumable / idempotent

    async with db.session() as s:
        after = {r.name: r for r in (await s.execute(select(SecretRecord))).scalars()}
    for name, (ct, nonce, wrapped) in before.items():
        assert after[name].ciphertext == ct and after[name].nonce == nonce
        assert after[name].wrapped_key != wrapped and after[name].kek_id == new_provider.key_id

    new_settings = settings.model_copy(update={"master_key": SecretStr(new_master)})
    new_store = DbSecretStore(db, new_settings)
    assert await new_store.reveal(tenant.id, "a") == "alpha-value"
    assert await new_store.reveal(other_tenant.id, "b") == "bravo-value"
    with pytest.raises(SecretUnreadable):
        await DbSecretStore(db, settings).reveal(tenant.id, "a")  # old key alone no longer works
