"""Log redaction: masks revealed secret values and common credential patterns.

``SECRET_REGISTRY`` holds the values this process has stored or revealed (the
store registers them automatically). Values live only in memory, in a bounded
LRU, and are matched by substring. ``redact(text)`` also masks Bearer/Basic
tokens and ``api_key=...`` / ``"client_secret": "..."``-style pairs.

``install_redaction()`` attaches ``RedactionFilter`` to the root logger and to
every handler on it. Logger-level filters do not see records propagated from
child loggers, so handler-level installation is what makes it effective; call
it again (idempotent) after adding handlers.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict

MASK = "***REDACTED***"
#: Values shorter than this are not registered (masking "1" everywhere is noise, not safety).
MIN_SECRET_LENGTH = 6

_CREDENTIAL_KEYS = (
    r"api[_-]?key|apikey|x-api-key|client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token"
    r"|password|passwd|secret|token|authorization"
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]+=*"), r"\1 " + MASK),
    (
        re.compile(rf"(?i)([\"']?(?:{_CREDENTIAL_KEYS})[\"']?\s*[:=]\s*[\"']?)(?!\*\*\*)([^\s\"'&,;}}\]]+)"),
        r"\1" + MASK,
    ),
)


class SecretRegistry:
    """Bounded, thread-safe set of secret values to mask in logs."""

    def __init__(self, max_entries: int = 4096) -> None:
        self._values: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self._pattern: re.Pattern[str] | None = None

    def register(self, value: str) -> None:
        if len(value) < MIN_SECRET_LENGTH:
            return
        with self._lock:
            if value in self._values:
                self._values.move_to_end(value)
                return
            self._values[value] = None
            while len(self._values) > self._max:
                self._values.popitem(last=False)
            self._pattern = None

    def forget(self, value: str) -> None:
        with self._lock:
            if value in self._values:
                del self._values[value]
                self._pattern = None

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._pattern = None

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"SecretRegistry(entries={len(self._values)})"

    def _compiled(self) -> re.Pattern[str] | None:
        with self._lock:
            if self._pattern is None and self._values:
                # Longest first so a value containing another is masked whole.
                ordered = sorted(self._values, key=len, reverse=True)
                self._pattern = re.compile("|".join(re.escape(v) for v in ordered))
            return self._pattern

    def mask(self, text: str) -> str:
        pattern = self._compiled()
        return pattern.sub(MASK, text) if pattern is not None else text


SECRET_REGISTRY = SecretRegistry()


def redact(text: str, registry: SecretRegistry | None = None) -> str:
    """Mask registered secret values and credential-looking substrings."""
    out = (registry or SECRET_REGISTRY).mask(text)
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


_STD_ATTRS = frozenset(vars(logging.LogRecord("x", logging.INFO, "x", 0, "", None, None))) | {"message", "asctime"}


class RedactionFilter(logging.Filter):
    """Rewrites the record in place (message, string extras, exception text); never drops it."""

    def __init__(self, registry: SecretRegistry | None = None) -> None:
        super().__init__()
        self.registry = registry or SECRET_REGISTRY

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message, self.registry)
        if cleaned != message or record.args:
            record.msg = cleaned
            record.args = None
        for key, value in list(vars(record).items()):
            if key not in _STD_ATTRS and isinstance(value, str):
                setattr(record, key, redact(value, self.registry))
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self.registry)
        return True


def install_redaction(logger: logging.Logger | None = None, registry: SecretRegistry | None = None) -> RedactionFilter:
    """Attach one RedactionFilter to ``logger`` (default root) and all its handlers. Idempotent."""
    target = logger or logging.getLogger()
    existing = next((f for f in target.filters if isinstance(f, RedactionFilter)), None)
    flt = existing or RedactionFilter(registry)
    if existing is None:
        target.addFilter(flt)
    for handler in target.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(flt)
    return flt
