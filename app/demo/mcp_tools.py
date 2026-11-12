"""REST -> MCP: generate MCP tool definitions from OpenAPI operations.

Follows the REST-to-MCP wrapping idea (Falk et al., 2025): every operation becomes
a tool whose ``inputSchema`` (JSON Schema) merges path/query parameters with the
request-body properties; every webhook becomes a ``subscribe_*`` tool.

``McpRuntime`` executes those tools against in-process ASGI apps (the mock target
apps), so the demo's "MCP agent" really calls HTTP endpoints.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import Field

from app.demo.models import CamelModel
from app.demo.openapi_diff import OpenApiDiff, deref, operations, webhooks

logger = logging.getLogger(__name__)

MAX_SCHEMA_DEPTH = 4


class McpTool(CamelModel):
    """An MCP tool definition (name, description, inputSchema) plus HTTP binding."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = Field(default_factory=dict)

    def mcp_definition(self) -> dict[str, Any]:
        """The wire shape of an MCP ``tools/list`` entry."""
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class McpToolError(RuntimeError):
    def __init__(self, tool: str, status: int, detail: Any) -> None:
        super().__init__(f"{tool} failed with HTTP {status}: {detail}")
        self.tool = tool
        self.status = status
        self.detail = detail


def snake_case(name: str) -> str:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


