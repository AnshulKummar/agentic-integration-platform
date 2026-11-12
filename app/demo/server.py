"""Standalone FastAPI app for the PMAIP demo (no databases, no app.core config).

Run:  python app/demo/run.py serve --port 8000
(``uvicorn app.demo.server:app`` also works once ``app/__init__.py`` no longer
imports ``app.core.config`` eagerly; see app/demo/run.py.)

Endpoints live under ``/demo``; the four mock target apps are under ``/mock``.
Every mutating endpoint returns the trace events it produced.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.demo.models import CamelModel, HumanEdit
from app.demo.orchestrator import LOOP1_INTENT, LOOP1_REFINE, DemoError, DemoOrchestrator

logger = logging.getLogger(__name__)

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://anshulkummar.github.io",
]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


class DemoSession:
    """One orchestrator per server process, serialised by a lock."""

    def __init__(self) -> None:
        mode = os.environ.get("DEMO_MODE", "scripted")
        self.orch = DemoOrchestrator(
            mode="live" if mode == "live" else "scripted",
            learning_enabled=_env_flag("DEMO_LEARNING_ENABLED", True),
        )
        self.lock = asyncio.Lock()

    async def run(self, fn: Callable[[DemoOrchestrator], Awaitable[Any]]) -> dict[str, Any]:
        async with self.lock:
            start = len(self.orch.trace)
            result = await fn(self.orch)
            return {"result": result, "trace": self.orch.trace_json(start)}


session = DemoSession()

app = FastAPI(
    title="PMAIP demo backend",
    description="Agentic integration of platform APIs: LLM + KG + MCP agents (DSI 2026 demo).",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"], allow_credentials=False
)


@app.exception_handler(DemoError)
async def demo_error_handler(_: Request, exc: DemoError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": str(exc)})


async def mock_dispatch(scope: Scope, receive: Receive, send: Send) -> None:
    """Forward /mock/* to the current orchestrator's mock apps (survives /demo/reset)."""
    await session.orch.apps.root(scope, receive, send)


app.mount("/mock", mock_dispatch)


# ------------------------------------------------------------------ bodies
class TextBody(CamelModel):
    text: str


class RefineBody(CamelModel):
    text: str = LOOP1_REFINE
    integration_id: str | None = None


class IntentBody(CamelModel):
    text: str = LOOP1_INTENT


class ResetBody(CamelModel):
    learning_enabled: bool | None = None
    mode: Literal["scripted", "live"] | None = None


class ReviewBody(CamelModel):
    edits: list[HumanEdit] | None = None


class ApproveBody(CamelModel):
    role: str
    by: str
    comment: str = ""


# --------------------------------------------------------------- endpoints
@app.get("/demo/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "mode": session.orch.mode, "learningEnabled": session.orch.learning_enabled}


@app.get("/demo/state")
async def state() -> dict[str, Any]:
    async with session.lock:
        return session.orch.state()


@app.post("/demo/reset")
async def reset(body: ResetBody | None = None) -> dict[str, Any]:
    body = body or ResetBody()
    async with session.lock:
        session.orch.reset(mode=body.mode, learning_enabled=body.learning_enabled)
        return session.orch.state()


@app.post("/demo/loop1/intent")
async def loop1_intent(body: IntentBody | None = None) -> dict[str, Any]:
    text = (body or IntentBody()).text

    async def go(o: DemoOrchestrator) -> Any:
        return (await o.loop1_intent(text)).to_json()

    out = await session.run(go)
    return {"integration": out["result"], "trace": out["trace"], "chat": [c.to_json() for c in session.orch.chat]}


@app.post("/demo/loop1/refine")
async def loop1_refine(body: RefineBody | None = None) -> dict[str, Any]:
    body = body or RefineBody()

    async def go(o: DemoOrchestrator) -> Any:
        return (await o.loop1_refine(body.text, body.integration_id)).to_json()

    out = await session.run(go)
    return {"integration": out["result"], "trace": out["trace"], "chat": [c.to_json() for c in session.orch.chat]}


@app.post("/demo/integrations/{integration_id}/review")
async def review(integration_id: str, body: ReviewBody | None = None) -> dict[str, Any]:
    edits = (body or ReviewBody()).edits

    async def go(o: DemoOrchestrator) -> Any:
        return (await o.review(integration_id, edits)).to_json()

    out = await session.run(go)
    return {"integration": out["result"], "trace": out["trace"]}


@app.post("/demo/api-releases/check")
async def check_releases() -> dict[str, Any]:
    async def go(o: DemoOrchestrator) -> Any:
        return [r.to_json() for r in await o.check_api_releases()]

    out = await session.run(go)
    return {
        "releases": out["result"],
        "roadmap": [r.to_json() for r in session.orch.roadmap.values()],
        "trace": out["trace"],
    }


@app.post("/demo/opportunities/{roadmap_id}/draft")
async def draft(roadmap_id: str) -> dict[str, Any]:
    async def go(o: DemoOrchestrator) -> Any:
        return (await o.draft_opportunity(roadmap_id)).to_json()

    out = await session.run(go)
    return {"integration": out["result"], "trace": out["trace"], "chat": [c.to_json() for c in session.orch.chat]}


@app.post("/demo/integrations/{integration_id}/approve")
async def approve(integration_id: str, body: ApproveBody) -> dict[str, Any]:
    async def go(o: DemoOrchestrator) -> Any:
        return o.approve(integration_id, body.role, body.by, body.comment).to_json()

    out = await session.run(go)
    return {"integration": out["result"], "trace": out["trace"]}


@app.post("/demo/integrations/{integration_id}/deploy")
async def deploy(integration_id: str) -> dict[str, Any]:
    async def go(o: DemoOrchestrator) -> Any:
        return (await o.deploy(integration_id)).to_json()

    out = await session.run(go)
    return {"integration": out["result"], "trace": out["trace"]}


@app.post("/demo/run/{loop}")
async def run_loop(loop: Literal["loop1", "loop2", "all"]) -> dict[str, Any]:
    """Convenience: run a whole loop non-interactively (scripted approvals)."""

    async def go(o: DemoOrchestrator) -> Any:
        if loop in ("loop1", "all"):
            await o.run_loop1()
        if loop in ("loop2", "all"):
            await o.run_loop2()
        return o.metrics()

    out = await session.run(go)
    return {"metrics": out["result"], "trace": out["trace"]}


@app.get("/demo/trace")
async def trace(since: int = 0) -> dict[str, Any]:
    async with session.lock:
        return {"events": session.orch.trace_json(since), "total": len(session.orch.trace)}


@app.get("/demo/mcp/tools")
async def mcp_tools() -> dict[str, Any]:
    """Current MCP tool catalogue (MCP ``tools/list`` shape)."""
    async with session.lock:
        return {"tools": [t.mcp_definition() for t in session.orch.mcp.tools]}
