"""Live LLM agent (Anthropic Python SDK, manual tool-use loop).

Used only in ``live`` mode. The tools wrap the same deterministic services the
scripted mode calls (KG queries, MCP calls, drafting, matching), so Claude decides
*what to look at* and writes the plan / rationale, while the services stay the
source of truth for state changes.

The API key is read from the environment (``ANTHROPIC_API_KEY``) by the SDK; it is
never passed around or logged. Model: env ``DEMO_MODEL`` (default ``claude-opus-5-5``).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TURNS = 8
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ToolFn = Callable[[dict[str, Any]], Awaitable[Any]]


class LiveAgentError(RuntimeError):
    """The live agent could not complete; callers fall back to scripted mode."""


@dataclass
class AgentTool:
    name: str  # must match ^[a-zA-Z0-9_-]{1,64}$
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolCallRecord:
    name: str
    input: dict[str, Any]
    output: Any
    is_error: bool = False


@dataclass
class AgentResult:
    text: str
    calls: list[ToolCallRecord] = field(default_factory=list)
    turns: int = 0


OnToolCall = Callable[[str, dict[str, Any]], Awaitable[None]]


class MessagesClient(Protocol):
    """Subset of ``anthropic.AsyncAnthropic().beta.messages`` we use (injectable for tests)."""

    async def create(self, **kwargs: Any) -> Any: ...


def live_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_name() -> str:
    return os.environ.get("DEMO_MODEL", DEFAULT_MODEL)


def _default_client() -> MessagesClient:
    import anthropic  # imported lazily so scripted mode never needs the SDK configured

    client: MessagesClient = anthropic.AsyncAnthropic().beta.messages
    return client


def _block_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        data: dict[str, Any] = block.model_dump(exclude_none=True)
        return data
    return dict(block)


class LiveAgent:
    def __init__(self, client: MessagesClient | None = None, model: str | None = None) -> None:
        self._client = client
        self.model = model or model_name()

    @property
    def client(self) -> MessagesClient:
        if self._client is None:
            self._client = _default_client()
        return self._client

    async def _create(self, output_config: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        import anthropic

        try:
            return await self.client.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                output_config=output_config or {"effort": DEFAULT_EFFORT},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                **kwargs,
            )
        except anthropic.AuthenticationError as exc:
            raise LiveAgentError(f"authentication failed: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LiveAgentError(f"rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LiveAgentError(f"API error {exc.status_code}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LiveAgentError(f"connection error: {exc}") from exc

    async def run(
        self,
        system: str,
        prompt: str,
        tools: list[AgentTool],
        on_tool_call: OnToolCall | None = None,
    ) -> AgentResult:
        """Manual agentic loop: call tools until Claude ends its turn."""
        by_name = {t.name: t for t in tools}
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        result = AgentResult(text="")
        for turn in range(1, MAX_TURNS + 1):
            response = await self._create(system=system, messages=messages, tools=[t.definition() for t in tools])
            result.turns = turn
            stop = getattr(response, "stop_reason", None)
            content = list(getattr(response, "content", []))
            if stop == "refusal":
                raise LiveAgentError("model declined the request (stop_reason=refusal)")
            if stop == "max_tokens":
                raise LiveAgentError("response hit max_tokens before finishing")
            messages.append({"role": "assistant", "content": [_block_dict(b) for b in content]})
            if stop == "pause_turn":
                continue
            tool_uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
            if stop != "tool_use" or not tool_uses:
                result.text = "\n".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")
                return result
            tool_results: list[dict[str, Any]] = []
            for block in tool_uses:
                name, args = block.name, dict(block.input or {})
                if on_tool_call is not None:
                    await on_tool_call(name, args)
                tool = by_name.get(name)
                if tool is None:
                    output: Any = f"unknown tool {name!r}"
                    is_error = True
                else:
                    try:
                        output, is_error = await tool.fn(args), False
                    except (KeyError, ValueError, LookupError) as exc:
                        logger.warning("live.tool_error", extra={"tool": name, "error": str(exc)})
                        output, is_error = f"{type(exc).__name__}: {exc}", True
                result.calls.append(ToolCallRecord(name=name, input=args, output=output, is_error=is_error))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output if isinstance(output, str) else json.dumps(output, default=str)[:20000],
                        "is_error": is_error,
                    }
                )
            # All results in one user message (keeps parallel tool use working).
            messages.append({"role": "user", "content": tool_results})
        raise LiveAgentError(f"agent did not finish within {MAX_TURNS} turns")

    async def json_call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Single structured-output call (used for optional schema-alignment refinement)."""
        response = await self._create(
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            raise LiveAgentError("model declined the request (stop_reason=refusal)")
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise LiveAgentError("structured output missing text block")
        data: dict[str, Any] = json.loads(text)
        return data
