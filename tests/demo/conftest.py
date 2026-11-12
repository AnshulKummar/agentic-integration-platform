"""Demo test setup: import ``app.demo`` without running ``app/__init__.py``.

``app/__init__.py`` eagerly imports ``app.core.config`` (pydantic-settings + DB
secrets). ``app/demo/run.py`` registers a bare ``app`` package stub first.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pmaip_demo_run", REPO_ROOT / "app" / "demo" / "run.py")
assert _spec is not None and _spec.loader is not None
_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_run)
_run.install_app_stub()

from app.demo.kg_store import KnowledgeGraph, load_fixture_json, load_seed  # noqa: E402
from app.demo.orchestrator import DemoOrchestrator  # noqa: E402


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests are deterministic: never reach the real API."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def seed() -> dict:
    return load_seed()


@pytest.fixture
def kg(seed: dict) -> KnowledgeGraph:
    return KnowledgeGraph.from_seed(seed)


@pytest.fixture
def spec_v2() -> dict:
    return load_fixture_json("esign_openapi_v2_4.json")


@pytest.fixture
def spec_v3() -> dict:
    return load_fixture_json("esign_openapi_v3_0.json")


@pytest.fixture
def orch() -> DemoOrchestrator:
    return DemoOrchestrator(mode="scripted", learning_enabled=True)


@pytest.fixture
async def after_loop1() -> DemoOrchestrator:
    o = DemoOrchestrator(mode="scripted", learning_enabled=True)
    await o.run_loop1()
    return o


@pytest.fixture
async def after_release() -> DemoOrchestrator:
    o = DemoOrchestrator(mode="scripted", learning_enabled=True)
    await o.run_loop1()
    await o.check_api_releases()
    return o
