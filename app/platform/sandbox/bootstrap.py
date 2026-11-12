"""Sandbox child bootstrap, shared by every process backend.

Runs as ``python -I -c <this file's source>`` (subprocess backend) or inside a
container (docker backend). Standard library only; it must not import anything
from the platform. The host reads this file with ``bootstrap_source()``.

JSON-lines protocol (one JSON object per line, UTF-8). stdout is reserved for
the protocol; integration ``print()`` output travels as ``log`` messages.

    child -> host  {"type": "ready"}
    host  -> child {"type": "start", "code", "event", "prelude", "max_output_bytes", "max_log_lines"}
    child -> host  {"type": "log", "line"}                         (any number)
    child -> host  {"type": "tool_call", "id", "tool", "args"}     (then blocks)
    host  -> child {"type": "tool_result", "id", "ok", "result" | "error"}
    child -> host  {"type": "result", "output"} | {"type": "error", "message"}

The integration code runs with restricted builtins (no open/eval/exec/getattr/
type/..., an import hook limited to json/math/re/datetime/decimal that returns
module *proxies* without submodules) plus the names exported by the trusted
prelude. This is defence in depth: the host validated the code's AST first, and
the process itself is isolated by the backend.
"""

from __future__ import annotations

import builtins
import json
import sys
import types
from collections.abc import Callable
from typing import Any

ALLOWED_IMPORTS = ("json", "math", "re", "datetime", "decimal")
SAFE_BUILTINS = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytes", "callable", "chr", "dict", "divmod", "enumerate",
    "filter", "float", "frozenset", "hash", "hex", "int", "isinstance", "iter", "len", "list", "map", "max",
    "min", "next", "oct", "ord", "pow", "range", "repr", "reversed", "round", "set", "slice", "sorted", "str",
    "sum", "tuple", "zip",
    "ArithmeticError", "AssertionError", "AttributeError", "Exception", "IndexError", "KeyError", "LookupError",
    "NameError", "NotImplementedError", "OverflowError", "RuntimeError", "StopIteration", "TypeError",
    "UnicodeError", "ValueError", "ZeroDivisionError",
)
MAX_LOG_LINE = 2000
MAX_ERROR = 4000


class _Protocol:
    def __init__(self) -> None:
        self._in = sys.stdin.buffer
        self._out = sys.stdout.buffer

    def send(self, msg: dict[str, Any]) -> None:
        self.send_raw(json.dumps(msg, separators=(",", ":"), ensure_ascii=True))

    def send_raw(self, line: str) -> None:
        self._out.write(line.encode("ascii") + b"\n")
        self._out.flush()

    def recv(self) -> dict[str, Any]:
        line = self._in.readline()
        if not line:
            # Host went away: nothing sensible left to do.
            raise SystemExit(3)
        msg = json.loads(line)
        if not isinstance(msg, dict):
            raise SystemExit(4)
        return msg


class _LogSink:
    """Collects print()/stray writes into bounded ``log`` messages."""

    def __init__(self, proto: _Protocol, max_lines: int) -> None:
        self._proto = proto
        self._max = max_lines
        self._sent = 0
        self._buf = ""

    def _line(self, line: str) -> None:
        if self._sent < self._max:
            self._proto.send({"type": "log", "line": line[:MAX_LOG_LINE]})
        elif self._sent == self._max:
            self._proto.send({"type": "log", "line": f"[log truncated after {self._max} lines]"})
        self._sent += 1

    def write(self, text: str) -> int:
        self._buf += str(text)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._line(line)
        if len(self._buf) > MAX_LOG_LINE:
            self._line(self._buf)
            self._buf = ""
        return len(text)

    def flush(self) -> None:
        if self._buf:
            self._line(self._buf)
            self._buf = ""

    def print(self, *args: object, sep: object = " ", end: object = "\n") -> None:
        self.write(str(sep).join(str(a) for a in args) + str(end))


