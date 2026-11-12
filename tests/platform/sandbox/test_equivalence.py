"""Generated code == app.demo IntegrationExecutor: same tool calls, same payloads, same outcome.

Each side runs against its own fresh mock world (app.demo.mocks) built by the
demo's own ``sandbox_factory``; the platform side runs the rendered code in the
real subprocess sandbox through DefaultToolBroker and an McpRuntime connector.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.demo.models import Integration
from app.demo.runtime import HISTORICAL_RECORDS, IntegrationExecutor, sandbox_factory
from app.platform.interfaces import SandboxLimits
from app.platform.sandbox.broker import DefaultToolBroker
from app.platform.sandbox.codegen import render_runnable, required_tools

Calls = list[tuple[str, dict[str, Any], bool]]


def event_for(integ: Integration, record: str) -> dict[str, Any]:
    if integ.trigger_node == "proc-closedwon":
        return {"opportunity_id": record}
    return {"agreement_id": record.replace("opp-", "agr-"), "event": "agreement.completed"}


def world_state(world: Any) -> dict[str, Any]:
    return {"customers": world.customers, "invoices": world.invoices, "opportunities": world.opportunities}


async def run_demo(integ: Integration, event: dict[str, Any]) -> tuple[Calls, bool, dict[str, Any]]:
    apps, mcp = sandbox_factory()()
    res = await IntegrationExecutor(mcp).run(integ, event)
    return [(c.tool, c.args, c.ok) for c in res.calls], res.ok, world_state(apps.world)


async def run_platform(
    integ: Integration, event: dict[str, Any], sandbox: Any, mcp_connector: Any
) -> tuple[Calls, bool, dict[str, Any], str | None]:
    apps, mcp = sandbox_factory()()
    rows = [r.to_json() for r in integ.mappings]  # exactly what IntegrationVersion.mappings stores
    code = render_runnable(rows, name=integ.name, source_system=integ.source, target_system=integ.target,
                           trigger=integ.trigger)
    tools = required_tools(rows, trigger=integ.trigger)
    connector = mcp_connector(mcp)
    broker = DefaultToolBroker({t: connector for t in tools}, tools, max_tool_calls=50)
    res = await sandbox.run(code, event, broker, SandboxLimits(timeout_s=20))
    assert [c.tool for c in res.tool_calls] == [c.tool for c in broker.calls]
    return [(c.tool, c.args, c.ok) for c in res.tool_calls], res.ok, world_state(apps.world), res.error


@pytest.mark.parametrize("which", ["loop1", "loop2", "loop1_draft"])
async def test_generated_code_matches_demo_executor(
    which: str, demo_integrations: Any, subprocess_sandbox: Any, mcp_connector: Any
) -> None:
    integ: Integration = getattr(demo_integrations, which)
    for record in HISTORICAL_RECORDS:
        event = event_for(integ, record)
        demo_calls, demo_ok, demo_world = await run_demo(integ, event)
        calls, ok, world, error = await run_platform(integ, event, subprocess_sandbox, mcp_connector)
        assert calls == demo_calls, f"{which}/{record}"
        assert ok == demo_ok, f"{which}/{record}: {error}"
        assert world == demo_world, f"{which}/{record}"


async def test_equivalence_covers_success_and_failure(demo_integrations: Any) -> None:
    """Guard: the cases above really exercise both paths (all-green final versions, a failing draft)."""
    for integ in (demo_integrations.loop1, demo_integrations.loop2):
        for record in HISTORICAL_RECORDS:
            _, ok, _ = await run_demo(integ, event_for(integ, record))
            assert ok
    draft_results = [(await run_demo(demo_integrations.loop1_draft, event_for(demo_integrations.loop1_draft, r)))[1]
                     for r in HISTORICAL_RECORDS]
    assert not all(draft_results)
