"""Mapping predictors behind one protocol.

* ``deterministic`` - the app.demo pipeline on a fresh KG per case.
* ``kg_learning``  - the same pipeline on one persistent KG; after each case the
  gold-corrected mappings (the "human review") are learned by app.demo's
  LearningService before the next case. ``learning=False`` keeps the persistent
  KG but disables learning (the demo's ablation switch).
* ``live_llm``     - Claude with a strict JSON schema; token/cost accounting and a
  hard stop at ``settings.eval_cost_cap_usd``. Only constructed on explicit request.

Runner contract: ``predict(case)`` then ``observe(case)`` (feedback with gold)
for every case, in order. Stateless predictors ignore ``observe``.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.platform.config import PlatformSettings
from app.platform.evals.adapter import (
    CaseGraph,
    LearningKG,
    draft_case,
    normalize_transform,
    register_case,
    row_source_path,
)
from app.platform.evals.dataset import TRANSFORMS, EvalCase
from app.platform.observability.metrics import LLM_COST, LLM_TOKENS

log = logging.getLogger(__name__)

PredictorName = Literal["deterministic", "kg_learning", "live_llm"]
PREDICTORS: tuple[PredictorName, ...] = ("deterministic", "kg_learning", "live_llm")
Origin = Literal["kg_reused", "kg_inferred", "llm_new", "human_edit", "llm"]


@dataclass(frozen=True)
class MappingPrediction:
    target: str
    #: Source path in the case's source schema; None = abstain ("no source").
    source: str | None
    transform: str | None
    confidence: float
    origin: str


@dataclass
class CasePrediction:
    case_id: str
    predictor: str
    mappings: list[MappingPrediction]
    meta: dict[str, float | int | str] = field(default_factory=dict)

    def for_target(self, target: str) -> MappingPrediction | None:
        return next((m for m in self.mappings if m.target == target), None)


class Predictor(Protocol):
    name: str

    def predict(self, case: EvalCase) -> CasePrediction: ...

    def observe(self, case: EvalCase, prediction: CasePrediction) -> None:
        """Feedback after scoring (the gold is the human review)."""
        ...


def _from_rows(case: EvalCase, predictor: str, rows: list[Any]) -> CasePrediction:
    prefix = f"{case.target.object}."
    mappings = [
        MappingPrediction(
            target=r.target[len(prefix):] if r.target.startswith(prefix) else r.target,
            source=row_source_path(case, r),
            transform=normalize_transform(r.transform) if row_source_path(case, r) is not None else None,
            confidence=float(r.confidence),
            origin=str(r.origin),
        )
        for r in rows
    ]
    return CasePrediction(case_id=case.id, predictor=predictor, mappings=mappings)


class DeterministicPredictor:
    name = "deterministic"

    def predict(self, case: EvalCase) -> CasePrediction:
        kg = CaseGraph()
        reg = register_case(kg, case)
        return _from_rows(case, self.name, draft_case(kg, case, reg, patterns=[]))

    def observe(self, case: EvalCase, prediction: CasePrediction) -> None:
        return None


class KGLearningPredictor:
    def __init__(self, learning: bool = True, family_retrieval: bool = True) -> None:
        self.name = "kg_learning" if learning else "kg_learning_off"
        self.state = LearningKG(enabled=learning, family_retrieval=family_retrieval)
        self._rows: dict[str, list[Any]] = {}

    def predict(self, case: EvalCase) -> CasePrediction:
        rows = self.state.draft(case)
        self._rows[case.id] = rows
        pred = _from_rows(case, self.name, rows)
        pred.meta["kg_nodes"] = self.state.kg.counts()["nodes"]
        pred.meta["kg_patterns"] = self.state.kg.counts()["patterns"]
        return pred

    def observe(self, case: EvalCase, prediction: CasePrediction) -> None:
        rows = self._rows.pop(case.id, None)
        if rows is None:
            raise ValueError(f"observe({case.id}) without a prior predict")
        self.state.learn(case, rows)


# ------------------------------------------------------------------ live LLM
#: USD per million tokens (input, output). Source: Anthropic pricing as of 2026-11.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
}
#: Unknown models are costed at the most expensive known price (never under-count spend).
_FALLBACK_PRICE = max(PRICING_PER_MTOK.values())
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25
LLM_MAX_TOKENS = 8000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class CostCapReached(RuntimeError):
    """The next call could exceed ``eval_cost_cap_usd``; the run stops before making it."""


def price_for(model: str) -> tuple[float, float]:
    return PRICING_PER_MTOK.get(model, _FALLBACK_PRICE)


@dataclass
class CostTracker:
    cap_usd: float
    spent_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def check(self, model: str, est_input_tokens: int, max_output_tokens: int) -> None:
        pin, pout = price_for(model)
        worst = (est_input_tokens * pin + max_output_tokens * pout) / 1e6
        if self.spent_usd + worst > self.cap_usd:
            raise CostCapReached(
                f"next call could cost up to ${worst:.4f}; spent ${self.spent_usd:.4f} of cap ${self.cap_usd:.2f}"
            )

    def record(self, model: str, usage: Any) -> float:
        pin, pout = price_for(model)
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cost = (inp * pin + out * pout + cache_read * pin * CACHE_READ_FACTOR
                + cache_write * pin * CACHE_WRITE_FACTOR) / 1e6
        self.spent_usd += cost
        self.input_tokens += inp + cache_read + cache_write
        self.output_tokens += out
        self.calls += 1
        LLM_TOKENS.labels(model=model, kind="input").inc(inp)
        LLM_TOKENS.labels(model=model, kind="output").inc(out)
        if cache_read:
            LLM_TOKENS.labels(model=model, kind="cache_read").inc(cache_read)
        if cache_write:
            LLM_TOKENS.labels(model=model, kind="cache_write").inc(cache_write)
        LLM_COST.labels(model=model).inc(cost)
        return cost


class MessagesClient(Protocol):
    """Subset of ``anthropic.Anthropic().beta.messages`` used here (injectable for tests)."""

    def create(self, **kwargs: Any) -> Any: ...


def mapping_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "source": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "transform": {"anyOf": [{"type": "string", "enum": sorted(TRANSFORMS)}, {"type": "null"}]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["target", "source", "transform", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["mappings"],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = (
    "You map fields between two business-system APIs for an integration platform. For every target field, "
    "name the single source field that populates it, or null if no source field carries that information "
    "(system-assigned ids of the target, target-only configuration, data the source lacks). Never pick a "
    "deprecated source field when its replacement exists. The target's external-reference field maps from the "
    "source primary key. Give a transform from the allowed list when the value needs converting (units, date "
    "formats, enum values, currency/country representations, ids resolved in another system = lookup), else "
    "null. confidence is your probability (0-1) that the source is right."
)


def _describe(fields: Any) -> str:
    lines = []
    for f in fields:
        bits = [f.type + (f"/{f.format}" if f.format else "")]
        if f.enum:
            bits.append("enum " + "|".join(f.enum))
        if f.deprecated:
            bits.append("DEPRECATED")
        if f.description:
            bits.append(f.description)
        if f.sample is not None:
            bits.append(f"e.g. {json.dumps(f.sample)}")
        lines.append(f"- {f.path}: " + "; ".join(bits))
    return "\n".join(lines)


def build_prompt(case: EvalCase) -> str:
    return (
        f"Source: {case.source.system} ({case.source.kind}) object {case.source.object}\n"
        f"{_describe(case.source_fields)}\n\n"
        f"Target: {case.target.system} ({case.target.kind}) object {case.target.object}\n"
        f"{_describe(case.target_fields)}\n\n"
        "Return one mapping per target field, using the exact paths above."
    )


class LiveLLMPredictor:
    name = "live_llm"

    def __init__(self, settings: PlatformSettings, client: MessagesClient | None = None) -> None:
        if client is None and settings.anthropic_api_key is None:
            raise ValueError("live_llm needs ANTHROPIC_API_KEY (or PLATFORM_ANTHROPIC_API_KEY)")
        self.settings = settings
        self.model = settings.llm_model
        self.cost = CostTracker(cap_usd=settings.eval_cost_cap_usd)
        self._client = client

    @property
    def client(self) -> MessagesClient:
        if self._client is None:
            import anthropic

            key = self.settings.anthropic_api_key
            if key is None:
                raise ValueError("live_llm needs ANTHROPIC_API_KEY (or PLATFORM_ANTHROPIC_API_KEY)")
            client: MessagesClient = anthropic.Anthropic(api_key=key.get_secret_value()).beta.messages  # type: ignore[assignment]
            self._client = client
            return client
        return self._client

    def predict(self, case: EvalCase) -> CasePrediction:
        prompt = build_prompt(case)
        est_input = math.ceil((len(SYSTEM_PROMPT) + len(prompt)) / 3.0) + 400  # chars/3 over-estimates tokens
        self.cost.check(self.model, est_input, LLM_MAX_TOKENS)
        response = self.client.create(
            model=self.model,
            max_tokens=LLM_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": mapping_schema()}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        served = str(getattr(response, "model", None) or self.model)
        cost = self.cost.record(served, response.usage)
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise RuntimeError(f"{case.id}: model declined (stop_reason=refusal)")
        if stop == "max_tokens":
            raise RuntimeError(f"{case.id}: response hit max_tokens")
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise RuntimeError(f"{case.id}: structured output missing text block")
        data = json.loads(text)
        return self._parse(case, data, cost)

    def _parse(self, case: EvalCase, data: dict[str, Any], cost: float) -> CasePrediction:
        valid_targets = {f.path for f in case.target_fields}
        seen: dict[str, MappingPrediction] = {}
        for item in data.get("mappings", []):
            target = item.get("target")
            if target not in valid_targets or target in seen:
                log.warning("live_llm.bad_target", extra={"case": case.id, "target": target})
                continue
            source = item.get("source") or None
            transform = item.get("transform") if source is not None else None
            seen[target] = MappingPrediction(
                target=target, source=source, transform=normalize_transform(transform),
                confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))), origin="llm",
            )
        # Targets the model skipped count as abstentions (confidence 0).
        mappings = [seen.get(t, MappingPrediction(t, None, None, 0.0, "llm")) for t in sorted(valid_targets)]
        return CasePrediction(case.id, self.name, mappings, meta={"cost_usd": round(cost, 6)})

    def observe(self, case: EvalCase, prediction: CasePrediction) -> None:
        return None


def make_predictor(name: str, settings: PlatformSettings | None = None, *, learning: bool = True,
                   family_retrieval: bool = True) -> Predictor:
    if name == "deterministic":
        return DeterministicPredictor()
    if name == "kg_learning":
        return KGLearningPredictor(learning=learning, family_retrieval=family_retrieval)
    if name == "live_llm":
        return LiveLLMPredictor(settings or PlatformSettings.from_env())
    raise ValueError(f"unknown predictor {name!r} (choose from {', '.join(PREDICTORS)})")
