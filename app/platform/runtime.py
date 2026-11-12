"""Composition root: wires the areas together for the API and the worker.

    connections (secrets + HTTP connectors)  ->  sandbox runner  ->  deployments
    deployment goes live                      ->  KG learning (integrations area)

Tool names route to connections by prefix: ``crm.get_account`` runs through the
tenant's connection whose system_id is ``crm`` (PUT /v1/connections/crm).
"""

from __future__ import annotations

import logging

from app.platform.config import PlatformSettings
from app.platform.connectors.registry import build_connector
from app.platform.db import Database
from app.platform.deployments.deps import DeploymentDeps, register_provider
from app.platform.deployments.hooks import DeploymentEvent, DeploymentHooks
from app.platform.integrations.service import learn_from_version
from app.platform.interfaces import Connector
from app.platform.sandbox import build_sandbox
from app.platform.sandbox.runner import DefaultIntegrationRunner
from app.platform.secrets.store import DbSecretStore

log = logging.getLogger(__name__)


KG_SYSTEM_PREFIX = "sys-"


def connection_id(system_id: str) -> str:
    """KG system ids (``sys-crm``, used on integrations) -> connection ids (``crm``, used by tools)."""
    return system_id.removeprefix(KG_SYSTEM_PREFIX)


def learning_hooks(db: Database) -> DeploymentHooks:
    """Hooks bound to ``db``: a version going live teaches the tenant's KG."""
    hooks = DeploymentHooks()

    async def learn_on_live(event: DeploymentEvent) -> None:
        async with db.session(event.tenant_id) as session:
            outcome = await learn_from_version(session, event.tenant_id, event.version_id)
            await session.commit()
        log.info(
            "kg learned from live version",
            extra={"version_id": event.version_id, "deployment_id": event.deployment_id, "outcome": repr(outcome)},
        )

    hooks.on_live(learn_on_live)
    return hooks


def build_deployment_deps(settings: PlatformSettings, db: Database) -> DeploymentDeps:
    secret_store = DbSecretStore(db, settings)

    async def connector_factory(tenant_id: str, system_id: str) -> Connector:
        return await build_connector(db, secret_store, settings, tenant_id, connection_id(system_id))

    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, connector_factory)
    # The runner persists ExecutionRecords itself; don't double count.
    return DeploymentDeps(
        runner=runner,
        connector_factory=connector_factory,
        hooks=learning_hooks(db),
        record_executions=False,
    )


def install() -> None:
    """Register the provider used by the API (lazily) and the worker."""
    register_provider(build_deployment_deps)
