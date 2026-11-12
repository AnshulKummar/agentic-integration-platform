"""Collaborators the deployments area needs, injected rather than imported.

``DeploymentDeps`` is stored on ``app.state.deployment_deps`` (API) and passed
to the ``Worker``. The runner and connector factory come from the sandbox and
connectors areas; this area depends only on the protocols in interfaces.py.

Merge wiring (app factory / worker), e.g.::

    from app.platform.deployments import deps
    deps.register_provider(lambda settings, db: deps.DeploymentDeps(runner=..., connector_factory=...))
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from app.platform.config import PlatformSettings
from app.platform.core_models import Integration, IntegrationVersion
from app.platform.db import Database
from app.platform.deployments.clock import Clock, SystemClock
from app.platform.deployments.hooks import DeploymentHooks, hooks
from app.platform.errors import PlatformError
from app.platform.interfaces import Connector, IntegrationRunner

#: async (tenant_id, system_id) -> Connector. The caller owns the returned
#: connector and calls ``aclose()`` when done.
ConnectorFactory = Callable[[str, str], Awaitable[Connector]]
#: (integration, version, count) -> synthetic events for sandbox/staging checks.
SyntheticEventFactory = Callable[[Integration, IntegrationVersion, int], list[dict[str, Any]]]


class ServiceUnavailable(PlatformError):
    status = 503
    code = "service_unavailable"


def default_synthetic_events(integration: Integration, version: IntegrationVersion, count: int) -> list[dict[str, Any]]:
    """Generic synthetic events. Marked ``synthetic: True`` so generated code and
    connectors can avoid real side effects; ids are stable per version."""
    events: list[dict[str, Any]] = []
    for i in range(count):
        n = i + 1
        events.append(
            {
                "id": f"synthetic-{version.id}-{n}",
                "synthetic": True,
                "trigger": integration.trigger,
                "source_system": integration.source_system,
                "target_system": integration.target_system,
                # Shapes used by the demo pipeline's closed-won / signed-agreement triggers.
                "opportunity_id": f"opp-{n:03d}",
                "agreement_id": f"agr-{n:03d}",
                "event": "agreement.completed",
            }
        )
    return events


def _default_hooks() -> DeploymentHooks:
    return hooks


@dataclass
class DeploymentDeps:
    runner: IntegrationRunner
    connector_factory: ConnectorFactory
    clock: Clock = field(default_factory=SystemClock)
    hooks: DeploymentHooks = field(default_factory=_default_hooks)
    synthetic_events: SyntheticEventFactory = default_synthetic_events
    #: Synthetic runs per stage (sandbox, staging).
    synthetic_runs: int = 3
    #: Persist an ExecutionRecord (+ EXECUTIONS metric) for each run dispatched
    #: here. Set False if the IntegrationRunner already persists them, to avoid
    #: double counting (SLO queries do not depend on who wrote the row).
    record_executions: bool = True


DepsProvider = Callable[[PlatformSettings, Database], DeploymentDeps]
_provider: DepsProvider | None = None


def register_provider(provider: DepsProvider | None) -> None:
    """Register how to build DeploymentDeps when none is on app.state (worker, API)."""
    global _provider
    _provider = provider


def build_deps(settings: PlatformSettings, db: Database) -> DeploymentDeps:
    if _provider is None:
        raise ServiceUnavailable(
            "deployment runtime is not wired (no IntegrationRunner/connector factory registered)",
            code="deployments_unavailable",
        )
    return _provider(settings, db)


def get_deployment_deps(request: Request) -> DeploymentDeps:
    deps: DeploymentDeps | None = getattr(request.app.state, "deployment_deps", None)
    if deps is None:
        deps = build_deps(request.app.state.settings, request.app.state.db)
        request.app.state.deployment_deps = deps
    return deps
