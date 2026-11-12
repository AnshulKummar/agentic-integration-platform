"""Fixtures for deployment tests: fake runner/connectors with controllable
failure rate and latency, a manual clock, seeded integrations and versions."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.platform.config import PlatformSettings, generate_master_key
from app.platform.core_models import Approval, Integration, IntegrationVersion, Tenant, VersionStatus
from app.platform.db import Database
from app.platform.deployments.clock import ManualClock
from app.platform.deployments.deps import DeploymentDeps
from app.platform.deployments.hooks import DeploymentHooks
from app.platform.deployments.models import Deployment
from app.platform.deployments.routing import dispatch_event
from app.platform.deployments.service import DeploymentService
from app.platform.errors import Conflict
from app.platform.interfaces import HealthStatus, SandboxResult
from app.platform.worker import Worker


def _bucket(text: str) -> float:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") % 10_000 / 10_000


@dataclass
class FakeRunner:
    """Per-version behaviour. Failures are deterministic in the event id."""

    error_rate: dict[str, float] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    #: Versions whose synthetic runs fail.
    synthetic_fails: set[str] = field(default_factory=set)
    #: Versions for which the runner raises (infrastructure failure).
    raises: set[str] = field(default_factory=set)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def run(self, tenant_id: str, version_id: str, event: dict[str, Any], *, stage: str) -> SandboxResult:
        event_id = str(event.get("id"))
        self.calls.append((version_id, stage, event_id))
        if version_id in self.raises:
            raise RuntimeError("sandbox backend unavailable")
        latency = self.latency_ms.get(version_id, 50.0)
        if stage == "synthetic":
            fail = version_id in self.synthetic_fails
        else:
            fail = _bucket(f"{version_id}:{event_id}") < self.error_rate.get(version_id, 0.0)
        return SandboxResult(
            ok=not fail,
            output=None if fail else {"handled": event_id},
            error="boom: target rejected payload" if fail else None,
            tool_calls=[],
            logs=[],
            duration_ms=latency,
        )


@dataclass
class FakeConnector:
    system_id: str
    healthy: bool = True
    latency_ms: float = 20.0
    closed: bool = False

    def tools(self) -> list[str]:
        return []

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        raise NotImplementedError

    async def health(self) -> HealthStatus:
        detail = "ok" if self.healthy else "503 from /health"
        return HealthStatus(ok=self.healthy, latency_ms=self.latency_ms, detail=detail)

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FakeConnectors:
    unhealthy: set[str] = field(default_factory=set)
    opened: list[FakeConnector] = field(default_factory=list)

    async def __call__(self, tenant_id: str, system_id: str) -> FakeConnector:
        conn = FakeConnector(system_id=system_id, healthy=system_id not in self.unhealthy)
        self.opened.append(conn)
        return conn


@pytest.fixture
def settings(database_url: str) -> PlatformSettings:
    # Overrides the root fixture: a bigger canary share and smaller sample keep tests fast.
    return PlatformSettings(
        env="test",
        database_url=database_url,
        jwt_secret="test-jwt-secret-" + "x" * 32,  # type: ignore[arg-type]
        master_key=generate_master_key(),  # type: ignore[arg-type]
        sandbox_backend="subprocess",
        log_json=False,
        canary_percent=50,
        canary_min_events=10,
        canary_max_error_rate=0.05,
        canary_max_p95_ms=2000.0,
        post_deploy_watch_s=300,
        health_check_interval_s=30,
    )


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def connectors() -> FakeConnectors:
    return FakeConnectors()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime.now(UTC))


@pytest.fixture
def hooks() -> DeploymentHooks:
    return DeploymentHooks()


@pytest.fixture
def deps(runner: FakeRunner, connectors: FakeConnectors, clock: ManualClock, hooks: DeploymentHooks) -> DeploymentDeps:
    return DeploymentDeps(runner=runner, connector_factory=connectors, clock=clock, hooks=hooks)


@pytest.fixture
def service(db: Database, settings: PlatformSettings, deps: DeploymentDeps) -> DeploymentService:
    return DeploymentService(db, settings, deps)


@pytest.fixture
def worker(db: Database, settings: PlatformSettings, deps: DeploymentDeps) -> Worker:
    return Worker(db, settings, deps, worker_id="test-worker")


@pytest.fixture
async def api(client: httpx.AsyncClient, app: FastAPI, deps: DeploymentDeps) -> AsyncIterator[httpx.AsyncClient]:
    app.state.deployment_deps = deps
    yield client


@pytest.fixture
def make_integration(db: Database) -> Callable[..., Awaitable[Integration]]:
    async def _make(tenant: Tenant, name: str = "crm-to-billing", active_version_id: str | None = None) -> Integration:
        async with db.session(tenant.id) as s:
            integ = Integration(
                tenant_id=tenant.id, name=name, source_system="crm", target_system="billing",
                trigger="opportunity.closed_won", active_version_id=active_version_id,
            )
            s.add(integ)
            await s.commit()
        return integ

    return _make


@pytest.fixture
def make_version(db: Database) -> Callable[..., Awaitable[IntegrationVersion]]:
    counters: dict[str, int] = {}

    async def _make(
        integration: Integration,
        *,
        status: str = VersionStatus.APPROVED,
        approvals: tuple[str, ...] = ("pm", "eng"),
        make_active: bool = False,
    ) -> IntegrationVersion:
        counters[integration.id] = counters.get(integration.id, 0) + 1
        async with db.session(integration.tenant_id) as s:
            version = IntegrationVersion(
                tenant_id=integration.tenant_id, integration_id=integration.id,
                number=counters[integration.id], status=status, code="def run(event, tools): return {}",
            )
            s.add(version)
            await s.flush()
            for role in approvals:
                s.add(
                    Approval(tenant_id=integration.tenant_id, version_id=version.id, role=role, approver_id=f"u-{role}")
                )
            if make_active:
                integ = (await s.execute(select(Integration).where(Integration.id == integration.id))).scalar_one()
                integ.active_version_id = version.id
                version.status = VersionStatus.LIVE
            await s.commit()
        return version

    return _make


@pytest.fixture
def send_events(db: Database, deps: DeploymentDeps) -> Callable[..., Awaitable[dict[str, int]]]:
    """send_events(integration, n, prefix) -> {stage: count}; 'rejected' counts 409s (no live version)."""

    async def _send(integration: Integration, n: int, prefix: str = "evt") -> dict[str, int]:
        counts: dict[str, int] = {}
        for i in range(n):
            try:
                res = await dispatch_event(
                    db, deps, tenant_id=integration.tenant_id, integration_id=integration.id,
                    event={"id": f"{prefix}-{i}", "amount": i},
                )
                counts[res.stage] = counts.get(res.stage, 0) + 1
            except Conflict:
                counts["rejected"] = counts.get("rejected", 0) + 1
        return counts

    return _send


@pytest.fixture
def load(db: Database) -> Callable[..., Awaitable[tuple[Deployment, Integration, dict[str, str]]]]:
    """load(tenant_id, deployment_id) -> (deployment, integration, {version_id: status})"""

    async def _load(tenant_id: str, deployment_id: str) -> tuple[Deployment, Integration, dict[str, str]]:
        async with db.session(tenant_id) as s:
            dep = (await s.execute(select(Deployment).where(Deployment.id == deployment_id))).scalar_one()
            integ = (await s.execute(select(Integration).where(Integration.id == dep.integration_id))).scalar_one()
            versions = (
                await s.execute(select(IntegrationVersion).where(IntegrationVersion.integration_id == integ.id))
            ).scalars()
            return dep, integ, {v.id: v.status for v in versions}

    return _load
