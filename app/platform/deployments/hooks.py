"""Deployment lifecycle hooks (e.g. learning-on-live, owned by the integrations area).

Hooks are delivered through the durable job queue (transactional outbox): the
state change and a ``deployment_hook`` job are committed together, and the
worker invokes the callbacks with retries. A hook that raises makes the job
retry with backoff (up to ``max_attempts``), so hooks must be idempotent.

Wiring at merge (integrations area)::

    from app.platform.deployments.hooks import hooks
    hooks.on_live(my_learning_callback)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal

log = logging.getLogger(__name__)

HookKind = Literal["on_live", "on_rolled_back"]
HOOK_KINDS: tuple[HookKind, ...] = ("on_live", "on_rolled_back")


@dataclass(frozen=True)
class DeploymentEvent:
    kind: HookKind
    tenant_id: str
    deployment_id: str
    integration_id: str
    version_id: str
    previous_version_id: str | None
    reason: str | None = None

    def to_payload(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> DeploymentEvent:
        def _opt(key: str) -> str | None:
            value = payload.get(key)
            return None if value is None else str(value)

        kind = str(payload["kind"])
        if kind not in HOOK_KINDS:
            raise ValueError(f"unknown hook kind {kind!r}")
        return cls(
            kind=kind,
            tenant_id=str(payload["tenant_id"]),
            deployment_id=str(payload["deployment_id"]),
            integration_id=str(payload["integration_id"]),
            version_id=str(payload["version_id"]),
            previous_version_id=_opt("previous_version_id"),
            reason=_opt("reason"),
        )


HookFn = Callable[[DeploymentEvent], Awaitable[None]]


class HookFailed(RuntimeError):
    """One or more lifecycle hooks raised; the hook job will be retried."""


class DeploymentHooks:
    def __init__(self) -> None:
        self._callbacks: dict[HookKind, list[HookFn]] = {kind: [] for kind in HOOK_KINDS}

    def on_live(self, fn: HookFn) -> HookFn:
        """Register (usable as a decorator). Called after a version becomes active."""
        self._callbacks["on_live"].append(fn)
        return fn

    def on_rolled_back(self, fn: HookFn) -> HookFn:
        """Register (usable as a decorator). Called after a deployment is rolled back."""
        self._callbacks["on_rolled_back"].append(fn)
        return fn

    def clear(self) -> None:
        for fns in self._callbacks.values():
            fns.clear()

    def callbacks(self, kind: HookKind) -> tuple[HookFn, ...]:
        return tuple(self._callbacks[kind])

    async def fire(self, event: DeploymentEvent) -> None:
        """Run every callback for ``event.kind``. All run even if one fails; then a
        ``HookFailed`` is raised so the job is retried."""
        errors: list[Exception] = []
        for fn in self.callbacks(event.kind):
            try:
                await fn(event)
            except Exception as exc:
                log.exception(
                    "deployment hook failed",
                    extra={
                        "hook": getattr(fn, "__qualname__", repr(fn)),
                        "hook_kind": event.kind,
                        "deployment_id": event.deployment_id,
                    },
                )
                errors.append(exc)
        if errors:
            raise HookFailed(f"{len(errors)} {event.kind} hook(s) failed: {errors[0]!r}") from errors[0]


#: Process-wide registry. DeploymentDeps uses it unless given another one.
hooks = DeploymentHooks()
