"""Host-side tool broker: the only path from sandboxed code to the outside world.

``DefaultToolBroker`` enforces the approved version's ``allowed_tools`` and the
per-run call budget, executes allowed calls through the tenant's
:class:`~app.platform.interfaces.Connector`, records a ``ToolCall`` per call and
counts ``TOOL_CALLS``.

Errors:
- :class:`ToolDenied` (policy: tool not allowed / budget exhausted / no
  connector). The sandbox aborts the run; the code never gets to retry.
- :class:`ToolFailed` (the upstream call failed). Carries a structured,
  scrubbed ``payload`` that is handed back to the sandboxed code, which sees it
  as a ``ToolError`` exception. It never contains headers, tokens or secrets.

Connector error convention (what ``safe_error`` reads): ``upstream_status`` or
``status_code`` (int, the vendor's HTTP status; ``status`` is used only for
non-``PlatformError`` exceptions such as ``app.demo.mcp_tools.McpToolError``)
and ``detail`` (the vendor's validation detail, e.g. ``[{"field", "message"}]``).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any, NoReturn

from app.platform.errors import Forbidden, PlatformError, UpstreamError
from app.platform.interfaces import Connector, ToolCall
from app.platform.observability.metrics import TOOL_CALLS

log = logging.getLogger(__name__)

MAX_MESSAGE = 500
MAX_DETAIL_BYTES = 4000
MAX_DETAIL_DEPTH = 6
REDACTED = "[redacted]"

_SENSITIVE_KEY = re.compile(
    r"(authori[sz]ation|cookie|token|secret|password|passwd|api[-_]?key|credential|private[-_]?key|headers?)$|^x-",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    # "Authorization: Bearer abc", "Bearer abc", "Basic abc"
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{6,}"),
    # key=value / key: value for sensitive-looking keys (query strings, header dumps, JSON-ish text)
    re.compile(
        r"(?i)([\"']?(?:authori[sz]ation|cookie|set-cookie|x-[a-z0-9-]*key|api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|client[_-]?secret|secret|password|token|sig|signature)[\"']?\s*[:=]\s*)"
        r"([\"']?)[^\s\"'&,;}]+"
    ),
    # credentials embedded in URLs: scheme://user:pass@host
    re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"),
)


def scrub(text: str) -> str:
    """Remove credential-looking material from free text (exception messages, details)."""
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    out = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    out = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}{REDACTED}@", out)
    return out


def _clean_detail(value: object, depth: int = 0) -> object:
    if depth > MAX_DETAIL_DEPTH:
        return "[truncated]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return scrub(value)[:MAX_MESSAGE]
    if isinstance(value, Mapping):
        return {
            str(k): REDACTED if _SENSITIVE_KEY.search(str(k)) else _clean_detail(v, depth + 1)
            for k, v in list(value.items())[:50]
        }
    if isinstance(value, list | tuple):
        return [_clean_detail(v, depth + 1) for v in list(value)[:50]]
    return scrub(str(value))[:MAX_MESSAGE]


def _status(exc: BaseException) -> int | None:
    for attr in ("upstream_status", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    if not isinstance(exc, PlatformError):
        value = getattr(exc, "status", None)
        if isinstance(value, int):
            return value
    return None


def safe_error(tool: str, exc: BaseException) -> dict[str, Any]:
    """The structured error handed to sandboxed code: no headers, no secrets, bounded size."""
    if isinstance(exc, PlatformError):
        code, message = exc.code, exc.message
    else:
        code, message = "connector_error", f"{type(exc).__name__}: {exc}"
    detail = _clean_detail(getattr(exc, "detail", None))
    if len(json.dumps(detail, default=str)) > MAX_DETAIL_BYTES:
        detail = "[detail too large]"
    return {
        "tool": tool,
        "code": code,
        "status": _status(exc),
        "message": scrub(message)[:MAX_MESSAGE],
        "detail": detail,
    }


class ToolDenied(Forbidden):
    """Policy refused the call (not allowed / over budget / unroutable)."""

    code = "tool_denied"


class ToolFailed(UpstreamError):
    """The connector call failed; ``payload`` is safe to hand to sandboxed code."""

    code = "tool_failed"

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("message", "tool call failed")))
        self.payload = payload


class DefaultToolBroker:
    """Gate between sandboxed code and connectors for ONE run (budget is per instance)."""

    def __init__(
        self,
        connectors: Mapping[str, Connector],
        allowed: frozenset[str] | set[str] | list[str] | tuple[str, ...],
        max_tool_calls: int,
    ) -> None:
        self.connectors = dict(connectors)
        self.allowed: frozenset[str] = frozenset(allowed)
        self.max_tool_calls = max_tool_calls
        self.calls: list[ToolCall] = []
        self._attempts = 0

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        if tool not in self.allowed:
            self._deny(tool, args, f"tool {tool!r} is not in this version's allowed tools")
        if self._attempts >= self.max_tool_calls:
            self._deny(tool, args, f"tool call budget of {self.max_tool_calls} exhausted")
        connector = self.connectors.get(tool)
        if connector is None:
            self._deny(tool, args, f"no connector is configured for tool {tool!r}")
        self._attempts += 1
        started = time.perf_counter()
        try:
            result = await connector.call(tool, args)
        except Exception as exc:
            payload = safe_error(tool, exc)
            # Log the type and safe message only: raw exception text may carry request data.
            log.warning(
                "broker.tool_failed",
                extra={"tool": tool, "error_type": type(exc).__name__, "status": payload["status"]},
            )
            self._record(tool, args, ok=False, started=started, error=payload["message"], outcome="error")
            raise ToolFailed(payload) from exc
        self._record(tool, args, ok=True, started=started, error=None, outcome="ok")
        return result

    def _deny(self, tool: str, args: dict[str, Any], message: str) -> NoReturn:
        self.calls.append(ToolCall(tool=tool, args=args, ok=False, duration_ms=0.0, error=message))
        # Unknown names come from untrusted code: never use them as metric labels.
        TOOL_CALLS.labels(tool=tool if tool in self.allowed else "<not-allowed>", outcome="denied").inc()
        raise ToolDenied(message)

    def _record(
        self, tool: str, args: dict[str, Any], *, ok: bool, started: float, error: str | None, outcome: str
    ) -> None:
        duration = (time.perf_counter() - started) * 1000.0
        self.calls.append(ToolCall(tool=tool, args=args, ok=ok, duration_ms=duration, error=error))
        TOOL_CALLS.labels(tool=tool, outcome=outcome).inc()
