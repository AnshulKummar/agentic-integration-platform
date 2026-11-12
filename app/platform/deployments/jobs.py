"""Durable job queue on the ``jobs`` table.

Claiming is exclusive across worker processes:
- PostgreSQL: ``SELECT ... FOR UPDATE SKIP LOCKED`` then mark running, in one transaction.
- SQLite: a guarded ``UPDATE ... WHERE id = :id AND <still claimable>`` per
  candidate; ``rowcount == 1`` means this worker won the race.

A claim takes a lease (``locked_until``). A job whose worker died is reclaimed
after the lease expires. Completion/failure updates are guarded by
``locked_by`` so a worker that lost its lease cannot overwrite the new owner.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import Database
from app.platform.deployments.models import Job, JobStatus

JOB_ADVANCE = "advance_deployment"
JOB_WATCH = "health_watch"
JOB_HOOK = "deployment_hook"

MAX_BACKOFF_S = 300.0
BASE_BACKOFF_S = 2.0
MAX_ERROR_CHARS = 2000


def enqueue(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict[str, Any],
    tenant_id: str | None,
    run_at: datetime,
    max_attempts: int = 5,
) -> Job:
    """Stage a job in the caller's transaction (outbox pattern)."""
    job = Job(
        kind=kind,
        payload=payload,
        tenant_id=tenant_id,
        run_at=run_at,
        status=JobStatus.QUEUED,
        max_attempts=max_attempts,
    )
    session.add(job)
    return job


def backoff_s(attempts: int) -> float:
    """Exponential backoff after the ``attempts``-th failure: 2, 4, 8, ... capped at 5 min."""
    return float(min(MAX_BACKOFF_S, BASE_BACKOFF_S ** max(1, attempts)))


def _claimable(now: datetime) -> ColumnElement[bool]:
    return or_(
        and_(Job.status == JobStatus.QUEUED, Job.run_at <= now),
        # Lease expired: the worker holding it died or hung.
        and_(Job.status == JobStatus.RUNNING, Job.locked_until.is_not(None), Job.locked_until < now),
    )


def _claim_values(worker_id: str, now: datetime, lease_s: float) -> dict[str, Any]:
    return {
        "status": JobStatus.RUNNING,
        "locked_by": worker_id,
        "locked_until": now + timedelta(seconds=lease_s),
        "attempts": Job.attempts + 1,
    }


def _rowcount(result: object) -> int:
    return result.rowcount if isinstance(result, CursorResult) else 0


async def claim(db: Database, *, worker_id: str, now: datetime, lease_s: float, limit: int = 1) -> list[Job]:
    if db.is_postgres:
        async with db.session() as s:
            ids = list(
                (
                    await s.execute(
                        select(Job.id)
                        .where(_claimable(now))
                        .order_by(Job.run_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            if ids:
                await s.execute(
                    update(Job)
                    .where(Job.id.in_(ids))
                    .values(**_claim_values(worker_id, now, lease_s))
                    .execution_options(synchronize_session=False)
                )
            await s.commit()
    else:
        async with db.session() as s:
            candidates = list(
                (await s.execute(select(Job.id).where(_claimable(now)).order_by(Job.run_at).limit(limit)))
                .scalars()
                .all()
            )
        ids = []
        for job_id in candidates:
            # Separate short write transactions: SQLite cannot upgrade a stale read snapshot.
            async with db.session() as s:
                result = await s.execute(
                    update(Job)
                    .where(Job.id == job_id, _claimable(now))
                    .values(**_claim_values(worker_id, now, lease_s))
                    .execution_options(synchronize_session=False)
                )
                await s.commit()
            if _rowcount(result) == 1:
                ids.append(job_id)
    return await _load(db, ids)


async def _load(db: Database, ids: Sequence[str]) -> list[Job]:
    if not ids:
        return []
    async with db.session() as s:
        rows = list((await s.execute(select(Job).where(Job.id.in_(ids)).order_by(Job.run_at))).scalars().all())
    return rows


async def _finish(db: Database, job_id: str, worker_id: str, values: dict[str, Any]) -> bool:
    async with db.session() as s:
        result = await s.execute(
            update(Job)
            .where(Job.id == job_id, Job.locked_by == worker_id, Job.status == JobStatus.RUNNING)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await s.commit()
    return _rowcount(result) == 1


async def complete(db: Database, job_id: str, *, worker_id: str) -> bool:
    return await _finish(
        db, job_id, worker_id, {"status": JobStatus.SUCCEEDED, "locked_by": None, "locked_until": None}
    )


async def reschedule(db: Database, job_id: str, *, worker_id: str, run_at: datetime) -> bool:
    """Not a failure: the handler asked to run again later (polling). Attempts reset."""
    return await _finish(
        db,
        job_id,
        worker_id,
        {"status": JobStatus.QUEUED, "run_at": run_at, "attempts": 0, "locked_by": None, "locked_until": None},
    )


async def fail(db: Database, job: Job, *, worker_id: str, error: str, now: datetime) -> str:
    """Record a failed attempt; requeue with backoff or dead-letter. Returns the new status."""
    dead = job.attempts >= job.max_attempts
    values: dict[str, Any] = {
        "last_error": error[:MAX_ERROR_CHARS],
        "locked_by": None,
        "locked_until": None,
        "status": JobStatus.FAILED if dead else JobStatus.QUEUED,
    }
    if not dead:
        values["run_at"] = now + timedelta(seconds=backoff_s(job.attempts))
    await _finish(db, job.id, worker_id, values)
    return str(values["status"])
