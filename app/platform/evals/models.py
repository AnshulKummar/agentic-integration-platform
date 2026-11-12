"""Tables for the evals area. OWNED BY: evals area (E).

EvalRun is platform-level (not TenantScoped): evals measure the platform's
mapping pipeline on public/synthetic datasets, not customer data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, new_id, utcnow


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    suite: Mapped[str] = mapped_column(String(32), nullable=False)
    predictor: Mapped[str] = mapped_column(String(32), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Headline numbers per suite ({"synthetic": {"f1": .., "edits_per_case": ..}, ...}) for listings.
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: The full run report (app.platform.evals.runner.run_eval).
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
