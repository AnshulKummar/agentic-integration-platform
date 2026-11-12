"""DefaultIntegrationRunner: run one stored integration version on one event, end to end.

1. Load the tenant's ``IntegrationVersion`` through a tenant-bound session
   (another tenant's id -> ``NotFound``, indistinguishable from a missing id).
2. Refuse unapproved code on live traffic: stages ``canary``/``production``
   need status approved/deploying/live (``Conflict`` otherwise).
3. Validate ``version.code``; build one connector per system its
   ``allowed_tools`` route to; run it in the sandbox through a
   :class:`DefaultToolBroker` limited to ``allowed_tools`` and the settings' budget.
4. Always close the connectors, write an ``ExecutionRecord`` and record
   ``EXECUTIONS`` / ``EXECUTION_LATENCY`` (the sandbox records ``SANDBOX_RUNS``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.platform.config import PlatformSettings
from app.platform.core_models import ExecutionRecord, IntegrationVersion, VersionStatus
from app.platform.db import Database
from app.platform.errors import Conflict, PlatformError, ValidationFailed
from app.platform.interfaces import Connector, Sandbox, SandboxLimits, SandboxResult
from app.platform.observability.metrics import EXECUTION_LATENCY, EXECUTIONS
from app.platform.sandbox.broker import DefaultToolBroker
from app.platform.sandbox.validate import CodeRejected, validate_code
from app.platform.tenancy import TenantRepository

log = logging.getLogger(__name__)

ConnectorFactory = Callable[[str, str], Awaitable[Connector]]
ToolRouting = Callable[[str], str]

STAGES = ("sandbox", "staging", "canary", "production", "synthetic")
LIVE_STAGES = ("canary", "production")
RUNNABLE_ON_LIVE = (VersionStatus.APPROVED, VersionStatus.DEPLOYING, VersionStatus.LIVE)
MAX_ERROR_CHARS = 2000


def default_tool_routing(tool: str) -> str:
    """``crm.get_account`` / ``crm_get_account`` -> ``crm`` (the connector's system_id)."""
    for sep in (".", "_"):
        head, found, _ = tool.partition(sep)
        if found and head:
            return head
    raise ValidationFailed(f"cannot route tool {tool!r} to a system", code="unroutable_tool")


def event_id_of(event: dict[str, Any]) -> str:
    for key in ("event_id", "id", "opportunity_id", "agreement_id"):
        value = event.get(key)
        if isinstance(value, str | int) and str(value):
            return str(value)[:100]
    return ""


class DefaultIntegrationRunner:
    def __init__(
        self,
        db: Database,
        sandbox: Sandbox,
        settings: PlatformSettings,
        connector_factory: ConnectorFactory,
        tool_routing: ToolRouting = default_tool_routing,
    ) -> None:
        self.db = db
        self.sandbox = sandbox
        self.settings = settings
        self.connector_factory = connector_factory
        self.tool_routing = tool_routing

    def limits(self) -> SandboxLimits:
        return SandboxLimits(
            timeout_s=self.settings.sandbox_timeout_s,
            memory_mb=self.settings.sandbox_memory_mb,
            max_tool_calls=self.settings.sandbox_max_tool_calls,
        )

    async def run(
        self,
        tenant_id: str,
        version_id: str,
        event: dict[str, Any],
        *,
        stage: str,
        deployment_id: str | None = None,
    ) -> SandboxResult:
        if stage not in STAGES:
            raise ValidationFailed(f"unknown stage {stage!r}; expected one of {list(STAGES)}")
        async with self.db.session(tenant_id) as session:
            version = await TenantRepository(session, tenant_id, IntegrationVersion).require(version_id)
            integration_id, code = version.integration_id, version.code
            allowed, status = list(version.allowed_tools), version.status
        if stage in LIVE_STAGES and status not in RUNNABLE_ON_LIVE:
            raise Conflict(f"version {version_id} is {status}; only approved versions run on {stage} traffic")

        started = time.perf_counter()
        try:
            result = await self._execute(tenant_id, code, allowed, event)
        except Exception as exc:
            # Unexpected failure: still leave an audit trail of the attempt, then re-raise.
            failed = SandboxResult(
                ok=False, output=None, error=f"internal error: {type(exc).__name__}", tool_calls=[], logs=[],
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            await self._record(tenant_id, integration_id, version_id, deployment_id, stage, event, failed)
            raise
        await self._record(tenant_id, integration_id, version_id, deployment_id, stage, event, result)
        return result

    async def _execute(self, tenant_id: str, code: str, allowed: list[str], event: dict[str, Any]) -> SandboxResult:
        started = time.perf_counter()

        def failed(message: str) -> SandboxResult:
            duration = (time.perf_counter() - started) * 1000.0
            return SandboxResult(ok=False, output=None, error=message, tool_calls=[], logs=[], duration_ms=duration)

        try:
            validate_code(code)
        except CodeRejected as exc:
            return failed(f"validation failed: {exc.message}")

        connectors: dict[str, Connector] = {}
        try:
            try:
                by_system: dict[str, Connector] = {}
                for tool in allowed:
                    system_id = self.tool_routing(tool)
                    if system_id not in by_system:
                        by_system[system_id] = await self.connector_factory(tenant_id, system_id)
                    connectors[tool] = by_system[system_id]
            except PlatformError as exc:
                log.warning("runner.connector_setup_failed", extra={"tenant": tenant_id, "code": exc.code})
                return failed(f"connector setup failed ({exc.code}): {exc.message}")
            broker = DefaultToolBroker(connectors, frozenset(allowed), self.settings.sandbox_max_tool_calls)
            return await self.sandbox.run(code, event, broker, self.limits())
        finally:
            await _close_all(set(connectors.values()))

    async def _record(
        self,
        tenant_id: str,
        integration_id: str,
        version_id: str,
        deployment_id: str | None,
        stage: str,
        event: dict[str, Any],
        result: SandboxResult,
    ) -> None:
        async with self.db.session(tenant_id) as session:
            session.add(
                ExecutionRecord(
                    tenant_id=tenant_id,
                    integration_id=integration_id,
                    version_id=version_id,
                    deployment_id=deployment_id,
                    stage=stage,
                    event_id=event_id_of(event),
                    ok=result.ok,
                    error=result.error[:MAX_ERROR_CHARS] if result.error else None,
                    duration_ms=result.duration_ms,
                    tool_calls=len(result.tool_calls),
                )
            )
            await session.commit()
        outcome = "ok" if result.ok else "error"
        EXECUTIONS.labels(tenant=tenant_id, integration=integration_id, stage=stage, outcome=outcome).inc()
        EXECUTION_LATENCY.labels(stage=stage).observe(result.duration_ms / 1000.0)


async def _close_all(connectors: set[Connector]) -> None:
    errors: list[BaseException] = []
    for connector in connectors:
        try:
            await connector.aclose()
        except Exception as exc:
            log.exception("runner.connector_close_failed", extra={"system": getattr(connector, "system_id", "?")})
            errors.append(exc)
    if errors:
        raise errors[0]
