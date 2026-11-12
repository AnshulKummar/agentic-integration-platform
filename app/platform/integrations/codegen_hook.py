"""Single seam for rendering a version's code and its tool allow-list.

The stored ``IntegrationVersion.code`` is exactly what the sandbox executes and
what PM/Eng approve: the runnable ``def run(event, tools)`` module from
``app.platform.sandbox.codegen``. ``allowed_tools`` is derived from the same
plan, so the broker's allow-list always matches the reviewed code.
"""

from __future__ import annotations

import logging

from app.demo.models import Integration
from app.platform.sandbox.codegen import CodegenError, render_runnable, required_tools

log = logging.getLogger(__name__)

NOT_RUNNABLE_MARKER = "# NOT RUNNABLE"


def render_code(integration: Integration) -> str:
    """Runnable module for review + execution.

    If the draft cannot be rendered yet (e.g. a mapping the generator does not
    support), the stored code is a non-executable stub that states why; the
    sandbox validator rejects it, so such a version can never deploy.
    """
    try:
        return render_runnable(
            integration.mappings,
            name=integration.name,
            source_system=integration.source,
            target_system=integration.target,
            trigger=integration.trigger,
            operations=integration.operations or None,
        )
    except CodegenError as exc:
        log.warning("draft %s is not runnable yet: %s", integration.id, exc)
        return f"{NOT_RUNNABLE_MARKER}: {exc}\n"


def allowed_tools(integration: Integration, fallback: list[str]) -> list[str]:
    """Tools the rendered code calls; ``fallback`` (planner view) when not renderable."""
    try:
        return required_tools(
            integration.mappings, trigger=integration.trigger, operations=integration.operations or None
        )
    except CodegenError:
        return fallback
