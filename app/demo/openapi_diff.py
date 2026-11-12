"""Structural diff of two OpenAPI 3.x documents.

Detects added/removed operations, added/removed webhooks (OpenAPI 3.1 ``webhooks``
or the 3.0 vendor extension ``x-webhooks``), newly deprecated schema properties
(``deprecated: true``) with replacement hints (``x-replaced-by``), and existing
operations whose request/response schemas changed.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.demo.models import ApiChange, CamelModel

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


class OpenApiError(ValueError):
    """The document is not a usable OpenAPI 3.x document."""


class Endpoint(CamelModel):
    method: str
    path: str
    operation_id: str | None = None
    summary: str = ""
    deprecated: bool = False
    reasons: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


class Webhook(CamelModel):
    name: str
    operation_id: str | None = None
    summary: str = ""


class DeprecatedField(CamelModel):
    schema_name: str
    field: str
    replaced_by: str | None = None
    sunset: str | None = None
    description: str = ""

    @property
    def path(self) -> str:
        return f"{self.schema_name.lower()}.{self.field}"


class OpenApiDiff(CamelModel):
    title: str
    from_version: str
    to_version: str
    added_endpoints: list[Endpoint] = Field(default_factory=list)
    removed_endpoints: list[Endpoint] = Field(default_factory=list)
    changed_endpoints: list[Endpoint] = Field(default_factory=list)
    added_webhooks: list[Webhook] = Field(default_factory=list)
    removed_webhooks: list[Webhook] = Field(default_factory=list)
    deprecated_fields: list[DeprecatedField] = Field(default_factory=list)
    added_schemas: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        parts = [f"+{len(self.added_endpoints)} endpoints", f"+{len(self.added_webhooks)} webhook"
                 + ("s" if len(self.added_webhooks) != 1 else "")]
        if self.removed_endpoints:
            parts.append(f"-{len(self.removed_endpoints)} endpoints")
        n_dep = len(self.deprecated_fields)
        parts.append(f"{n_dep} deprecated field" + ("s" if n_dep != 1 else ""))
        return ", ".join(parts)

    def to_api_changes(self) -> list[ApiChange]:
        changes: list[ApiChange] = []
        for ep in self.added_endpoints:
            changes.append(ApiChange(kind="added", method=ep.method, path=ep.path, summary=ep.summary))  # type: ignore[arg-type]
        for wh in self.added_webhooks:
            changes.append(ApiChange(kind="added", method="WEBHOOK", path=wh.name, summary=wh.summary))
        for ep in self.removed_endpoints:
            changes.append(
                ApiChange(kind="deprecated", method=ep.method, path=ep.path, summary="Removed")  # type: ignore[arg-type]
            )
        for df in self.deprecated_fields:
            summary = f"Replaced by {df.replaced_by}" if df.replaced_by else "Deprecated"
            changes.append(
                ApiChange(kind="deprecated", method="FIELD", path=df.path, summary=summary, replaced_by=df.replaced_by)
            )
        return changes


# ---------------------------------------------------------------- helpers
def _check(spec: dict[str, Any]) -> None:
    version = str(spec.get("openapi", ""))
    if not version.startswith("3."):
        raise OpenApiError(f"expected an OpenAPI 3.x document, got openapi={version!r}")
    if "paths" not in spec or not isinstance(spec["paths"], dict):
        raise OpenApiError("OpenAPI document has no 'paths' object")


def resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise OpenApiError(f"only local $refs are supported, got {ref!r}")
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise OpenApiError(f"unresolvable $ref {ref!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise OpenApiError(f"$ref {ref!r} does not point to an object")
    return node


def deref(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            raise OpenApiError(f"circular $ref {ref!r}")
        seen.add(ref)
        schema = resolve_ref(spec, ref)
    return schema


def operations(spec: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """``{"POST /v3/agreements": (method, path, operation)}``."""
    _check(spec)
    out: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for path, item in spec["paths"].items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                out[f"{method.upper()} {path}"] = (method.upper(), path, op)
    return out


def webhooks(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{"agreement.completed": operation}`` from ``webhooks`` or ``x-webhooks``."""
    raw: dict[str, Any] = {}
    raw.update(spec.get("x-webhooks") or {})
    raw.update(spec.get("webhooks") or {})
    out: dict[str, dict[str, Any]] = {}
    for name, item in raw.items():
        op = next((item[m] for m in HTTP_METHODS if isinstance(item.get(m), dict)), None)
        out[name] = op if op is not None else {}
    return out


