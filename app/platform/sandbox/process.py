"""Host side of the child protocol, shared by the subprocess and docker backends.

A backend only knows how to *launch* an isolated child running
``bootstrap.py``; :class:`ProcessSandbox` validates the code, drives the JSON
-lines protocol (see ``bootstrap.py``), routes ``tool_call`` messages through the
:class:`~app.platform.interfaces.ToolBroker`, enforces the limits and records
metrics.

Timeout semantics: ``SandboxLimits.timeout_s`` bounds the time the *child*
spends computing (time waiting on tool calls is excluded, because connectors
have their own timeouts/retries). Each tool call is bounded by
``tool_call_timeout_s``. So the worst-case wall time of a run is
``startup + timeout_s + max_tool_calls * tool_call_timeout_s``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from app.platform.errors import PlatformError
from app.platform.interfaces import SandboxLimits, SandboxResult, ToolBroker, ToolCall
from app.platform.observability.metrics import SANDBOX_RUNS
from app.platform.sandbox.broker import ToolDenied, ToolFailed, safe_error
from app.platform.sandbox.validate import CodeRejected, validate_code

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
MAX_LOG_LINES = 200
MAX_STDERR_BYTES = 16_000
#: Headroom on top of max_output_bytes for one protocol line (envelope, tool args/results).
LINE_HEADROOM = 256_000


@cache
def bootstrap_source() -> str:
    return (_HERE / "bootstrap.py").read_text(encoding="utf-8")


@cache
def prelude_source() -> str:
    return (_HERE / "prelude.py").read_text(encoding="utf-8")


def stream_limit(limits: SandboxLimits) -> int:
    return limits.max_output_bytes + LINE_HEADROOM


@dataclass
class Child:
    """A launched child process speaking the protocol on stdin/stdout."""

    proc: asyncio.subprocess.Process
    #: Hard-stop the child (and its container/job). Must be idempotent.
    kill: Callable[[], Awaitable[None]]
    #: Release backend resources after the process has exited.
    cleanup: Callable[[], Awaitable[None]]


class _Abort(Exception):
    """Ends a run early with an error result (and kills the child)."""

    def __init__(self, message: str, outcome: str) -> None:
        super().__init__(message)
        self.message = message
        self.outcome = outcome


@dataclass
class _Run:
    limits: SandboxLimits
    broker: ToolBroker
    tool_call_timeout_s: float
    logs: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    stderr: bytearray = field(default_factory=bytearray)

    def add_log(self, line: str) -> None:
        if len(self.logs) < MAX_LOG_LINES:
            self.logs.append(line[:2000])
        elif len(self.logs) == MAX_LOG_LINES:
            self.logs.append("[log truncated]")


class ProcessSandbox(ABC):
    """Template for sandboxes that run ``bootstrap.py`` in an isolated child process."""

    backend: str = "process"

    def __init__(self, *, startup_timeout_s: float = 15.0, tool_call_timeout_s: float = 60.0) -> None:
        self.startup_timeout_s = startup_timeout_s
        self.tool_call_timeout_s = tool_call_timeout_s

    @abstractmethod
    async def launch(self, limits: SandboxLimits, *, script: str | None = None) -> Child:
        """Start an isolated child running ``script`` (default: the bootstrap)."""

    async def run(self, code: str, event: dict[str, Any], broker: ToolBroker, limits: SandboxLimits) -> SandboxResult:
        started = time.perf_counter()
        state = _Run(limits=limits, broker=broker, tool_call_timeout_s=self.tool_call_timeout_s)
        try:
            validate_code(code)
            event_json = json.loads(json.dumps(event))
        except CodeRejected as exc:
            return self._finish(state, started, None, f"validation failed: {exc.message}", "rejected")
        except (TypeError, ValueError) as exc:
            return self._finish(state, started, None, f"event is not JSON-serialisable: {exc}", "rejected")

        child = await self.launch(limits)
        try:
            output = await self._drive(child, state, code, event_json)
        except _Abort as exc:
            self._attach_stderr(state)
            return self._finish(state, started, None, exc.message, exc.outcome)
        finally:
            await child.kill()
            await child.cleanup()
        return self._finish(state, started, output, None, "ok")

    # ------------------------------------------------------------------ internals
    def _finish(
        self, state: _Run, started: float, output: dict[str, Any] | None, error: str | None, outcome: str
    ) -> SandboxResult:
        SANDBOX_RUNS.labels(backend=self.backend, outcome=outcome).inc()
        return SandboxResult(
            ok=error is None,
            output=output,
            error=error,
            tool_calls=state.calls,
            logs=state.logs,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    @staticmethod
    def _attach_stderr(state: _Run) -> None:
        text = state.stderr.decode("utf-8", errors="replace").strip()
        if text:
            for line in text.splitlines()[-20:]:
                state.add_log(f"[stderr] {line}")

    async def _drain_stderr(self, child: Child, state: _Run) -> None:
        stream = child.proc.stderr
        if stream is None:
            return
        while chunk := await stream.read(4096):
            room = MAX_STDERR_BYTES - len(state.stderr)
            if room > 0:
                state.stderr += chunk[:room]

    async def _read(self, child: Child, timeout_s: float, *, total: float | None = None) -> dict[str, Any]:
        stdout = child.proc.stdout
        if stdout is None:
            raise _Abort("sandbox child has no stdout", "error")
        try:
            line = await asyncio.wait_for(stdout.readline(), timeout=max(timeout_s, 0.001))
        except TimeoutError:
            what, limit = ("startup", timeout_s) if total is None else ("execution", total)
            raise _Abort(f"{what} timed out after {limit:.1f}s", "timeout") from None
        except ValueError:  # StreamReader limit overrun
            raise _Abort("sandbox message exceeds the size limit", "error") from None
        if not line:
            code = await _exit_code(child)
            raise _Abort(f"sandbox exited unexpectedly (exit code {code})", "error")
        try:
            msg = json.loads(line)
        except ValueError:
            raise _Abort("sandbox sent a malformed protocol message", "error") from None
        if not isinstance(msg, dict):
            raise _Abort("sandbox sent a malformed protocol message", "error")
        return msg

    async def _write(self, child: Child, msg: dict[str, Any]) -> None:
        stdin = child.proc.stdin
        if stdin is None:
            raise _Abort("sandbox child has no stdin", "error")
        data = json.dumps(msg, separators=(",", ":"), ensure_ascii=True, default=str).encode("ascii") + b"\n"
        try:
            stdin.write(data)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            code = await _exit_code(child)
            raise _Abort(f"sandbox exited unexpectedly (exit code {code})", "error") from None

    async def _drive(self, child: Child, state: _Run, code: str, event: dict[str, Any]) -> dict[str, Any]:
        stderr_task = asyncio.create_task(self._drain_stderr(child, state))
        try:
            ready = await self._read(child, self.startup_timeout_s)
            if ready.get("type") != "ready":
                raise _Abort("sandbox failed to start", "error")
            await self._write(
                child,
                {
                    "type": "start",
                    "code": code,
                    "event": event,
                    "prelude": prelude_source(),
                    "max_output_bytes": state.limits.max_output_bytes,
                    "max_log_lines": MAX_LOG_LINES,
                },
            )
            budget = state.limits.timeout_s
            while True:
                if budget <= 0:
                    raise _Abort(f"timed out after {state.limits.timeout_s:.1f}s", "timeout")
                t0 = time.perf_counter()
                msg = await self._read(child, budget, total=state.limits.timeout_s)
                budget -= time.perf_counter() - t0
                kind = msg.get("type")
                if kind == "log":
                    state.add_log(str(msg.get("line", "")))
                elif kind == "tool_call":
                    await self._write(child, await self._tool_call(state, msg))
                elif kind == "result":
                    return self._check_output(state, msg.get("output"))
                elif kind == "error":
                    raise _Abort(str(msg.get("message", "integration failed"))[:4000], "error")
                else:
                    raise _Abort(f"sandbox sent an unknown message type {kind!r}", "error")
        finally:
            if child.proc.returncode is not None:
                # The child is gone: give the drain a moment to collect its last stderr (crash reasons).
                await asyncio.wait({stderr_task}, timeout=1.0)
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

    @staticmethod
    def _check_output(state: _Run, output: object) -> dict[str, Any]:
        if not isinstance(output, dict):
            raise _Abort("run() must return a dict", "error")
        size = len(json.dumps(output, separators=(",", ":"), ensure_ascii=True))
        if size > state.limits.max_output_bytes:
            raise _Abort(f"output is {size} bytes, limit is {state.limits.max_output_bytes}", "error")
        return output

    async def _tool_call(self, state: _Run, msg: dict[str, Any]) -> dict[str, Any]:
        call_id = msg.get("id")
        tool = msg.get("tool")
        args = msg.get("args")
        if not isinstance(tool, str) or not isinstance(args, dict):
            raise _Abort("sandbox sent a malformed tool_call", "error")
        t0 = time.perf_counter()

        def record(ok: bool, error: str | None) -> None:
            state.calls.append(
                ToolCall(tool=tool, args=args, ok=ok, duration_ms=(time.perf_counter() - t0) * 1000.0, error=error)
            )

        try:
            result = await asyncio.wait_for(state.broker.call(tool, args), timeout=state.tool_call_timeout_s)
        except ToolDenied as exc:
            record(False, exc.message)
            raise _Abort(f"tool call denied: {exc.message}", "denied") from None
        except ToolFailed as exc:
            record(False, exc.message)
            return {"type": "tool_result", "id": call_id, "ok": False, "error": exc.payload}
        except TimeoutError:
            message = f"tool call exceeded {state.tool_call_timeout_s:.0f}s"
            record(False, message)
            error = {"tool": tool, "code": "tool_timeout", "status": None, "message": message, "detail": None}
            return {"type": "tool_result", "id": call_id, "ok": False, "error": error}
        except PlatformError as exc:
            payload = safe_error(tool, exc)
            record(False, payload["message"])
            return {"type": "tool_result", "id": call_id, "ok": False, "error": payload}
        except Exception as exc:
            # A broker bug, not an upstream failure: fail the run loudly.
            log.exception("sandbox.broker_error", extra={"tool": tool})
            record(False, type(exc).__name__)
            raise _Abort(f"internal broker error ({type(exc).__name__})", "error") from exc
        record(True, None)
        return {"type": "tool_result", "id": call_id, "ok": True, "result": result}


async def _exit_code(child: Child) -> int | None:
    try:
        return await asyncio.wait_for(child.proc.wait(), timeout=2.0)
    except TimeoutError:
        return None