def _module_proxy(name: str) -> types.SimpleNamespace:
    module = __import__(name)
    public = {
        key: value
        for key, value in vars(module).items()
        if not key.startswith("_") and not isinstance(value, types.ModuleType)
    }
    return types.SimpleNamespace(**public)


def _restricted_builtins(log: _LogSink) -> dict[str, Any]:
    proxies = {name: _module_proxy(name) for name in ALLOWED_IMPORTS}

    def guarded_import(
        name: str,
        globals: object = None,  # noqa: A002 - mirrors __import__'s signature
        locals: object = None,  # noqa: A002
        fromlist: object = (),
        level: int = 0,
    ) -> types.SimpleNamespace:
        if level != 0 or name not in proxies:
            raise ImportError(f"import of {name!r} is not allowed in integration code")
        return proxies[name]

    safe: dict[str, Any] = {name: getattr(builtins, name) for name in SAFE_BUILTINS}
    safe["print"] = log.print
    safe["__import__"] = guarded_import
    return safe


def _make_tools(proto: _Protocol, tool_error: Callable[[Any], BaseException]) -> object:
    counter = [0]

    def call(name: str, args: dict[str, Any] | None = None) -> Any:
        if not isinstance(name, str):
            raise TypeError("tools.call(name, args): name must be a str")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise TypeError("tools.call(name, args): args must be a dict")
        counter[0] += 1
        call_id = counter[0]
        proto.send({"type": "tool_call", "id": call_id, "tool": name, "args": args})
        reply = proto.recv()
        if reply.get("type") != "tool_result" or reply.get("id") != call_id:
            raise SystemExit(5)
        if reply.get("ok"):
            return reply.get("result")
        raise tool_error(reply.get("error") or {})

    class Tools:
        __slots__ = ("call",)

        def __init__(self) -> None:
            self.call = call

    return Tools()


def _error_line(exc: BaseException) -> str:
    tb = exc.__traceback__
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<integration>":
            line = tb.tb_lineno
        tb = tb.tb_next
    where = f" (integration line {line})" if line is not None else ""
    return f"{type(exc).__name__}{where}: {exc}"[:MAX_ERROR]


def main() -> int:
    proto = _Protocol()
    proto.send({"type": "ready"})
    start = proto.recv()
    if start.get("type") != "start":
        return 2
    max_output = int(start.get("max_output_bytes", 1_000_000))
    log = _LogSink(proto, int(start.get("max_log_lines", 200)))
    # Nothing but the protocol may reach the real stdout/stderr from here on.
    sys.stdout = log  # type: ignore[assignment]
    sys.stderr = log  # type: ignore[assignment]

    prelude_ns: dict[str, Any] = {"__name__": "pmaip_prelude", "__builtins__": builtins}
    exec(compile(str(start.get("prelude", "")), "<prelude>", "exec"), prelude_ns)  # trusted host source
    exported = {name: prelude_ns[name] for name in prelude_ns.get("__all__", [])}

    namespace: dict[str, Any] = {"__name__": "integration", "__builtins__": _restricted_builtins(log)}
    namespace.update(exported)
    tools = _make_tools(proto, prelude_ns["ToolError"])
    try:
        exec(compile(str(start.get("code", "")), "<integration>", "exec"), namespace)
        run = namespace.get("run")
        if not callable(run):
            raise TypeError("integration code must define run(event, tools)")
        output = run(start.get("event") or {}, tools)
        if not isinstance(output, dict):
            raise TypeError(f"run() must return a dict, got {type(output).__name__}")
        data = json.dumps(output, separators=(",", ":"), ensure_ascii=True)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - every failure is reported to the host
        log.flush()
        proto.send({"type": "error", "message": _error_line(exc)})
        return 1
    log.flush()
    if len(data) > max_output:
        proto.send({"type": "error", "message": f"output is {len(data)} bytes, limit is {max_output}"})
        return 1
    proto.send_raw('{"type":"result","output":' + data + "}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
