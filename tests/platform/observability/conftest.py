"""Observability fixtures: capture JSON log lines exactly as the platform emits them."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app.platform.observability.logs import ContextFilter, JsonFormatter


@pytest.fixture
def json_logs() -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """Returns a function giving the JSON log records emitted so far."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter())
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def _records() -> list[dict[str, Any]]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    try:
        yield _records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
