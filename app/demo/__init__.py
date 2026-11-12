"""Self-contained PMAIP demo backend (no external databases).

Note: the repository's ``app/__init__.py`` eagerly imports ``app.core.config``
(which needs pydantic-settings and DB/API secrets). The demo therefore runs via
``app/demo/run.py``, which registers a lightweight ``app`` namespace stub before
importing ``app.demo`` so none of the platform's config or database layer loads.
"""
