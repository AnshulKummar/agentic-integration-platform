"""Tables for the secrets area. OWNED BY: secrets/connectors area (B).

One row per (tenant, name) holding the CURRENT version only; a rotation
(``put`` on an existing name) re-encrypts under a fresh data key and bumps
``version``. Plaintext is never stored.
"""

from __future__ import annotations

from sqlalchemy import Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, TenantScoped, TimestampMixin, new_id


class SecretRecord(TenantScoped, TimestampMixin, Base):
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_secrets_tenant_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: AES-256-GCM(data_key, nonce, value, aad=tenant|name|version), tag appended.
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: 96-bit GCM nonce of the value encryption.
    nonce: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    #: Data key wrapped by the KEK (provider-specific blob; local = nonce || AES-GCM(kek, dk)).
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_id: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(12), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"SecretRecord(name={self.name!r}, version={self.version}, fingerprint={self.fingerprint!r})"
