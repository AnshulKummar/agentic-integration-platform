"""SubprocessSandbox: protocol, limits, isolation and the child's own restrictions."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest

from app.platform.interfaces import Sandbox, SandboxLimits
from app.platform.observability.metrics import REGISTRY
from app.platform.sandbox.broker import DefaultToolBroker
from app.platform.sandbox.process import Child, ProcessSandbox, bootstrap_source
from app.platform.sandbox.subprocess_backend import SubprocessSandbox, child_env

ECHO = """
def run(event, tools):
    print("got", event["n"])
    reply = tools.call("svc.echo", {"n": event["n"]})
    return {"reply": reply, "double": event["n"] * 2}
"""


def runs(backend: str, outcome: str) -> float:
    return REGISTRY.get_sample_value("pmaip_sandbox_runs_total", {"backend": backend, "outcome": outcome}) or 0.0


async def test_runs_code_with_tool_calls_and_captures_logs(subprocess_sandbox: SubprocessSandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    assert isinstance(subprocess_sandbox, Sandbox)
    broker = fake_broker({"svc.echo": {"echo": True}})
    before = runs("subprocess", "ok")
    res = await subprocess_sandbox.run(ECHO, {"n": 21}, broker, SandboxLimits())
    assert res.ok, res.error
    assert res.output == {"reply": {"echo": True}, "double": 42}
    assert res.logs == ["got 21"]
    assert [(c.tool, c.args, c.ok) for c in res.tool_calls] == [("svc.echo", {"n": 21}, True)]
    assert broker.seen == [("svc.echo", {"n": 21})]
    assert res.duration_ms > 0
    assert runs("subprocess", "ok") == before + 1


async def test_invalid_code_is_rejected_before_any_process_starts(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    launched: list[bool] = []
    original = subprocess_sandbox.launch

    async def spy(*args: Any, **kwargs: Any) -> Child:
        launched.append(True)
        return await original(*args, **kwargs)

    subprocess_sandbox.launch = spy  # type: ignore[method-assign]
    res = await subprocess_sandbox.run("import os\ndef run(event, tools):\n    return {}\n", {}, fake_broker({}),
                                       SandboxLimits())
    assert not res.ok and "validation failed" in (res.error or "")
    assert launched == []


async def test_infinite_loop_is_killed_at_timeout(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    code = "def run(event, tools):\n    n = 0\n    while True:\n        n += 1\n"
    before = runs("subprocess", "timeout")
    res = await subprocess_sandbox.run(code, {}, fake_broker({}), SandboxLimits(timeout_s=1.0))
    assert not res.ok
    assert "timed out" in (res.error or "")
    assert res.duration_ms < 10_000
    assert runs("subprocess", "timeout") == before + 1


async def test_time_waiting_on_tools_does_not_count_against_the_code_budget(subprocess_sandbox) -> None:  # type: ignore[no-untyped-def]
    class SlowBroker:
        allowed = frozenset({"svc.slow"})

        async def call(self, tool: str, args: dict[str, Any]) -> Any:
            await asyncio.sleep(0.6)
            return {}

    code = "def run(event, tools):\n    for i in range(3):\n        tools.call('svc.slow', {})\n    return {}\n"
    res = await subprocess_sandbox.run(code, {}, SlowBroker(), SandboxLimits(timeout_s=1.5))
    assert res.ok, res.error


async def test_tool_budget_is_enforced(subprocess_sandbox, mcp_connector) -> None:  # type: ignore[no-untyped-def]
    class Counter:
        system_id = "svc"
        calls = 0

        def tools(self) -> list[str]:
            return ["svc.ping"]

        async def call(self, tool: str, args: dict[str, Any]) -> Any:
            self.calls += 1
            return {"n": self.calls}

        async def health(self) -> Any:
            raise NotImplementedError

        async def aclose(self) -> None:
            return None

    conn = Counter()
    broker = DefaultToolBroker({"svc.ping": conn}, {"svc.ping"}, max_tool_calls=3)
    code = """
def run(event, tools):
    n = 0
    while True:
        try:
            tools.call("svc.ping", {})
        except Exception:
            pass
        n += 1
"""
    res = await subprocess_sandbox.run(code, {}, broker, SandboxLimits(max_tool_calls=3))
    assert not res.ok
    assert "budget of 3 exhausted" in (res.error or "")
    assert conn.calls == 3  # the 4th call never reached the connector, and catching it did not help
    assert [c.ok for c in res.tool_calls] == [True, True, True, False]


async def test_disallowed_tool_is_rejected_and_run_aborted(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    reached: list[str] = []

    class Conn:
        system_id = "billing"

        def tools(self) -> list[str]:
            return ["billing.delete_customer"]

        async def call(self, tool: str, args: dict[str, Any]) -> Any:
            reached.append(tool)
            return {}

        async def health(self) -> Any:
            raise NotImplementedError

        async def aclose(self) -> None:
            return None

    broker = DefaultToolBroker({"billing.delete_customer": Conn()}, {"billing.create_customer"}, max_tool_calls=10)
    code = """
