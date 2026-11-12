"""AST validation: escape attempts are rejected, generated code is accepted."""

from __future__ import annotations

import pytest

from app.platform.errors import ValidationFailed
from app.platform.sandbox.validate import CodeRejected, find_violations, validate_code

OK_RUN = "def run(event, tools):\n    return {}\n"


def wrap(body: str) -> str:
    """Put ``body`` inside run()."""
    lines = "\n".join("    " + line for line in body.strip().splitlines())
    return f"def run(event, tools):\n{lines}\n    return {{}}\n"


ESCAPES = {
    "import os": "import os",
    "import subprocess": "import subprocess",
    "import socket": "import socket",
    "from os import system": "from os import system",
    "import sys": "import sys",
    "import builtins": "import builtins",
    "import importlib": "import importlib",
    "import ctypes": "import ctypes",
    "relative import": "from . import x",
    "submodule import": "import json.decoder",
    "private from json": "from json import _default_decoder",
    "star import": "from math import *",
    "open": "open('C:/secrets.txt').read()",
    "eval": "eval('1+1')",
    "exec": "exec('x=1')",
    "compile": "compile('1', 'f', 'eval')",
    "__import__": "__import__('os')",
    "globals": "g = globals()",
    "locals": "l = locals()",
    "vars": "v = vars()",
    "getattr": "getattr(tools, 'call')",
    "setattr": "setattr(tools, 'call', None)",
    "delattr": "delattr(tools, 'call')",
    "input": "input()",
    "breakpoint": "breakpoint()",
    "type()": "t = type(event)",
    "object": "o = object",
    "dunder class chain": "().__class__.__bases__[0].__subclasses__()",
    "dunder via subscript": "x = [].__class__",
    "dunder name": "__builtins__['eval']('1')",
    "dunder globals of prelude fn": "geocode.__globals__",
    "dunder in f-string": "s = f'{event.__class__}'",
    "private attribute": "tools._secret",
    "str.format attribute walk": "'{0.__class__}'.format(event)",
    "format_map": "'{x}'.format_map(event)",
    "generator frame walk": "g = (x for x in [1]); g.gi_frame.f_back",
    "frame globals": "e = None; e.tb_frame.f_globals",
    "code object": "run.co_code",
    "mro": "x = int.mro()",
    "attribute assignment": "tools.call = None",
    "attribute deletion": "del tools.call",
    "yield": "yield 1",
    "lambda dunder default": "f = lambda __x: __x",
    "with": "with x:\n    pass",
    "match class pattern": "match event:\n    case dict(__class__=c):\n        pass",
    "global": "global X",
    "dunder keyword": "dict(__class__=1)",
}


@pytest.mark.parametrize("snippet", list(ESCAPES.values()), ids=list(ESCAPES.keys()))
def test_escape_attempts_are_rejected(snippet: str) -> None:
    code = snippet + "\n" + OK_RUN if snippet.startswith(("import", "from")) else wrap(snippet)
    with pytest.raises(CodeRejected) as err:
        validate_code(code)
    assert err.value.violations
    assert isinstance(err.value, ValidationFailed)
    assert err.value.status == 422


@pytest.mark.parametrize(
    "code",
    [
        "class Evil:\n    pass\n" + OK_RUN,
        "async def run(event, tools):\n    return {}\n",
        "def run(event):\n    return {}\n",
        "def run(event, tools, extra=1):\n    return {}\n",
        "def run(*args):\n    return {}\n",
        "def helper():\n    return 1\n",
        OK_RUN + OK_RUN,
        "print('top-level side effect')\n" + OK_RUN,
        "for i in range(3):\n    pass\n" + OK_RUN,
        "@decorator\n" + OK_RUN,
        "def _private():\n    return 1\n" + OK_RUN,
        "def run(event, tools)\n    return {}\n",
    ],
    ids=[
        "class", "async run", "missing tools param", "extra param", "varargs", "no run", "two runs",
        "top-level call", "top-level loop", "decorator", "private helper", "syntax error",
    ],
)
def test_module_shape_is_enforced(code: str) -> None:
    assert find_violations(code)


def test_all_violations_are_reported_at_once() -> None:
    code = "import os\n" + wrap("eval('1')\nx = ().__class__\nopen('f')")
    with pytest.raises(CodeRejected) as err:
        validate_code(code)
    text = " ".join(err.value.violations)
    for needle in ("'os'", "'eval'", "'__class__'", "'open'"):
        assert needle in text
    assert len(err.value.violations) >= 4
    assert "line" in err.value.violations[0]


def test_size_limit() -> None:
    code = OK_RUN + "# " + "x" * 70_000 + "\n"
    assert "limit" in find_violations(code)[0]
    assert find_violations(OK_RUN, max_bytes=10)


def test_accepts_helpers_constants_allowed_imports_and_control_flow() -> None:
    code = '''"""Docstring."""
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
import re

RATE = 0.19
CODES = {"EUR": 1}


def total(amount, rate=RATE):
    return round(amount * (1 + rate), 2)


def run(event, tools):
    n = 0
    while n < 3:
        n += 1
    items = [x * 2 for x in range(n) if x]
    try:
        tools.call("a.b", {"k": json.dumps(items)})
    except ToolError as exc:
        print("failed", exc.code)
    out = {"n": n, "t": total(10.0), "s": math.sqrt(4), "d": str(Decimal("1.5"))}
    out["when"] = datetime.now(timezone.utc).isoformat()
    out["m"] = bool(re.match(r"^a", "abc"))
    out["f"] = f"{n}-{out['t']}"
    return out
'''
    assert find_violations(code) == []


async def test_generated_demo_code_is_accepted(demo_integrations) -> None:  # type: ignore[no-untyped-def]
    from app.platform.sandbox.codegen import render_runnable

    for integ in (demo_integrations.loop1, demo_integrations.loop2, demo_integrations.loop1_draft):
        code = render_runnable(
            [r.to_json() for r in integ.mappings],
            name=integ.name, source_system=integ.source, target_system=integ.target, trigger=integ.trigger,
        )
        assert find_violations(code) == []
