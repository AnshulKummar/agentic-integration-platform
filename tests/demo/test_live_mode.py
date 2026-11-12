"""Live mode without network: a fake Messages client drives the tool-use loop."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.demo.llm import AgentTool, LiveAgent, LiveAgentError
from app.demo.orchestrator import DemoOrchestrator


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class Response:
    content: list[Block]
    stop_reason: str


class FakeMessages:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Response:
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


async def test_manual_tool_loop_executes_tools_and_returns_text() -> None:
    calls: list[dict[str, Any]] = []

    async def echo(args: dict[str, Any]) -> Any:
        calls.append(args)
        return {"ok": True}

    fake = FakeMessages([
        Response([Block("tool_use", id="tu1", name="echo", input={"x": 1}),
                  Block("tool_use", id="tu2", name="missing", input={})], "tool_use"),
        Response([Block("text", text="Plan ready.")], "end_turn"),
    ])
    agent = LiveAgent(client=fake, model="claude-opus-5-5")
    result = await agent.run("sys", "go", [AgentTool("echo", "Echo", {"type": "object"}, echo)])
    assert result.text == "Plan ready." and result.turns == 2
    assert calls == [{"x": 1}]
    assert [c.is_error for c in result.calls] == [False, True]
    first = fake.requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"] == {"effort": "medium"}
    assert "thinking" not in first and "tool_choice" not in first
    tool_results = fake.requests[1]["messages"][-1]
    assert tool_results["role"] == "user" and len(tool_results["content"]) == 2  # one message, all results


async def test_refusal_raises_live_agent_error() -> None:
    agent = LiveAgent(client=FakeMessages([Response([], "refusal")]))
    with pytest.raises(LiveAgentError):
        await agent.run("sys", "go", [])


async def test_live_mode_without_key_falls_back_to_scripted(caplog: pytest.LogCaptureFixture) -> None:
    o = DemoOrchestrator(mode="live")
    assert o.mode == "scripted" and o.requested_mode == "live"
    assert "ANTHROPIC_API_KEY is not set" in caplog.text
    assert o.trace[0].tone == "warn"


async def test_live_mode_uses_agent_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    fake = FakeMessages([
        Response([Block("tool_use", id="t1", name="find_patterns", input={})], "tool_use"),
        Response([Block("text", text="LIVE PLAN: real-time sync, 4 mappings.")], "end_turn"),
    ])
    o = DemoOrchestrator(mode="live", agent=LiveAgent(client=fake))
    assert o.mode == "live"
    await o.loop1_intent()
    assert o.chat[-1].text.startswith("LIVE PLAN")
    assert any(e.text.startswith("[live] find_patterns") for e in o.trace)


async def test_live_agent_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    o = DemoOrchestrator(mode="live", agent=LiveAgent(client=FakeMessages([Response([], "refusal")])))
    integ = await o.loop1_intent()
    assert integ.status == "planned"
    assert o.chat[-1].text.startswith("Plan:")  # scripted text
    assert any(e.tone == "warn" and "Live LLM unavailable" in e.text for e in o.trace)