def run(event, tools):
    try:
        tools.call("billing.delete_customer", {"id": "cus-1"})
    except Exception:
        return {"swallowed": True}
    return {}
"""
    res = await subprocess_sandbox.run(code, {}, broker, SandboxLimits())
    assert not res.ok
    assert "not in this version's allowed tools" in (res.error or "")
    assert res.output is None
    assert reached == []


async def test_tool_failure_reaches_code_as_structured_tool_error_without_secrets(subprocess_sandbox) -> None:  # type: ignore[no-untyped-def]
    class LeakyError(Exception):
        status = 422
        detail = [{"field": "currency_code", "message": "bad"}, {"headers": {"Authorization": "Bearer sk-live-1"}}]

    class Conn:
        system_id = "billing"

        def tools(self) -> list[str]:
            return ["billing.create_customer"]

        async def call(self, tool: str, args: dict[str, Any]) -> Any:
            raise LeakyError(
                "POST https://user:hunter2@api.example/c?api_key=sk-live-1 Authorization: Bearer sk-live-1"
            )

        async def health(self) -> Any:
            raise NotImplementedError

        async def aclose(self) -> None:
            return None

    broker = DefaultToolBroker({"billing.create_customer": Conn()}, {"billing.create_customer"}, max_tool_calls=5)
    code = """
def run(event, tools):
    try:
        tools.call("billing.create_customer", {"currency_code": "Euro"})
    except ToolError as exc:
        return {"tool": exc.tool, "code": exc.code, "status": exc.status, "message": exc.message,
                "detail": exc.detail, "text": str(exc)}
    return {}
