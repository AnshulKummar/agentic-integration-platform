"""Health checks. Each returns an observed value against a threshold.

(a) ``synthetic_transaction`` runs the version on synthetic events (stage 'synthetic').
(b) ``connector_health`` probes the integration's source and target systems.
(c) ``window_stats`` + ``slo_checks``: error rate and p95 latency over ExecutionRecord rows.
(d) ``baseline_check``: canary error rate vs the active (baseline) version's.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select

from app.platform.config import PlatformSettings
from app.platform.core_models import ExecutionRecord, Integration, IntegrationVersion
from app.platform.db import Database
from app.platform.deployments.deps import DeploymentDeps
from app.platform.deployments.execution import execute_run

log = logging.getLogger(__name__)

#: Canary error rate may exceed the baseline's by at most this much (absolute).
#: Assumption: no PlatformSettings field exists for it; 2 percentage points.
BASELINE_MARGIN = 0.02
#: p95 is computed over at most this many most-recent executions in a window.
P95_SAMPLE_LIMIT = 10_000
SYNTHETIC_STAGE = "synthetic"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    observed: float | None
    threshold: float | None
    detail: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        return f"{self.name}: observed {_fmt(self.observed)} vs threshold {_fmt(self.threshold)}"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


# --- (a) synthetic transaction ---------------------------------------------------


async def synthetic_transaction(
    db: Database,
    deps: DeploymentDeps,
    *,
    integration: Integration,
    version: IntegrationVersion,
    deployment_id: str,
) -> CheckResult:
    """All synthetic runs must succeed. Runner exceptions propagate (infra failure, job retries)."""
    events = deps.synthetic_events(integration, version, deps.synthetic_runs)
    failures: list[str] = []
    for event in events:
        result = await execute_run(
            db,
            deps,
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
            version_id=version.id,
            event=event,
            stage=SYNTHETIC_STAGE,
            deployment_id=deployment_id,
        )
        if not result.ok:
            failures.append(f"{event.get('id', '?')}: {(result.error or 'failed')[:200]}")
    return CheckResult(
        name="synthetic_transaction",
        ok=not failures and bool(events),
        observed=float(len(failures)),
        threshold=0.0,
        detail={"runs": len(events), "failures": len(failures), "errors": failures[:5]},
    )


# --- (b) connector health -----------------------------------------------------------


async def connector_health(
    deps: DeploymentDeps, settings: PlatformSettings, *, integration: Integration
) -> list[CheckResult]:
    """One check per distinct system; observed = probe latency (ms), threshold = connector timeout."""
    threshold_ms = settings.connector_timeout_s * 1000
    systems = list(dict.fromkeys([integration.source_system, integration.target_system]))
    results: list[CheckResult] = []
    for system in systems:
        name = f"connector_health:{system}"
        try:
            connector = await deps.connector_factory(integration.tenant_id, system)
        except Exception as exc:
            log.warning("connector unavailable for health check", exc_info=True, extra={"system": system})
            results.append(CheckResult(name, False, None, threshold_ms, {"error": f"connector unavailable: {exc}"}))
            continue
        try:
            status = await connector.health()
        except Exception as exc:
            log.warning("connector health probe raised", exc_info=True, extra={"system": system})
            results.append(CheckResult(name, False, None, threshold_ms, {"error": f"health probe raised: {exc}"}))
        else:
            ok = status.ok and status.latency_ms <= threshold_ms
            results.append(CheckResult(name, ok, status.latency_ms, threshold_ms, {"detail": status.detail}))
        finally:
            try:
                await connector.aclose()
            except Exception:
                # Cleanup failure does not change the verdict; surfaced in logs.
                log.warning("connector aclose failed", exc_info=True, extra={"system": system})
    return results


# --- (c) SLO window -------------------------------------------------------------------


@dataclass(frozen=True)
class WindowStats:
    events: int
    errors: int
    p95_ms: float

    @property
    def error_rate(self) -> float:
        return self.errors / self.events if self.events else 0.0

    def as_detail(self) -> dict[str, Any]:
        return {"events": self.events, "errors": self.errors, "error_rate": self.error_rate, "p95_ms": self.p95_ms}


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def window_stats(
    db: Database,
    *,
    tenant_id: str,
    integration_id: str,
    version_id: str,
    stage: str,
    since: datetime,
) -> WindowStats:
    where = (
        ExecutionRecord.tenant_id == tenant_id,
        ExecutionRecord.integration_id == integration_id,
        ExecutionRecord.version_id == version_id,
        ExecutionRecord.stage == stage,
        ExecutionRecord.created_at >= since,
    )
    async with db.session(tenant_id) as s:
        failed = func.sum(case((ExecutionRecord.ok.is_(False), 1), else_=0))
        total, errors = (await s.execute(select(func.count(ExecutionRecord.id), failed).where(*where))).one()
        durations = list(
            (
                await s.execute(
                    select(ExecutionRecord.duration_ms)
                    .where(*where)
                    .order_by(ExecutionRecord.created_at.desc())
                    .limit(P95_SAMPLE_LIMIT)
                )
            )
            .scalars()
            .all()
        )
    return WindowStats(events=int(total or 0), errors=int(errors or 0), p95_ms=p95([float(d) for d in durations]))


def slo_checks(stats: WindowStats, settings: PlatformSettings) -> list[CheckResult]:
    detail = {**stats.as_detail(), "min_events": settings.canary_min_events}
    return [
        CheckResult(
            "error_rate",
            stats.error_rate <= settings.canary_max_error_rate,
            stats.error_rate,
            settings.canary_max_error_rate,
            detail,
        ),
        CheckResult(
            "p95_latency_ms",
            stats.p95_ms <= settings.canary_max_p95_ms,
            stats.p95_ms,
            settings.canary_max_p95_ms,
            detail,
        ),
    ]


# --- (d) canary vs baseline ----------------------------------------------------------


def baseline_check(
    canary: WindowStats, baseline: WindowStats | None, *, margin: float = BASELINE_MARGIN
) -> CheckResult:
    """observed = canary error rate - baseline error rate; must be <= margin."""
    if baseline is None or baseline.events == 0:
        return CheckResult(
            "canary_vs_baseline", True, None, margin,
            {"canary": canary.as_detail(), "baseline": None, "note": "no baseline traffic to compare"},
        )
    delta = canary.error_rate - baseline.error_rate
    return CheckResult(
        "canary_vs_baseline",
        delta <= margin,
        delta,
        margin,
        {"canary": canary.as_detail(), "baseline": baseline.as_detail()},
    )
