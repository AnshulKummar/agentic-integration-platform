"""Cross-area contracts. Areas implement these; other areas depend only on them.

    secrets area     -> SecretStore
    connectors area  -> Connector, ConnectorConfig, AuthConfig
    sandbox area     -> Sandbox, ToolBroker, IntegrationRunner, generated-code contract
    deployments area -> consumes IntegrationRunner + Connector.health()

Generated-code contract (sandbox area renders it, sandbox runs it):
    The module defines ``def run(event: dict, tools) -> dict``.
    ``tools.call(name: str, args: dict) -> Any`` is the ONLY way out of the
    sandbox. It is synchronous from the code's point of view; the host-side
    ToolBroker executes the call through a Connector with the tenant's secrets.
    The sandbox itself holds no credentials and has no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

# --- secrets -------------------------------------------------------------------


@dataclass(frozen=True)
class SecretMetadata:
    name: str
    #: Short, non-reversible fingerprint (e.g. first 12 hex of HMAC-SHA256) for UIs/audit.
    fingerprint: str
    version: int
    description: str
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class SecretStore(Protocol):
    async def put(self, tenant_id: str, name: str, value: str, *, description: str = "") -> SecretMetadata:
        """Create or rotate (bumps version). Never logs ``value``."""
        ...

    async def reveal(self, tenant_id: str, name: str) -> str:
        """Internal only (connectors at call time). Never exposed over the API."""
        ...

    async def list(self, tenant_id: str) -> list[SecretMetadata]: ...

    async def delete(self, tenant_id: str, name: str) -> None: ...


# --- connectors ------------------------------------------------------------------

AuthKind = Literal["none", "api_key", "bearer", "oauth2_client_credentials"]


@dataclass(frozen=True)
class AuthConfig:
    kind: AuthKind = "none"
    #: Logical name -> secret name in the SecretStore, e.g.
    #: {"client_id": "esign_client_id", "client_secret": "esign_client_secret"} or {"api_key": "crm_key"}.
    secrets: dict[str, str] = field(default_factory=dict)
    token_url: str | None = None
    scopes: tuple[str, ...] = ()
    #: For api_key: header carrying the key.
    header_name: str = "X-API-Key"


@dataclass(frozen=True)
class ConnectorConfig:
    system_id: str
    base_url: str
    #: OpenAPI 3 document; tools are generated from it (app.demo.mcp_tools).
    spec: dict[str, Any]
    auth: AuthConfig = field(default_factory=AuthConfig)
    #: Prefix for generated tool names, e.g. "crm" -> "crm_get_opportunity".
    tool_prefix: str = ""
    #: Path probed by health(); defaults to "/health".
    health_path: str = "/health"


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    latency_ms: float
    detail: str = ""


@runtime_checkable
class Connector(Protocol):
    system_id: str

    def tools(self) -> list[str]: ...

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        """Execute one tool against the live API (auth, retries, rate limits handled)."""
        ...

    async def health(self) -> HealthStatus: ...

    async def aclose(self) -> None: ...


# --- execution ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]
    ok: bool
    duration_ms: float
    error: str | None = None


@runtime_checkable
class ToolBroker(Protocol):
    """Host-side gate between sandboxed code and connectors."""

    allowed: frozenset[str]

    async def call(self, tool: str, args: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class SandboxLimits:
    timeout_s: float = 10.0
    memory_mb: int = 256
    max_tool_calls: int = 50
    max_output_bytes: int = 1_000_000


@dataclass
class SandboxResult:
    ok: bool
    output: dict[str, Any] | None
    error: str | None
    tool_calls: list[ToolCall]
    logs: list[str]
    duration_ms: float


@runtime_checkable
class Sandbox(Protocol):
    backend: str

    async def run(self, code: str, event: dict[str, Any], broker: ToolBroker, limits: SandboxLimits) -> SandboxResult: ...


@runtime_checkable
class IntegrationRunner(Protocol):
    """Runs one stored integration version on one event, end to end."""

    async def run(self, tenant_id: str, version_id: str, event: dict[str, Any], *, stage: str) -> SandboxResult: ...
