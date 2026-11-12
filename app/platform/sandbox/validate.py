"""Static (AST) validation of integration code before it is ever executed.

Generated code is reviewed by humans, but it is still untrusted input to the
sandbox: mapping rows come from an LLM and from people. Validation is the first
of three layers (validation -> restricted builtins in the child -> OS isolation
of the child process / container).

Accepted module shape::

    # comments, an optional docstring
    import json                      # only ALLOWED_IMPORTS
    CONSTANT = {...}                 # top-level assignments
    def helper(x): ...               # helper functions
    def run(event, tools): ...       # exactly one, exactly these two parameters

Rejected anywhere: classes, async code, generators (``yield``), ``global`` /
``nonlocal``, ``with``, ``match`` (class patterns read attributes by name),
decorators, attribute writes, any name or attribute starting with ``_``
(blocks ``().__class__.__subclasses__()`` style escapes), introspection
attributes that walk to frames or code objects (``gi_frame``, ``f_globals``,
``tb_frame``, ``co_code`` ...), ``str.format``/``format_map`` (format strings can
read attributes), and the dangerous builtins in ``FORBIDDEN_NAMES``.

Infinite loops are not detected here: the sandbox's wall-clock timeout handles them.
"""

from __future__ import annotations

import ast

from app.platform.errors import ValidationFailed

ALLOWED_IMPORTS: frozenset[str] = frozenset({"json", "math", "re", "datetime", "decimal"})

FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "input",
        "breakpoint",
        "help",
        "exit",
        "quit",
        "memoryview",
        "type",
        "object",
        "super",
        "classmethod",
        "staticmethod",
        "property",
        "id",
        "__builtins__",
        "__loader__",
        "__spec__",
    }
)

#: Attribute names that reach interpreter internals without using underscores.
FORBIDDEN_ATTRS: frozenset[str] = frozenset({"format", "format_map", "mro", "func_globals", "func_code"})
FORBIDDEN_ATTR_PREFIXES: tuple[str, ...] = ("_", "gi_", "cr_", "ag_", "f_", "tb_", "co_")

FORBIDDEN_NODES: dict[type[ast.AST], str] = {
    ast.ClassDef: "class definitions",
    ast.AsyncFunctionDef: "async functions",
    ast.Await: "await",
    ast.AsyncFor: "async for",
    ast.AsyncWith: "async with",
    ast.Yield: "yield (generators)",
    ast.YieldFrom: "yield from",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
    ast.With: "with statements",
    ast.Match: "match statements",
}

MAX_CODE_BYTES = 64_000
MAX_AST_NODES = 20_000
RUN_SIGNATURE = ("event", "tools")


class CodeRejected(ValidationFailed):
    """Code failed static validation. ``violations`` lists every problem found."""

    code = "code_rejected"

    def __init__(self, violations: list[str]) -> None:
        self.violations = list(violations)
        shown = "; ".join(violations[:20])
        more = f" (+{len(violations) - 20} more)" if len(violations) > 20 else ""
        super().__init__(f"integration code rejected: {shown}{more}")


def _where(node: ast.AST) -> str:
    line = getattr(node, "lineno", None)
    return f"line {line}" if line is not None else "module"


def _bad_name(name: str) -> bool:
    return name in FORBIDDEN_NAMES or name.startswith("__")


