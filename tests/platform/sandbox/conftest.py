"""Sandbox-area fixtures (shared via fixtures, never via imports).

- ``subprocess_sandbox``: a SubprocessSandbox with a short startup timeout.
- ``fake_broker(responses, allowed=None)``: a ToolBroker answering from a dict/callable.
- ``mcp_connector(mcp, system_id)``: wraps an ``app.demo`` McpRuntime as a Connector.
- ``demo_integrations``: the demo orchestrator's loop-1/loop-2 integrations (final + pre-review drafts).
- ``raw_child(sandbox, code, event)``: talk the child protocol directly, skipping host-side
  validation, to test the child's own restrictions (defence in depth).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.demo.mcp_tools import McpRuntime
from app.demo.models import Integration
from app.demo.orchestrator import DemoOrchestrator
from app.platform.interfaces import HealthStatus, SandboxLimits
from app.platform.sandbox.process import ProcessSandbox, prelude_source
from app.platform.sandbox.subprocess_backend import SubprocessSandbox


@pytest.fixture
def subprocess_sandbox() -> SubprocessSandbox:
    return SubprocessSandbox(startup_timeout_s=30.0, tool_call_timeout_s=10.0)


@dataclass
class FakeBroker:
    responses: dict[str, Any] | Callable[[str, dict[str, Any]], Any]
    allowed: frozenset[str] = frozenset()
    seen: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        self.seen.append((tool, args))
        if callable(self.responses):
            return self.responses(tool, args)
        return self.responses[tool]


@pytest.fixture
def fake_broker() -> Callable[..., FakeBroker]:
    def make(responses: dict[str, Any] | Callable[[str, dict[str, Any]], Any], allowed: Any = None) -> FakeBroker:
        names = allowed if allowed is not None else (responses.keys() if isinstance(responses, dict) else ())
        return FakeBroker(responses=responses, allowed=frozenset(names))

    return make


class McpConnector:
    """Connector over an in-process McpRuntime (the demo's mock vendor apps)."""

    def __init__(self, mcp: McpRuntime, system_id: str = "mock") -> None:
        self.mcp = mcp
        self.system_id = system_id
        self.closed = False

    def tools(self) -> list[str]:
        return [t.name for t in self.mcp.tools]

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        return await self.mcp.call(tool, args)

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=0.0)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def mcp_connector() -> Callable[..., McpConnector]:
    def make(mcp: McpRuntime, system_id: str = "mock") -> McpConnector:
        return McpConnector(mcp, system_id)

    return make


@dataclass(frozen=True)
class DemoIntegrations:
    loop1: Integration
    loop2: Integration
    loop1_draft: Integration


async def _build_demo() -> DemoIntegrations:
    draft = DemoOrchestrator(mode="scripted", learning_enabled=True)
    integ = await draft.loop1_intent()
    loop1_draft = (await draft.loop1_refine(integration_id=integ.id)).model_copy(deep=True)
    orch = DemoOrchestrator(mode="scripted", learning_enabled=True)
    loop1 = await orch.run_loop1()
    loop2 = await orch.run_loop2()
    return DemoIntegrations(loop1=loop1, loop2=loop2, loop1_draft=loop1_draft)


@pytest.fixture(scope="module")
def demo_integrations() -> DemoIntegrations:
    # Sync + asyncio.run so the (slow-ish) orchestrator loops run once per module.
    return asyncio.run(_build_demo())


RawChild = Callable[..., Awaitable[list[dict[str, Any]]]]


@pytest.fixture
def raw_child() -> RawChild:
    """Run ``code`` in the real child bootstrap WITHOUT host validation; return every child message."""

    async def run(sandbox: ProcessSandbox, code: str, event: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        child = await sandbox.launch(SandboxLimits())
        assert child.proc.stdin is not None and child.proc.stdout is not None
        messages: list[dict[str, Any]] = []
        try:
            messages.append(json.loads(await asyncio.wait_for(child.proc.stdout.readline(), 30)))
            start = {"type": "start", "code": code, "event": event or {}, "prelude": prelude_source(),
                     "max_output_bytes": 100_000, "max_log_lines": 50}
            child.proc.stdin.write(json.dumps(start).encode() + b"\n")
            await child.proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(child.proc.stdout.readline(), 30)
                if not line:
                    break
                msg = json.loads(line)
                messages.append(msg)
                if msg["type"] == "tool_call":
                    reply = {"type": "tool_result", "id": msg["id"], "ok": True, "result": {"ok": True}}
                    child.proc.stdin.write(json.dumps(reply).encode() + b"\n")
                    await child.proc.stdin.drain()
                if msg["type"] in ("result", "error"):
                    break
        finally:
            await child.kill()
            await child.cleanup()
        return messages

    return run
