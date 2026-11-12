"""HTTP routes for the evals area. OWNED BY: evals area (E).

GET  /v1/evals/runs        list runs (any authenticated user), newest first
GET  /v1/evals/runs/{id}   one run with the full report
POST /v1/evals/runs        admin only; runs deterministic or kg_learning and persists it.
                           live_llm is CLI-only (it spends money and needs an API key).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.deps import get_session
from app.platform.errors import NotFound, ValidationFailed
from app.platform.evals.models import EvalRun
from app.platform.evals.runner import run_eval
from app.platform.evals.synthetic import DEFAULT_SEED
from app.platform.principal import Principal, Role, current_principal, require_roles

router = APIRouter(tags=["evals"])

Session = Annotated[AsyncSession, Depends(get_session)]
AnyUser = Annotated[Principal, Depends(current_principal)]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]

API_PREDICTORS = ("deterministic", "kg_learning")
SUITES = ("synthetic", "handlabeled", "all")


class EvalRunCreate(BaseModel):
    suite: str = "all"
    predictor: str = "deterministic"
    seed: int = Field(default=DEFAULT_SEED, ge=0, le=2**31 - 1)


class EvalRunOut(BaseModel):
    id: str
    suite: str
    predictor: str
    seed: int
    git_sha: str | None
    summary: dict[str, Any]
    created_by: str | None
    created_at: datetime


class EvalRunDetail(EvalRunOut):
    metrics: dict[str, Any]


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    keys = ("cases", "f1", "precision", "recall", "edits_per_case", "reuse_rate", "ece")
    out: dict[str, Any] = {name: {k: res["metrics"][k] for k in keys} for name, res in report["results"].items()}
    return out


def _out(run: EvalRun) -> EvalRunOut:
    return EvalRunOut(id=run.id, suite=run.suite, predictor=run.predictor, seed=run.seed, git_sha=run.git_sha,
                      summary=run.summary, created_by=run.created_by, created_at=run.created_at)


@router.get("/v1/evals/runs", response_model=list[EvalRunOut])
async def list_runs(
    _: AnyUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EvalRunOut]:
    rows = await session.execute(
        select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id).limit(limit).offset(offset)
    )
    return [_out(r) for r in rows.scalars()]


@router.get("/v1/evals/runs/{run_id}", response_model=EvalRunDetail)
async def get_run(run_id: str, _: AnyUser, session: Session) -> EvalRunDetail:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise NotFound(f"eval run {run_id} not found")
    return EvalRunDetail(**_out(run).model_dump(), metrics=run.metrics)


@router.post("/v1/evals/runs", response_model=EvalRunDetail, status_code=201)
async def create_run(
    body: EvalRunCreate, principal: Admin, session: Session
) -> EvalRunDetail:
    if body.predictor not in API_PREDICTORS:
        raise ValidationFailed(
            f"predictor must be one of {', '.join(API_PREDICTORS)} (live_llm runs only via the CLI)"
        )
    if body.suite not in SUITES:
        raise ValidationFailed(f"suite must be one of {', '.join(SUITES)}")
    # CPU-bound and synchronous: keep it off the event loop.
    report = await asyncio.to_thread(run_eval, body.suite, body.predictor, seed=body.seed)  # type: ignore[arg-type]
    run = EvalRun(suite=body.suite, predictor=body.predictor, seed=body.seed, git_sha=report.get("git_sha"),
                  summary=_summary(report), metrics=report, created_by=principal.user_id)
    session.add(run)
    await session.commit()
    return EvalRunDetail(**_out(run).model_dump(), metrics=run.metrics)