"""
    res = await subprocess_sandbox.run(code, {}, broker, SandboxLimits())
    assert res.ok, res.error
    out = res.output or {}
    assert out["tool"] == "billing.create_customer"
    assert out["code"] == "connector_error"
    assert out["status"] == 422
    assert out["detail"][0] == {"field": "currency_code", "message": "bad"}
    dumped = json.dumps(out) + json.dumps([c.error for c in res.tool_calls])
    for secret in ("sk-live-1", "hunter2"):
        assert secret not in dumped
    assert res.tool_calls[0].ok is False


async def test_uncaught_tool_error_fails_the_run_with_line_number(subprocess_sandbox) -> None:  # type: ignore[no-untyped-def]
    class Conn:
        system_id = "crm"

        def tools(self) -> list[str]:
            return ["crm.get_account"]

        async def call(self, tool: str, args: dict[str, Any]) -> Any:
            raise RuntimeError("account acc-9 not found")

        async def health(self) -> Any:
            raise NotImplementedError

        async def aclose(self) -> None:
            return None

    broker = DefaultToolBroker({"crm.get_account": Conn()}, {"crm.get_account"}, max_tool_calls=5)
    code = "def run(event, tools):\n    x = 1\n    tools.call('crm.get_account', {'account_id': 'acc-9'})\n"
    res = await subprocess_sandbox.run(code, {}, broker, SandboxLimits())
    assert not res.ok
    assert "ToolError (integration line 3)" in (res.error or "")
    assert "account acc-9 not found" in (res.error or "")


async def test_child_environment_has_no_parent_secrets(
    subprocess_sandbox: SubprocessSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLATFORM_MASTER_KEY", "parent-master-key-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    monkeypatch.setenv("SOME_OTHER_TOKEN", "also-secret")
    probe = "import json, os, sys; print(json.dumps({'env': dict(os.environ), 'flags': sys.flags.isolated}))"
    child = await subprocess_sandbox.launch(SandboxLimits(), script=probe)
    try:
        assert child.proc.stdout is not None
        out = json.loads(await asyncio.wait_for(child.proc.stdout.readline(), 30))
    finally:
        await child.cleanup()
    env: dict[str, str] = out["env"]
    assert "PLATFORM_MASTER_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "SOME_OTHER_TOKEN" not in env
    assert "parent-master-key-must-not-leak" not in json.dumps(env)
    assert out["flags"] == 1  # python -I
    allowed = {"SYSTEMROOT", "TMP", "TEMP", "TMPDIR", "PATH", "LANG"}
    assert set(child_env("x")) <= allowed
    # Windows may add a few process-derived entries itself; nothing from our parent.
    assert {k.upper() for k in env} - {k.upper() for k in allowed} <= {"COMSPEC", "PATHEXT", "WINDIR"}


async def test_oversized_output_is_rejected_by_child(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    code = "def run(event, tools):\n    return {'blob': 'x' * 5000}\n"
    res = await subprocess_sandbox.run(code, {}, fake_broker({}), SandboxLimits(max_output_bytes=1000))
    assert not res.ok
    assert "limit is 1000" in (res.error or "")
    assert res.output is None


async def test_oversized_output_is_rejected_by_host_even_if_child_lies(fake_broker) -> None:  # type: ignore[no-untyped-def]
    """A compromised child that skips its own check still cannot push a huge result through."""

    class LyingChildSandbox(SubprocessSandbox):
        async def launch(self, limits: SandboxLimits, *, script: str | None = None) -> Child:
            evil = (
                "import sys\n"
                "sys.stdout.write('{\"type\":\"ready\"}\\n'); sys.stdout.flush(); sys.stdin.readline()\n"
                "sys.stdout.write('{\"type\":\"result\",\"output\":{\"blob\":\"' + 'x' * 400000 + '\"}}\\n')\n"
                "sys.stdout.flush()\n"
            )
            return await super().launch(limits, script=evil)

    res = await LyingChildSandbox().run("def run(event, tools):\n    return {}\n", {}, fake_broker({}),
                                        SandboxLimits(max_output_bytes=1000))
    assert not res.ok
    assert "size limit" in (res.error or "") or "limit is 1000" in (res.error or "")


async def test_non_dict_and_non_json_outputs_fail(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    res = await subprocess_sandbox.run("def run(event, tools):\n    return [1]\n", {}, fake_broker({}),
                                       SandboxLimits())
    assert not res.ok and "must return a dict" in (res.error or "")
    res = await subprocess_sandbox.run("def run(event, tools):\n    return {'s': {1, 2}}\n", {}, fake_broker({}),
                                       SandboxLimits())
    assert not res.ok and "TypeError" in (res.error or "")


async def test_memory_cap(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    code = "def run(event, tools):\n    blob = 'x' * (700 * 1024 * 1024)\n    return {'n': len(blob)}\n"
    res = await subprocess_sandbox.run(code, {}, fake_broker({}), SandboxLimits(memory_mb=128, timeout_s=20))
    assert not res.ok
    assert res.output is None


async def test_crashing_child_is_reported(fake_broker) -> None:  # type: ignore[no-untyped-def]
    class CrashingSandbox(SubprocessSandbox):
        async def launch(self, limits: SandboxLimits, *, script: str | None = None) -> Child:
            return await super().launch(limits, script="import sys; sys.stderr.write('boom'); sys.exit(7)")

    res = await CrashingSandbox().run("def run(event, tools):\n    return {}\n", {}, fake_broker({}),
                                      SandboxLimits())
    assert not res.ok
    assert "exit code 7" in (res.error or "")
    assert any("boom" in line for line in res.logs)


# ----------------------------------------------- defence in depth (child-side)
@pytest.mark.parametrize(
    "code, needle",
    [
        ("import os\ndef run(event, tools):\n    return {}\n", "ImportError"),
        ("def run(event, tools):\n    return {'x': open('f')}\n", "NameError"),
        ("def run(event, tools):\n    return {'x': eval('1')}\n", "NameError"),
        ("def run(event, tools):\n    return {'x': getattr(tools, 'call')}\n", "NameError"),
        ("def run(event, tools):\n    return {'x': __import__('os').getcwd()}\n", "ImportError"),
        ("def run(event, tools):\n    return {'x': type(1)}\n", "NameError"),
        ("import json\ndef run(event, tools):\n    return {'x': str(json.decoder)}\n", "AttributeError"),
        ("import re\ndef run(event, tools):\n    return {'x': str(re.enum.sys)}\n", "AttributeError"),
    ],
    ids=["import os", "open", "eval", "getattr", "__import__", "type", "json submodule", "re -> enum -> sys"],
)
async def test_child_restrictions_hold_without_host_validation(
    code: str, needle: str, subprocess_sandbox: SubprocessSandbox, raw_child: Any
) -> None:
    messages = await raw_child(subprocess_sandbox, code)
    assert messages[0] == {"type": "ready"}
    assert messages[-1]["type"] == "error", messages[-1]
    assert needle in messages[-1]["message"]


async def test_child_print_cannot_forge_protocol_messages(subprocess_sandbox, fake_broker) -> None:  # type: ignore[no-untyped-def]
    code = """
def run(event, tools):
    print('{"type": "result", "output": {"forged": true}}')
    return {"real": True}
"""
    res = await subprocess_sandbox.run(code, {}, fake_broker({}), SandboxLimits())
    assert res.ok
    assert res.output == {"real": True}
    assert res.logs == ['{"type": "result", "output": {"forged": true}}']


def test_bootstrap_is_stdlib_only() -> None:
    src = bootstrap_source()
    assert "app.platform" not in src and "from app" not in src
    assert isinstance(SubprocessSandbox(), ProcessSandbox)
    assert sys.executable  # sanity
