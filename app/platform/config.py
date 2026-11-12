"""Platform settings, loaded from ``PLATFORM_*`` environment variables.

Secrets (JWT signing key, master encryption key, LLM key) come only from the
environment. In development they may be absent: an ephemeral value is generated
with a loud warning. In production, missing or unsafe values refuse to start.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

log = logging.getLogger(__name__)

Env = Literal["development", "test", "production"]


class ConfigError(RuntimeError):
    """Settings are missing or unsafe for the selected environment."""


def generate_master_key() -> str:
    """A fresh 256-bit key, urlsafe-base64 encoded (the PLATFORM_MASTER_KEY format)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


class PlatformSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    env: Env = "development"
    database_url: str = "sqlite+aiosqlite:///./platform.db"
    #: Create tables on startup (dev/test). Production uses Alembic migrations.
    auto_create_schema: bool = True
    cors_origins: tuple[str, ...] = ()

    # --- identity -----------------------------------------------------------
    jwt_secret: SecretStr | None = None
    jwt_issuer: str = "pmaip-platform"
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 14 * 24 * 3600
    login_rate_limit_per_minute: int = 10

    # --- secrets ------------------------------------------------------------
    #: urlsafe-base64 32-byte key encrypting per-secret data keys (envelope encryption).
    master_key: SecretStr | None = None
    kms_provider: Literal["local"] = "local"

    # --- execution ----------------------------------------------------------
    sandbox_backend: Literal["subprocess", "docker"] = "subprocess"
    sandbox_timeout_s: float = 10.0
    sandbox_memory_mb: int = 256
    sandbox_max_tool_calls: int = 50
    sandbox_image: str = "python:3.12-slim"
    connector_timeout_s: float = 15.0
    connector_max_retries: int = 3

    # --- deployments / health -----------------------------------------------
    canary_percent: int = 10
    canary_min_events: int = 20
    canary_max_error_rate: float = 0.05
    canary_max_p95_ms: float = 2000.0
    post_deploy_watch_s: int = 900
    health_check_interval_s: int = 30

    # --- LLM / evals ----------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    eval_cost_cap_usd: float = 5.0

    # --- observability --------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    metrics_enabled: bool = True
    otel_endpoint: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> PlatformSettings:
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for name, field in cls.model_fields.items():
            raw = env.get(f"PLATFORM_{name.upper()}")
            if raw is None:
                continue
            if field.annotation in (tuple[str, ...],):
                values[name] = tuple(x.strip() for x in raw.split(",") if x.strip())
            else:
                values[name] = raw
        if "anthropic_api_key" not in values and env.get("ANTHROPIC_API_KEY"):
            values["anthropic_api_key"] = env["ANTHROPIC_API_KEY"]
        return cls.model_validate(values)

    # ------------------------------------------------------------------------
    def production_problems(self) -> list[str]:
        problems: list[str] = []
        if self.jwt_secret is None or len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("PLATFORM_JWT_SECRET must be set (>= 32 chars)")
        if self.master_key is None:
            problems.append("PLATFORM_MASTER_KEY must be set")
        else:
            try:
                if len(base64.urlsafe_b64decode(self.master_key.get_secret_value())) != 32:
                    problems.append("PLATFORM_MASTER_KEY must decode to 32 bytes")
            except ValueError:
                problems.append("PLATFORM_MASTER_KEY is not valid urlsafe base64")
        if self.sandbox_backend != "docker":
            problems.append("PLATFORM_SANDBOX_BACKEND must be 'docker' (subprocess is not a security boundary)")
        if not self.database_url.startswith("postgresql"):
            problems.append("PLATFORM_DATABASE_URL must be PostgreSQL")
        if self.auto_create_schema:
            problems.append("PLATFORM_AUTO_CREATE_SCHEMA must be false (use Alembic migrations)")
        return problems

    def validate_runtime(self) -> PlatformSettings:
        """Refuse unsafe production config; fill ephemeral dev secrets otherwise."""
        if self.env == "production":
            problems = self.production_problems()
            if problems:
                raise ConfigError("Unsafe production configuration:\n- " + "\n- ".join(problems))
            return self
        updates: dict[str, object] = {}
        if self.jwt_secret is None:
            log.warning("PLATFORM_JWT_SECRET unset: using an ephemeral key (sessions die on restart)")
            updates["jwt_secret"] = SecretStr(secrets.token_urlsafe(48))
        if self.master_key is None:
            log.warning("PLATFORM_MASTER_KEY unset: using an ephemeral key (stored secrets unreadable after restart)")
            updates["master_key"] = SecretStr(generate_master_key())
        return self.model_copy(update=updates) if updates else self

    def jwt_key(self) -> str:
        if self.jwt_secret is None:
            raise ConfigError("jwt_secret missing; call validate_runtime() first")
        return self.jwt_secret.get_secret_value()

    def master_key_bytes(self) -> bytes:
        if self.master_key is None:
            raise ConfigError("master_key missing; call validate_runtime() first")
        return base64.urlsafe_b64decode(self.master_key.get_secret_value())
