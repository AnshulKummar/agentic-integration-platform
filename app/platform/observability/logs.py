"""Structured logging.

JSON lines with ``timestamp, level, logger, message, request_id, tenant_id``
plus any ``extra={...}`` fields. Extra keys that look like credentials are
redacted (defence in depth; callers must still never log secret values).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.platform.observability.context import current_context

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None)).keys()
    | {"message", "asctime", "taskName"}
)
_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|authorization|api[_-]?key|cookie|credential)", re.IGNORECASE)
REDACTED = "[redacted]"
_HANDLER_MARK = "_pmaip_handler"


def _jsonable(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): (REDACTED if _SECRET_KEY.search(str(k)) else _jsonable(v)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    return str(value)


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_ATTRS or key.startswith("_"):
            continue
        out[key] = REDACTED if _SECRET_KEY.search(key) else _jsonable(value)
    return out


class ContextFilter(logging.Filter):
    """Copies request_id / tenant_id from the request context onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = current_context()
        if not getattr(record, "request_id", None):
            record.request_id = ctx.request_id if ctx else None
        if not getattr(record, "tenant_id", None):
            record.tenant_id = ctx.tenant_id if ctx else None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "tenant_id": getattr(record, "tenant_id", None),
        }
        for key, value in extra_fields(record).items():
            payload.setdefault(key, value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [rid=%(request_id)s tenant=%(tenant_id)s] %(message)s")


def configure_logging(level: str = "INFO", *, json_lines: bool = True) -> logging.Handler:
    """Install (or replace) the platform's stdout handler on the root logger.

    Idempotent; handlers added by others (e.g. pytest's caplog) are left alone.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if json_lines else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    return handler