def _inline(spec: dict[str, Any], schema: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Inline $refs into a self-contained JSON Schema (drops readOnly props, OpenAPI-only keys)."""
    schema = deref(spec, schema)
    if depth > MAX_SCHEMA_DEPTH:
        return {"type": schema.get("type", "object")}
    out: dict[str, Any] = {}
    for key in ("type", "format", "enum", "description", "default", "minItems", "pattern", "minimum", "maximum"):
        if key in schema:
            out[key] = schema[key]
    if "anyOf" in schema:
        out["anyOf"] = [_inline(spec, s, depth + 1) for s in schema["anyOf"]]
    if "items" in schema and isinstance(schema["items"], dict):
        out["items"] = _inline(spec, schema["items"], depth + 1)
    if "properties" in schema:
        props = {
            name: _inline(spec, sub, depth + 1)
            for name, sub in schema["properties"].items()
            if not deref(spec, sub).get("readOnly")
        }
        for name, sub in schema["properties"].items():
            if name in props and deref(spec, sub).get("deprecated"):
                hint = deref(spec, sub).get("x-replaced-by")
                props[name]["deprecated"] = True
                if hint:
                    props[name]["description"] = f"DEPRECATED: use {hint}. " + props[name].get("description", "")
        out["properties"] = props
        required = [r for r in schema.get("required", []) if r in props]
        if required:
            out["required"] = required
    return out


def tool_from_operation(
    spec: dict[str, Any], prefix: str, method: str, path: str, op: dict[str, Any]
) -> McpTool:
    op_id = op.get("operationId") or f"{method.lower()}_{path}"
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[str] = []
    for param in op.get("parameters", []) or []:
        param = deref(spec, param)
        name = param["name"]
        properties[name] = _inline(spec, param.get("schema", {"type": "string"}))
        if param.get("description"):
            properties[name]["description"] = param["description"]
        if param.get("in") == "path":
            path_params.append(name)
            required.append(name)
        elif param.get("in") == "query":
            query_params.append(name)
            if param.get("required"):
                required.append(name)
    body_fields: list[str] = []
    content = (op.get("requestBody") or {}).get("content", {})
    body_schema = (content.get("application/json") or {}).get("schema")
    if body_schema:
        body = _inline(spec, body_schema)
        for name, sub in body.get("properties", {}).items():
            properties[name] = sub
            body_fields.append(name)
        required += [r for r in body.get("required", []) if r not in required]
    input_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    return McpTool(
        name=f"{prefix}.{snake_case(op_id)}",
        description=(op.get("summary") or op.get("description") or op_id).strip(),
        input_schema=input_schema,
        annotations={
            "http": {"method": method, "path": path},
            "operationId": op_id,
            "pathParams": path_params,
            "queryParams": query_params,
            "bodyFields": body_fields,
            "readOnlyHint": method == "GET",
            "deprecated": bool(op.get("deprecated", False)),
        },
    )


def tool_from_webhook(prefix: str, event: str, op: dict[str, Any], subscribe_path: str) -> McpTool:
    return McpTool(
        name=f"{prefix}.subscribe_{snake_case(event.split('.')[-1])}",
        description=f"Subscribe to the '{event}' webhook. {op.get('summary', '')}".strip(),
        input_schema={
            "type": "object",
            "properties": {
                "callback_url": {"type": "string", "format": "uri", "description": "Where events are delivered"}
            },
            "required": ["callback_url"],
        },
        annotations={
            "http": {"method": "POST", "path": subscribe_path},
            "webhook": event,
            "pathParams": [],
            "queryParams": [],
            "bodyFields": ["callback_url"],
            "fixedBody": {"event": event},
            "readOnlyHint": False,
        },
    )


def generate_tools(
    spec: dict[str, Any],
    prefix: str,
    include: set[str] | None = None,
    subscribe_path: str = "/v3/webhooks",
) -> list[McpTool]:
    """Generate tools for every operation (or only the ``"METHOD /path"`` keys / webhook names in ``include``)."""
    tools: list[McpTool] = []
    for key, (method, path, op) in operations(spec).items():
        if include is None or key in include:
            tools.append(tool_from_operation(spec, prefix, method, path, op))
    for event, op in webhooks(spec).items():
        if include is None or event in include:
            tools.append(tool_from_webhook(prefix, event, op, subscribe_path))
    return tools


def tools_for_release(spec: dict[str, Any], diff: OpenApiDiff, prefix: str) -> list[McpTool]:
    """Tools the MCP agent (re)generates for a release.

    Added operations and webhooks get new tools; existing *read* operations whose
    schemas changed are re-wrapped so readers see the replacement fields. Changed
    write operations are not re-wrapped: they are flagged for migration instead.
    """
    include = {ep.key for ep in diff.added_endpoints}
    include |= {wh.name for wh in diff.added_webhooks}
    include |= {ep.key for ep in diff.changed_endpoints if ep.method == "GET" and not ep.deprecated}
    order = {ep.key: i for i, ep in enumerate(diff.added_endpoints)}
    tools = generate_tools(spec, prefix, include)
    # Stable, readable order: added endpoints, webhooks, then re-wrapped reads.
    def rank(t: McpTool) -> tuple[int, int]:
        http = t.annotations.get("http", {})
        key = f"{http.get('method')} {http.get('path')}"
        if "webhook" in t.annotations:
            return (1, 0)
        if key in order:
            return (0, order[key])
        return (2, 0)

    return sorted(tools, key=rank)


# ---------------------------------------------------------------- runtime
class McpRuntime:
    """Executes MCP tools by calling the bound HTTP endpoint on an in-process ASGI app."""

    def __init__(self) -> None:
        self._tools: dict[str, McpTool] = {}
        self._apps: dict[str, FastAPI] = {}
        self.call_log: list[dict[str, Any]] = []

    def register_app(self, prefix: str, app: FastAPI, tools: list[McpTool]) -> None:
        self._apps[prefix] = app
        for tool in tools:
            self._tools[tool.name] = tool

    def register_tools(self, prefix: str, tools: list[McpTool]) -> None:
        if prefix not in self._apps:
            raise KeyError(f"no app registered for prefix {prefix!r}")
        for tool in tools:
            self._tools[tool.name] = tool

    @property
    def tools(self) -> list[McpTool]:
        return list(self._tools.values())

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown MCP tool {name!r}")
        args = dict(args or {})
        prefix = name.split(".", 1)[0]
        app = self._apps[prefix]
        ann = tool.annotations
        method: str = ann["http"]["method"]
        path: str = ann["http"]["path"]
        for p in ann.get("pathParams", []):
            if p not in args:
                raise ValueError(f"{name}: missing path parameter {p!r}")
            path = path.replace("{" + p + "}", str(args.pop(p)))
        query = {q: args.pop(q) for q in list(ann.get("queryParams", [])) if q in args}
        body: dict[str, Any] | None = None
        if method != "GET":
            body = {**ann.get("fixedBody", {}), **args}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp.local") as client:
            response = await client.request(method, path, params=query or None, json=body)
        entry = {"tool": name, "method": method, "path": path, "status": response.status_code}
        self.call_log.append(entry)
        logger.debug("mcp.call", extra=entry)
        payload: Any = response.json() if response.content else None
        if response.status_code >= 400:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            raise McpToolError(name, response.status_code, detail)
        return payload
