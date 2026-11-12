"""Staged rollout: sandbox -> staging -> canary -> production, with automatic rollback.

* sandbox    : synthetic test suite, each test in a fresh mock world
* staging    : replay all historical records in a fresh mock world, 0 errors allowed
* canary     : run on the *production* mock world for a 5% slice (1 record); any error rolls back
* production : health-check target apps, subscribe the trigger webhook, apply bundled migrations

If a stage fails, later stages stay pending, earlier stages are marked rolled back
and the integration ends in ``rolled_back``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.demo.mcp_tools import McpRuntime, McpToolError
from app.demo.models import Integration, PipelineStage, StageName
from app.demo.runtime import (
    HISTORICAL_RECORDS,
    IntegrationExecutor,
    SandboxFactory,
    _event_for,
    replay_history,
    run_synthetic_tests,
)

logger = logging.getLogger(__name__)

STAGES: tuple[StageName, ...] = ("sandbox", "staging", "canary", "production")
CANARY_FRACTION = 0.05
WEBHOOK_CALLBACK = "https://pmaip.local/hooks/esign"

StageCallback = Callable[[PipelineStage], Awaitable[None]]


class StageFailed(RuntimeError):
    pass


@dataclass
class DeployResult:
    ok: bool
    stages: list[PipelineStage]
    failed_stage: StageName | None = None
    reason: str | None = None


class DeploymentPipeline:
    def __init__(self, production_mcp: McpRuntime, sandbox: SandboxFactory) -> None:
        self.production_mcp = production_mcp
        self.sandbox = sandbox

    def _record_label(self, integration: Integration) -> str:
        return "deals" if integration.trigger_node == "proc-closedwon" else "signed agreements"

    async def _sandbox(self, integration: Integration) -> str:
        results = await run_synthetic_tests(integration, self.sandbox)
        integration.tests = results
        failing = [t.name for t in results if not t.passed]
        if failing:
            raise StageFailed(f"{len(failing)} synthetic test(s) failed: {', '.join(failing)}")
        return f"{len(results)} synthetic tests passed"

    async def _staging(self, integration: Integration) -> str:
        replayed, errors = await replay_history(integration, self.sandbox)
        if errors:
            raise StageFailed(f"{errors}/{replayed} historical replays failed")
        return f"Replayed {replayed} historical {self._record_label(integration)}"

    async def _canary(self, integration: Integration) -> str:
        n = max(1, round(len(HISTORICAL_RECORDS) * CANARY_FRACTION))
        executor = IntegrationExecutor(self.production_mcp)
        for record in HISTORICAL_RECORDS[:n]:
            res = await executor.run(integration, _event_for(integration, record))
            if not res.ok:
                raise StageFailed(f"canary error: {res.errors[0]}")
        return f"{int(CANARY_FRACTION * 100)}% of {self._record_label(integration)}, 0 errors, auto-rollback armed"

    async def _production(self, integration: Integration) -> str:
        notes: list[str] = []
        try:
            for app in sorted({op.split(".")[0] for op in integration.operations} | {"crm"}):
                await self.production_mcp.call(f"{app}.health", {})
            if integration.trigger_node and integration.trigger_node.startswith("ep-") and self.production_mcp.has_tool(
                "esign.subscribe_completed"
            ):
                await self.production_mcp.call("esign.subscribe_completed", {"callback_url": WEBHOOK_CALLBACK})
                notes.append("subscribed agreement.completed")
            if "signer_email" in integration.migrations:
                await self.production_mcp.call(
                    "esign.send_envelope",
                    {"document_id": "migration-smoke-test", "signers": [{"email": "smoke@pmaip.local"}]},
                )
                notes.append("signer_email migration complete")
        except McpToolError as exc:
            raise StageFailed(f"production activation failed: {exc}") from exc
        return "Live for all tenants" + (f"; {'; '.join(notes)}" if notes else "")

    async def deploy(self, integration: Integration, on_stage: StageCallback | None = None) -> DeployResult:
        stages = [PipelineStage(name=name) for name in STAGES]
        integration.pipeline = stages
        integration.status = "deploying"
        runners = {
            "sandbox": self._sandbox,
            "staging": self._staging,
            "canary": self._canary,
            "production": self._production,
        }
        for i, stage in enumerate(stages):
            stage.status = "running"
            try:
                stage.note = await runners[stage.name](integration)
            except StageFailed as exc:
                stage.status = "failed"
                stage.note = str(exc)
                for prev in stages[:i]:
                    prev.status = "rolled_back"
                    prev.note = f"{prev.note} (rolled back)"
                integration.status = "rolled_back"
                logger.warning(
                    "pipeline.rollback",
                    extra={"integration": integration.id, "stage": stage.name, "reason": str(exc)},
                )
                if on_stage is not None:
                    await on_stage(stage)
                return DeployResult(ok=False, stages=stages, failed_stage=stage.name, reason=str(exc))
            stage.status = "passed"
            if on_stage is not None:
                await on_stage(stage)
        integration.status = "live"
        return DeployResult(ok=True, stages=stages)
