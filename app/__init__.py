"""
Agentic Integration Platform

Production-grade AI-powered integration platform implementing the agentic integration paradigm.
Transforms natural language specifications into production-ready integration code using advanced
AI models, knowledge graphs, and semantic validation.
"""

from typing import Any

__version__ = "2.0.0"
__author__ = "Shiv Yadav"
__email__ = "shiv@example.com"
__license__ = "MIT"

__all__ = ["settings"]


def __getattr__(name: str) -> Any:
    # Lazy: importing app.core.config needs the legacy stack's secrets, so only
    # load it when someone actually asks for ``app.settings``. This lets
    # ``app.demo`` and ``app.platform`` import without that side effect.
    if name == "settings":
        from app.core.config import settings

        return settings
    raise AttributeError(f"module 'app' has no attribute {name!r}")
