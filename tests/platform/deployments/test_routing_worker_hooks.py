"""Canary routing split, durable job worker, lifecycle hooks."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.platform.db import Database
from app.platform.deployments import jobs
from app.platform.deployments.hooks import DeploymentEvent, HookFailed
from app.platform.deployments.models import DeploymentStatus, Job, JobStatus, Stage
from app.platform.deployments.routing import canary_bucket, routes_to_canary
from app.platform.worker import Worker


def test_routing_split_is_deterministic_and_close_to_percent() -> None:
    ids = [f"evt-{i}" for i in range(20_000)]
    first = [routes_to_canary("integ-1", e, 10) for e in ids]
    assert first == [routes_to_canary("integ-1", e, 10) for e in ids]
    share = sum(first) / len(ids)
    assert 0.09 <= share <= 0.11
    # Different integrations hash differently (no correlated canary cohorts).
    assert [routes_to_canary("integ-2", e, 10) for e in ids] != first
    assert not any(routes_to_canary("integ-1", e, 0) for e in ids[:500])
    assert all(routes_to_canary("integ-1", e, 100) for e in ids[:500])
    assert 0 <= canary_bucket("i", "e") < 10_000


async def test_dispatch_routes_canary_share_to_new_version(
    tenant, service, send_events, make_integration, make_version, runner
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    dep = await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v2.id)
    await service.run_to_rest(tenant.id, dep.id)
    counts = await send_events(integ, 200)
    # Exact: every event goes where the deterministic split says (canary_percent=50 in these tests).
    expected = sum(routes_to_canary(integ.id, f"evt-{i}", 50) for i in range(200))
    assert counts["canary"] == expected
    # Loose sanity on the split itself: +/-5 sigma of Binomial(200, 0.5) (the integration id is random per run).
    assert 65 <= counts["canary"] <= 135
    by_stage = {(v, s) for v, s, _ in runner.calls if s in ("canary", "production")}
    assert by_stage == {(v2.id, "canary"), (v1.id, "production")}
    # A redelivered event goes to the same version.
    first = {e: v for v, s, e in runner.calls if s != "synthetic"}
    runner.calls.clear()
    await send_events(integ, 200)
    assert {e: v for v, _, e in runner.calls} == first


async def _enqueue(db: Database, kind: str, *, run_at, max_attempts: int = 3) -> str:  # type: ignore[no-untyped-def]
    async with db.session() as s:
        job = jobs.enqueue(s, kind=kind, payload={}, tenant_id=None, run_at=run_at, max_attempts=max_attempts)
        await s.commit()
        return job.id


async def _job(db: Database, job_id: str) -> Job:
    async with db.session() as s:
        return (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def test_job_runs_exactly_once_with_competing_workers(db, settings, deps, clock) -> None:
    calls: list[str] = []

    async def handler(_: Worker, job: Job) -> float | None:
        calls.append(job.id)
        await asyncio.sleep(0)
        return None

    workers = [Worker(db, settings, deps, worker_id=f"w{i}", handlers={"count": handler}) for i in range(4)]
    ids = [await _enqueue(db, "count", run_at=clock.now()) for _ in range(5)]
    future = await _enqueue(db, "count", run_at=clock.now() + timedelta(seconds=60))

    await asyncio.gather(*(w.run_once() for w in workers))
    await asyncio.gather(*(w.run_once() for w in workers))
    assert sorted(calls) == sorted(ids)  # each due job exactly once; the future job not yet
    for job_id in ids:
        assert (await _job(db, job_id)).status == JobStatus.SUCCEEDED
    assert (await _job(db, future)).status == JobStatus.QUEUED

    clock.advance(61)
    await workers[0].drain()
    assert calls.count(future) == 1


async def test_job_retries_with_backoff_then_succeeds(db, settings, deps, clock) -> None:
    attempts: list[int] = []

    async def flaky(_: Worker, job: Job) -> float | None:
        attempts.append(job.attempts)
        if len(attempts) < 3:
            raise RuntimeError("transient")
        return None

    worker = Worker(db, settings, deps, worker_id="w", handlers={"flaky": flaky})
    job_id = await _enqueue(db, "flaky", run_at=clock.now(), max_attempts=5)
    assert await worker.run_once() == 1
    job = await _job(db, job_id)
    assert job.status == JobStatus.QUEUED and job.last_error == "RuntimeError: transient"
    assert await worker.run_once() == 0  # backoff: not due yet
    clock.advance(jobs.backoff_s(1) + 1)
    await worker.run_once()
    clock.advance(jobs.backoff_s(2) + 1)
    await worker.run_once()
    assert attempts == [1, 2, 3]
    assert (await _job(db, job_id)).status == JobStatus.SUCCEEDED


async def test_job_dead_letters_after_max_attempts(db, settings, deps, clock) -> None:
    async def broken(_: Worker, job: Job) -> float | None:
        raise ValueError("permanent")

    worker = Worker(db, settings, deps, worker_id="w", handlers={"broken": broken})
    job_id = await _enqueue(db, "broken", run_at=clock.now(), max_attempts=2)
    for _ in range(4):
        await worker.run_once()
        clock.advance(600)
    job = await _job(db, job_id)
    assert job.status == JobStatus.FAILED and job.attempts == 2 and job.last_error == "ValueError: permanent"


async def test_expired_lease_is_reclaimed(db, settings, deps, clock) -> None:
    seen: list[str] = []

    async def handler(w: Worker, job: Job) -> float | None:
        seen.append(w.worker_id)
        return None

    job_id = await _enqueue(db, "k", run_at=clock.now())
    # A worker claims and then "dies" without finishing.
    claimed = await jobs.claim(db, worker_id="dead", now=clock.now(), lease_s=60, limit=1)
    assert [j.id for j in claimed] == [job_id]
    live = Worker(db, settings, deps, worker_id="live", handlers={"k": handler})
    assert await live.run_once() == 0
    clock.advance(61)
    assert await live.run_once() == 1
    assert seen == ["live"]
    # The dead worker can no longer complete it.
    assert not await jobs.complete(db, job_id, worker_id="dead")


async def test_graceful_stop_finishes_in_flight_job(db, settings, deps, clock) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    done: list[str] = []

    async def slow(w: Worker, job: Job) -> float | None:
        started.set()
        await release.wait()
        done.append(job.id)
        return None

    worker = Worker(db, settings, deps, worker_id="w", handlers={"slow": slow}, poll_interval_s=0.01, batch_size=1)
    job_id = await _enqueue(db, "slow", run_at=clock.now())
    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(started.wait(), timeout=5)
    worker.request_stop()  # what SIGINT/SIGTERM do
    release.set()
    await asyncio.wait_for(task, timeout=5)
    assert done == [job_id] and (await _job(db, job_id)).status == JobStatus.SUCCEEDED


async def test_worker_main_refuses_to_start_unwired(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.platform import runtime
    from app.platform.deployments.deps import register_provider
    from app.platform.worker import amain

    # Normally amain installs the runtime; simulate a build where it is missing.
    monkeypatch.setattr(runtime, "install", lambda: None)
    register_provider(None)
    env = {"PLATFORM_ENV": "test", "PLATFORM_DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'w.db').as_posix()}"}
    try:
        with pytest.raises(SystemExit, match="deployment runtime is not wired"):
            await amain(env)
    finally:
        monkeypatch.undo()
        runtime.install()


async def test_unknown_job_kind_is_dead_lettered(db, settings, deps, clock) -> None:
    worker = Worker(db, settings, deps, worker_id="w", handlers={})
    job_id = await _enqueue(db, "mystery", run_at=clock.now())
    await worker.run_once()
    job = await _job(db, job_id)
    assert job.status == JobStatus.FAILED and "no handler" in (job.last_error or "")


async def test_worker_drives_deployment_and_fires_on_live_hook(
    db, tenant, service, worker, hooks, send_events, make_integration, make_version, load, clock
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    live: list[DeploymentEvent] = []

    @hooks.on_live
    async def _learn(event: DeploymentEvent) -> None:
        live.append(event)

    dep = await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v2.id)
    await worker.drain()
    dep_row, _, _ = await load(tenant.id, dep.id)
    assert dep_row.current_stage == Stage.CANARY

    await send_events(integ, 40)
    clock.advance(31)
    await worker.drain()
    dep_row, integ_row, _ = await load(tenant.id, dep.id)
    assert dep_row.current_stage == Stage.WATCH and integ_row.active_version_id == v2.id
    assert [(e.kind, e.version_id, e.previous_version_id) for e in live] == [("on_live", v2.id, v1.id)]

    for _ in range(12):
        clock.advance(30)
        await worker.drain()
    dep_row, _, _ = await load(tenant.id, dep.id)
    assert dep_row.status == DeploymentStatus.SUCCEEDED
    assert len(live) == 1


async def test_failing_hook_is_retried(db, tenant, worker, hooks, clock) -> None:
    calls: list[int] = []

    @hooks.on_rolled_back
    async def _flaky(event: DeploymentEvent) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("learning store down")

    event = DeploymentEvent(
        kind="on_rolled_back", tenant_id=tenant.id, deployment_id="d", integration_id="i",
        version_id="v", previous_version_id=None, reason="r",
    )
    with pytest.raises(HookFailed):
        await hooks.fire(event)
    calls.clear()
    async with db.session() as s:
        job = jobs.enqueue(s, kind=jobs.JOB_HOOK, payload=dict(event.to_payload()), tenant_id=tenant.id,
                           run_at=clock.now())
        await s.commit()
    await worker.run_once()
    clock.advance(10)
    await worker.run_once()
    assert len(calls) == 2
    assert (await _job(db, job.id)).status == JobStatus.SUCCEEDED
    assert DeploymentEvent.from_payload(event.to_payload()) == event
