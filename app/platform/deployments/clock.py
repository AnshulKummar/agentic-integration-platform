"""Injectable clock so deployment timing (canary windows, watch windows, job
backoff) is testable without sleeping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """A clock that only moves when told to (tests, simulations)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = as_utc(start) if start is not None else datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def set(self, value: datetime) -> None:
        self._now = as_utc(value)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on read; every stored timestamp is UTC, so re-attach it."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
