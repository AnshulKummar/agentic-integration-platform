"""Envelope encryption for tenant secrets (AES-256-GCM).

Layout of one secret version::

    data_key   = 32 random bytes (fresh per version, never stored in clear)
    ciphertext = AES-256-GCM(data_key, nonce, value, aad)
    wrapped    = KeyProvider.wrap(data_key, aad)          # KEK never leaves the provider
    aad        = f"{tenant_id}|{name}|{version}"

Binding the AAD to tenant, name and version means a ciphertext (or wrapped key)
copied onto another tenant's row, another name, or another version fails
authentication instead of decrypting.

Key-encryption keys come from a ``KeyProvider``. ``LocalKeyProvider`` derives
its KEK from ``PLATFORM_MASTER_KEY`` (HKDF, purpose-separated). A cloud KMS
provider (AWS KMS ``Encrypt``/``Decrypt`` with ``EncryptionContext=aad``, GCP
Cloud KMS with ``additional_authenticated_data``, Azure Key Vault ``wrapKey``)
implements the same three members; select it in ``provider_from_settings``.
None is shipped here on purpose: an untested fake would be worse than the seam.

Master-key rotation re-wraps every data key under the new KEK (see
``rewrap_data_keys`` in ``store.py``); ciphertexts are untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.platform.config import ConfigError, PlatformSettings

KEY_BYTES = 32
NONCE_BYTES = 12

_KEK_INFO = b"pmaip/secrets/kek/v1"
_FINGERPRINT_INFO = b"pmaip/secrets/fingerprint/v1"


class SecretDecryptionError(Exception):
    """Authentication failed: wrong key, tampered data or mismatched AAD.

    The message never contains key material or plaintext.
    """


def build_aad(tenant_id: str, name: str, version: int) -> bytes:
    if "|" in tenant_id or "|" in name:
        # Names are validated upstream; this keeps the AAD encoding unambiguous.
        raise ValueError("tenant_id and name must not contain '|'")
    return f"{tenant_id}|{name}|{version}".encode()


def _hkdf(master: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=info).derive(master)


# ---------------------------------------------------------------- key providers
@runtime_checkable
class KeyProvider(Protocol):
    """Wraps/unwraps data keys with a key-encryption key (KEK).

    ``key_id`` identifies the KEK currently used for ``wrap``; it is stored on
    every record so ``unwrap`` (and rotation) know which KEK wrapped it.
    Implementations must authenticate ``aad`` (GCM AAD / KMS encryption context).
    """

    key_id: str

    async def wrap(self, data_key: bytes, aad: bytes) -> bytes: ...

    async def unwrap(self, wrapped: bytes, aad: bytes, key_id: str) -> bytes: ...


def local_key_id(master_key: bytes) -> str:
    """Stable, non-reversible id of a local master key ("local:" + 16 hex)."""
    digest = hmac.new(_hkdf(master_key, b"pmaip/secrets/key-id/v1"), b"key-id", hashlib.sha256).hexdigest()
    return f"local:{digest[:16]}"


class LocalKeyProvider:
    """KEK derived from the platform master key; wrapped key = nonce || AES-GCM(kek, data_key).

    ``retired`` master keys can still unwrap (needed while rotating), never wrap.
    """

    def __init__(self, master_key: bytes, *, retired: Sequence[bytes] = ()) -> None:
        if len(master_key) != KEY_BYTES:
            raise ConfigError("master key must be 32 bytes")
        self.key_id = local_key_id(master_key)
        self._keks: dict[str, bytes] = {self.key_id: _hkdf(master_key, _KEK_INFO)}
        for old in retired:
            if len(old) != KEY_BYTES:
                raise ConfigError("retired master key must be 32 bytes")
            self._keks.setdefault(local_key_id(old), _hkdf(old, _KEK_INFO))

    def __repr__(self) -> str:  # never show key material
        return f"LocalKeyProvider(key_id={self.key_id!r}, known={len(self._keks)})"

    async def wrap(self, data_key: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(self._keks[self.key_id]).encrypt(nonce, data_key, aad)

    async def unwrap(self, wrapped: bytes, aad: bytes, key_id: str) -> bytes:
        kek = self._keks.get(key_id)
        if kek is None:
            raise SecretDecryptionError(f"key-encryption key {key_id!r} is not available")
        if len(wrapped) <= NONCE_BYTES:
            raise SecretDecryptionError("wrapped data key is truncated")
        try:
            return AESGCM(kek).decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], aad)
        except InvalidTag as exc:
            raise SecretDecryptionError("data key failed authentication") from exc


def provider_from_settings(settings: PlatformSettings) -> KeyProvider:
    """The configured KEK provider. Add cloud KMS providers here (settings.kms_provider)."""
    if settings.kms_provider == "local":
        return LocalKeyProvider(settings.master_key_bytes())
    raise ConfigError(f"unsupported kms_provider {settings.kms_provider!r}")  # pragma: no cover


# ---------------------------------------------------------------- envelopes
@dataclass(frozen=True)
class Envelope:
    ciphertext: bytes
    nonce: bytes
    wrapped_key: bytes
    kek_id: str

    def __repr__(self) -> str:
        return f"Envelope(kek_id={self.kek_id!r}, ciphertext=<{len(self.ciphertext)} bytes>)"


async def seal(provider: KeyProvider, plaintext: str, aad: bytes) -> Envelope:
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    wrapped = await provider.wrap(data_key, aad)
    return Envelope(ciphertext=ciphertext, nonce=nonce, wrapped_key=wrapped, kek_id=provider.key_id)


async def open_envelope(provider: KeyProvider, envelope: Envelope, aad: bytes) -> str:
    data_key = await provider.unwrap(envelope.wrapped_key, aad, envelope.kek_id)
    try:
        plaintext = AESGCM(data_key).decrypt(envelope.nonce, envelope.ciphertext, aad)
    except InvalidTag as exc:
        raise SecretDecryptionError("secret ciphertext failed authentication") from exc
    return plaintext.decode("utf-8")


async def rewrap(source: KeyProvider, target: KeyProvider, envelope: Envelope, aad: bytes) -> Envelope:
    """Re-wrap the data key under ``target``'s KEK. The ciphertext is not touched."""
    data_key = await source.unwrap(envelope.wrapped_key, aad, envelope.kek_id)
    wrapped = await target.wrap(data_key, aad)
    return Envelope(ciphertext=envelope.ciphertext, nonce=envelope.nonce, wrapped_key=wrapped, kek_id=target.key_id)


# ---------------------------------------------------------------- fingerprints
class Fingerprinter:
    """First 12 hex of HMAC-SHA256 under a key derived from the master key.

    The tenant id is mixed in so equal values in two tenants do not correlate.
    """

    def __init__(self, master_key: bytes) -> None:
        self._key = _hkdf(master_key, _FINGERPRINT_INFO)

    def __repr__(self) -> str:
        return "Fingerprinter()"

    def __call__(self, tenant_id: str, value: str) -> str:
        msg = tenant_id.encode() + b"\x00" + value.encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:12]
