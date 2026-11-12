"""Background worker entry point: ``python -m app.platform.worker``.

OWNED BY THE DEPLOYMENTS/OBSERVABILITY AREA (D): runs deployment stage
progression, canary evaluation, post-deploy health watch and auto-rollback,
and delivers lifecycle hooks, by draining the durable ``jobs`` table.

- Claims due jobs exclusively (PostgreSQL ``FOR UPDATE SKIP LOCKED``; SQLite guarded UPDATE).
- A handler returns ``None`` (done) or a delay in seconds (poll again later).
- A handler exception is a failed attempt: retried with exponential backoff up
  to ``max_attempts``, then dead-lettered (status 'failed', ``last_error`` kept).
  A dead-lettered deployment job aborts the deployment (fail / roll back).
- SIGINT/SIGTERM: stop claiming, finish the in-flight job, exit.
- Settings and Database come from the environment (``PLATFORM_*``); the runner
  and connector factory come from ``app.platform.runtime`` (registered at startup).
- Tests call ``Worker.run_once()`` in-process.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from types import FrameType

from prometheus_client import start_http_server

from app.platform import runtime
from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.deployments import jobs
from app.platform.deployments.deps import DeploymentDeps, ServiceUnavailable, build_deps
from app.platform.deployments.hooks import DeploymentEvent
from app.platform.deployments.models import Job, JobStatus
from app.platform.deployments.service import DeploymentService
from app.platform.observability.context import RequestContext, pop_context, push_context
from app.platform.observability.logs import configure_logging
from app.platform.observability.metrics import REGISTRY
from app.platform.secrets.redaction import install_redaction

log = logging.getLogger("app.platform.worker")

#: handler(worker, job) -> None when finished, or seconds until it should run again.
Handler = Callable[["Worker", Job], Awaitable[float | None]]
DEFAULT_LEASE_S = 300.0


async def _handle_advance(worker: Worker, job: Job) -> float | None:
    return await worker.service.advance(_tenant(job), _deployment_id(job))


async def _handle_watch(worker: Worker, job: Job) -> float | None:
    return await worker.service.watch_tick(_tenant(job), _deployment_id(job))


async def _handle_hook(worker: Worker, job: Job) -> float | None:
    await worker.deps.hooks.fire(DeploymentEvent.from_payload(job.payload))
    return None


DEFAULT_HANDLERS: Mapping[str, Handler] = {
    jobs.JOB_ADVANCE: _handle_advance,
    jobs.JOB_WATCH: _handle_watch,
    jobs.JOB_HOOK: _handle_hook,
}
#: Jobs whose permanent failure means the deployment's health cannot be verified.
_DEPLOYMENT_JOBS = frozenset({jobs.JOB_ADVANCE, jobs.JOB_WATCH})


def _tenant(job: Job) -> str:
    if job.tenant_id is None:
        raise ValueError(f"job {job.id} ({job.kind}) has no tenant")
    return job.tenant_id


def _deployment_id(job: Job) -> str:
    value = job.payload.get("deployment_id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"job {job.id} ({job.kind}) payload has no deployment_id")
    return value


class Worker:
    def __init__(
        self,
        db: Database,
        settings: PlatformSettings,
        deps: DeploymentDeps,
        *,
        worker_id: str | None = None,
        handlers: Mapping[str, Handler] | None = None,
        lease_s: float = DEFAULT_LEASE_S,
        poll_interval_s: float = 1.0,
        batch_size: int = 10,
    ) -> None:
        self.db = db
        self.settings = settings
        self.deps = deps
        self.service = DeploymentService(db, settings, deps)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.handlers: dict[str, Handler] = dict(DEFAULT_HANDLERS if handlers is None else handlers)
        self.lease_s = lease_s
        self.poll_interval_s = poll_interval_s
        self.batch_size = batch_size
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        if not self._stop.is_set():
            log.info("worker stopping after in-flight job", extra={"worker_id": self.worker_id})
        self._stop.set()

    async def run_once(self) -> int:
        """Claim and process due jobs (one batch). Returns how many were processed."""
        claimed = await jobs.claim(
            self.db, worker_id=self.worker_id, now=self.deps.clock.now(), lease_s=self.lease_s, limit=self.batch_size
        )
        processed = 0
        for job in claimed:
            if self._stop.is_set():
                # Hand unstarted jobs back immediately instead of waiting for the lease.
                await jobs.reschedule(self.db, job.id, worker_id=self.worker_id, run_at=job.run_at)
                continue
            await self._process(job)
            processed += 1
        return processed

    async def drain(self, max_rounds: int = 100) -> int:
        """Run batches until nothing is due (tests / one-shot CLI). Returns total processed."""
        total = 0
        for _ in range(max_rounds):
            n = await self.run_once()
            if n == 0:
                return total
            total += n
        return total

    async def _process(self, job: Job) -> None:
        token = push_context(RequestContext(request_id=f"job-{job.id}", tenant_id=job.tenant_id))
        try:
            if job.attempts > job.max_attempts:
                # Reclaimed after its lease expired too many times (worker crashes).
                await self._dead_letter(job, "lease expired after max attempts")
                return
            handler = self.handlers.get(job.kind)
            if handler is None:
                await self._dead_letter(job, f"no handler for job kind {job.kind!r}")
                return
            try:
                delay = await handler(self, job)
            except Exception as exc:
                log.exception(
                    "job attempt failed",
                    extra={"job_id": job.id, "job_kind": job.kind, "attempt": job.attempts,
                           "max_attempts": job.max_attempts},
                )
                status = await jobs.fail(
                    self.db, job, worker_id=self.worker_id, error=f"{type(exc).__name__}: {exc}",
                    now=self.deps.clock.now(),
                )
                if status == JobStatus.FAILED:
                    await self._on_dead(job, f"{type(exc).__name__}: {exc}")
                return
            if delay is None:
                await jobs.complete(self.db, job.id, worker_id=self.worker_id)
            else:
                await jobs.reschedule(
                    self.db, job.id, worker_id=self.worker_id,
                    run_at=self.deps.clock.now() + timedelta(seconds=max(0.0, delay)),
                )
        finally:
            pop_context(token)

    async def _dead_letter(self, job: Job, error: str) -> None:
        job.attempts = max(job.attempts, job.max_attempts)
        await jobs.fail(self.db, job, worker_id=self.worker_id, error=error, now=self.deps.clock.now())
        await self._on_dead(job, error)

    async def _on_dead(self, job: Job, error: str) -> None:
        log.error(
            "job dead-lettered",
            extra={"alert": "job_dead_letter", "job_id": job.id, "job_kind": job.kind, "error": error[:500]},
        )
        if job.kind in _DEPLOYMENT_JOBS and job.tenant_id is not None:
            deployment_id = job.payload.get("deployment_id")
            if isinstance(deployment_id, str):
                await self.service.abort(
                    job.tenant_id, deployment_id, f"health checks could not be evaluated: {error[:300]}"
                )

    async def run_forever(self) -> None:
        log.info("worker started", extra={"worker_id": self.worker_id})
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                # Claim-level failure (e.g. DB down): log, back off, keep the process alive.
                log.exception("worker loop error; backing off", extra={"worker_id": self.worker_id})
                processed = 0
                await self._sleep(min(30.0, self.poll_interval_s * 10))
                continue
            if processed == 0:
                await self._sleep(self.poll_interval_s)
        log.info("worker stopped", extra={"worker_id": self.worker_id})

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)


def install_signal_handlers(worker: Worker) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows event loops: fall back to signal.signal, hopping onto the loop thread.
            def _handler(_signum: int, _frame: FrameType | None) -> None:
                loop.call_soon_threadsafe(worker.request_stop)

            signal.signal(sig, _handler)


async def amain(environ: Mapping[str, str] | None = None, *, metrics_port: int = 0) -> None:
    settings = PlatformSettings.from_env(dict(environ) if environ is not None else None).validate_runtime()
    configure_logging(settings.log_level, json_lines=settings.log_json)
    install_redaction()
    runtime.install()
    if metrics_port and settings.metrics_enabled:
        # Deployment/rollback/execution metrics are recorded in this process; serve them
        # for Prometheus (internal network only, like the API's /metrics).
        start_http_server(metrics_port, registry=REGISTRY)
        log.info("worker metrics listening", extra={"port": metrics_port})
    db = Database(settings.database_url)
    try:
        if settings.auto_create_schema:
            await db.create_all()
        try:
            deps = build_deps(settings, db)
        except ServiceUnavailable as exc:
            raise SystemExit(f"worker cannot start: {exc.message}") from exc
        worker = Worker(db, settings, deps)
        install_signal_handlers(worker)
        await worker.run_forever()
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.platform.worker", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--metrics-port", type=int, default=0, help="serve Prometheus metrics on this port (0 = disabled)"
    )
    args = parser.parse_args(argv)
    asyncio.run(amain(metrics_port=args.metrics_port))


if __name__ == "__main__":
    main()
