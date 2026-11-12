"""Launcher for the standalone demo (run by path, not with ``-m``).

    python app/demo/run.py serve [--host 127.0.0.1] [--port 8000]
    python app/demo/run.py loop1|loop2|all [--live] [--no-learning] [--json]

Why a launcher: ``app/__init__.py`` imports ``app.core.config`` at import time,
which requires the full platform's dependencies and secrets. Registering a bare
``app`` package stub first lets ``app.demo`` import without that side effect.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parent


def install_app_stub() -> None:
    """Make ``import app.demo...`` skip ``app/__init__.py`` (idempotent)."""
    # Running by path puts app/demo itself on sys.path[0]; drop it so our module
    # names (models, pipeline, ...) cannot shadow third-party top-level imports.
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != Path(here)]
    existing = sys.modules.get("app")
    if existing is not None and getattr(existing, "__file__", None):
        raise RuntimeError("the real 'app' package is already imported; run the demo via app/demo/run.py")
    if existing is None:
        stub = types.ModuleType("app")
        stub.__path__ = [str(APP_DIR)]  # namespace-like package without __init__ side effects
        sys.modules["app"] = stub
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    install_app_stub()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "serve":
        import argparse

        import uvicorn

        parser = argparse.ArgumentParser(prog="run.py serve")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        opts = parser.parse_args(args[1:])
        from app.demo.server import app

        uvicorn.run(app, host=opts.host, port=opts.port, log_level="info")
        return 0
    from app.demo.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
