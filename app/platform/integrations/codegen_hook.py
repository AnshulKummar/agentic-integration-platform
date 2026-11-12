"""Single seam for rendering a version's code.

For now this is the demo's reviewable rendering (``app.demo.codegen.render``).
The sandbox area is building a runnable renderer (``def run(event, tools)``
contract in ``interfaces.py``); it replaces the body of ``render_code`` at merge.
"""

from __future__ import annotations

from app.demo import codegen
from app.demo.models import Integration


def render_code(integration: Integration) -> str:
    return codegen.render(integration)