def _bad_attr(attr: str) -> bool:
    return attr in FORBIDDEN_ATTRS or attr.startswith(FORBIDDEN_ATTR_PREFIXES)


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def bad(self, node: ast.AST, message: str) -> None:
        self.violations.append(f"{_where(node)}: {message}")

    # -- generic -------------------------------------------------------------
    def generic_visit(self, node: ast.AST) -> None:
        label = FORBIDDEN_NODES.get(type(node))
        if label is not None:
            self.bad(node, f"{label} are not allowed")
        super().generic_visit(node)

    # -- names ---------------------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if _bad_name(node.id):
            self.bad(node, f"name {node.id!r} is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _bad_attr(node.attr):
            self.bad(node, f"attribute {node.attr!r} is not allowed")
        if not isinstance(node.ctx, ast.Load):
            self.bad(node, "assigning or deleting attributes is not allowed")
        self.generic_visit(node)

    def _check_args(self, node: ast.AST, args: ast.arguments) -> None:
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg is not None:
            every.append(args.vararg)
        if args.kwarg is not None:
            every.append(args.kwarg)
        for arg in every:
            if _bad_name(arg.arg):
                self.bad(node, f"parameter name {arg.arg!r} is not allowed")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if _bad_name(node.name) or node.name.startswith("_"):
            self.bad(node, f"function name {node.name!r} is not allowed")
        if node.decorator_list:
            self.bad(node, "decorators are not allowed")
        self._check_args(node, node.args)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._check_args(node, node.args)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None and _bad_name(node.name):
            self.bad(node, f"name {node.name!r} is not allowed")
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg is not None and node.arg.startswith("__"):
            self.bad(node, f"keyword {node.arg!r} is not allowed")
        self.generic_visit(node)

    # -- imports -------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_IMPORTS:
                self.bad(node, f"import of {alias.name!r} is not allowed (allowed: {sorted(ALLOWED_IMPORTS)})")
            if alias.asname is not None and _bad_name(alias.asname):
                self.bad(node, f"import alias {alias.asname!r} is not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level != 0 or node.module not in ALLOWED_IMPORTS:
            self.bad(node, f"import from {node.module!r} is not allowed (allowed: {sorted(ALLOWED_IMPORTS)})")
        for alias in node.names:
            if alias.name == "*" or alias.name.startswith("_"):
                self.bad(node, f"importing {alias.name!r} is not allowed")
            if alias.asname is not None and _bad_name(alias.asname):
                self.bad(node, f"import alias {alias.asname!r} is not allowed")


_TOP_LEVEL_OK = (ast.FunctionDef, ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)


def _check_module_shape(tree: ast.Module) -> list[str]:
    problems: list[str] = []
    runs: list[ast.FunctionDef] = []
    for i, stmt in enumerate(tree.body):
        is_docstring = (
            i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
        if is_docstring:
            continue
        if not isinstance(stmt, _TOP_LEVEL_OK):
            problems.append(
                f"{_where(stmt)}: top-level {type(stmt).__name__} is not allowed "
                "(only imports, constants and function definitions)"
            )
            continue
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "run":
            runs.append(stmt)
    if not runs:
        problems.append("module: must define 'def run(event, tools)'")
    elif len(runs) > 1:
        problems.append(f"{_where(runs[1])}: 'run' is defined more than once")
    else:
        args = runs[0].args
        names = tuple(a.arg for a in [*args.posonlyargs, *args.args])
        extras = args.vararg or args.kwarg or args.kwonlyargs or args.defaults or args.kw_defaults
        if names != RUN_SIGNATURE or extras:
            problems.append(f"{_where(runs[0])}: run must have exactly the signature 'def run(event, tools)'")
    return problems


def find_violations(code: str, *, max_bytes: int = MAX_CODE_BYTES) -> list[str]:
    """Every reason ``code`` may not run (empty list = acceptable)."""
    size = len(code.encode("utf-8"))
    if size > max_bytes:
        return [f"module: code is {size} bytes, limit is {max_bytes}"]
    if "\x00" in code:
        return ["module: code contains NUL bytes"]
    try:
        tree = ast.parse(code, filename="<integration>", mode="exec")
    except SyntaxError as exc:
        return [f"line {exc.lineno}: syntax error: {exc.msg}"]
    nodes = sum(1 for _ in ast.walk(tree))
    if nodes > MAX_AST_NODES:
        return [f"module: code has {nodes} AST nodes, limit is {MAX_AST_NODES}"]
    checker = _Checker()
    checker.visit(tree)
    return _check_module_shape(tree) + checker.violations


def validate_code(code: str, *, max_bytes: int = MAX_CODE_BYTES) -> None:
    """Raise :class:`CodeRejected` (a ``ValidationFailed``) listing all violations."""
    violations = find_violations(code, max_bytes=max_bytes)
    if violations:
        raise CodeRejected(violations)