def _collect_refs(node: Any, acc: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            acc.add(ref.rsplit("/", 1)[-1])
        for v in node.values():
            _collect_refs(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_refs(v, acc)


def referenced_schemas(spec: dict[str, Any], operation: dict[str, Any]) -> set[str]:
    """Schema names an operation references, transitively."""
    found: set[str] = set()
    _collect_refs(operation, found)
    frontier = list(found)
    schemas = spec.get("components", {}).get("schemas", {})
    while frontier:
        name = frontier.pop()
        nested: set[str] = set()
        _collect_refs(schemas.get(name, {}), nested)
        for n in nested - found:
            found.add(n)
            frontier.append(n)
    return found


def _schema_signature(schema: dict[str, Any]) -> dict[str, tuple[str, bool]]:
    props = schema.get("properties", {}) or {}
    return {
        name: (str(p.get("type", p.get("$ref", ""))), bool(p.get("deprecated", False)))
        for name, p in props.items()
    }


def deprecated_properties(spec: dict[str, Any]) -> list[DeprecatedField]:
    out: list[DeprecatedField] = []
    for schema_name, schema in (spec.get("components", {}).get("schemas", {}) or {}).items():
        for prop, prop_schema in (schema.get("properties", {}) or {}).items():
            if prop_schema.get("deprecated"):
                out.append(
                    DeprecatedField(
                        schema_name=schema_name,
                        field=prop,
                        replaced_by=prop_schema.get("x-replaced-by"),
                        sunset=prop_schema.get("x-sunset"),
                        description=prop_schema.get("description", ""),
                    )
                )
    return out


# ------------------------------------------------------------------- diff
def diff_specs(old: dict[str, Any], new: dict[str, Any]) -> OpenApiDiff:
    old_ops, new_ops = operations(old), operations(new)
    old_hooks, new_hooks = webhooks(old), webhooks(new)

    def endpoint(key: str, ops: dict[str, tuple[str, str, dict[str, Any]]]) -> Endpoint:
        method, path, op = ops[key]
        return Endpoint(
            method=method,
            path=path,
            operation_id=op.get("operationId"),
            summary=op.get("summary", ""),
            deprecated=bool(op.get("deprecated", False)),
        )

    old_schemas = old.get("components", {}).get("schemas", {}) or {}
    new_schemas = new.get("components", {}).get("schemas", {}) or {}
    changed_schemas = {
        name
        for name in set(old_schemas) & set(new_schemas)
        if _schema_signature(deref(old, old_schemas[name])) != _schema_signature(deref(new, new_schemas[name]))
    }

    changed: list[Endpoint] = []
    for key in sorted(set(old_ops) & set(new_ops)):
        reasons = sorted(referenced_schemas(new, new_ops[key][2]) & changed_schemas)
        if new_ops[key][2].get("deprecated") and not old_ops[key][2].get("deprecated"):
            reasons.append("operation deprecated")
        if reasons:
            ep = endpoint(key, new_ops)
            ep.reasons = [f"schema {r} changed" if r != "operation deprecated" else r for r in reasons]
            changed.append(ep)

    old_deprecated = {(d.schema_name, d.field) for d in deprecated_properties(old)}
    newly_deprecated = [d for d in deprecated_properties(new) if (d.schema_name, d.field) not in old_deprecated]

    return OpenApiDiff(
        title=str(new.get("info", {}).get("title", "")),
        from_version=str(old.get("info", {}).get("version", "")),
        to_version=str(new.get("info", {}).get("version", "")),
        added_endpoints=[endpoint(k, new_ops) for k in new_ops if k not in old_ops],
        removed_endpoints=[endpoint(k, old_ops) for k in old_ops if k not in new_ops],
        changed_endpoints=changed,
        added_webhooks=[
            Webhook(name=n, operation_id=new_hooks[n].get("operationId"), summary=new_hooks[n].get("summary", ""))
            for n in new_hooks
            if n not in old_hooks
        ],
        removed_webhooks=[Webhook(name=n) for n in old_hooks if n not in new_hooks],
        deprecated_fields=newly_deprecated,
        added_schemas=sorted(set(new_schemas) - set(old_schemas)),
    )
