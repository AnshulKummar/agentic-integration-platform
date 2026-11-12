"""The PMAIP agent: runs Loop 1 (intent-centric) and Loop 2 (new API release).

Agents (as in the paper's Fig. 1):
  llm         plans, drafts, explains (scripted heuristics, or Claude in live mode)
  kg          knowledge-graph agent (kg_store, schema_aligner, learning, roadmap_matcher)
  mcp         MCP agent (mcp_tools runtime against the mock target apps, pipeline)
  governance  dual approval + risk checks
  context     context store (prior integrations, change requests)

Every step emits structured ``TraceEvent``s shaped like the frontend's.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.demo import codegen
from app.demo.governance import GovernanceError, approve, assess_risks, ensure_deployable
from app.demo.kg_store import KnowledgeGraph, load_fixture_json, load_seed
from app.demo.learning import LearningService, integration_confidence, pattern_identity
from app.demo.llm import AgentTool, LiveAgent, LiveAgentError, live_available, model_name
from app.demo.mapping_drafter import MappingDrafter, Slot
from app.demo.mcp_tools import McpToolError, tools_for_release
from app.demo.mocks import MockApps, create_mock_apps
from app.demo.models import (
    AgentKind,
    ApiRelease,
    ChatMessage,
    HumanEdit,
    Integration,
    MappingRow,
    RoadmapItem,
    TraceEvent,
    TraceTone,
)
from app.demo.openapi_diff import OpenApiDiff, diff_specs
from app.demo.pipeline import DeploymentPipeline
from app.demo.roadmap_matcher import match_roadmap
from app.demo.runtime import build_mcp_runtime, run_synthetic_tests, sandbox_factory
from app.demo.schema_aligner import apply_alignments, compute_alignments, refine_with_llm, register_capabilities
from app.demo.similarity import content_words, object_similarity, text_overlap

logger = logging.getLogger(__name__)

Mode = Literal["scripted", "live"]

LOOP1_INTENT = "Connect our CRM to the billing system so every closed-won account becomes a billing customer."
LOOP1_REFINE = "Modify to include tax calculations."
PM = ("Priya (PM)", "Matches the roadmap intent.")
ENG = ("Marco (Eng)", "Tests green, rollback plan OK.")

SYSTEM_KEYWORDS: dict[str, str] = {
    "crm": "sys-crm", "billing": "sys-billing", "erp": "sys-billing", "invoice": "sys-billing",
    "tax": "sys-tax", "e-signature": "sys-esign", "esign": "sys-esign", "signature": "sys-esign",
}
TRIGGER_KEYWORDS: dict[str, str] = {"closed-won": "proc-closedwon", "closed won": "proc-closedwon"}
SIGNING_WORDS = ("signs", "signed", "signature")
WATCHED_SYSTEMS = {"sys-esign": "esign"}

ENGINEER_FIXES: dict[str, tuple[str | None, str]] = {
    "Customer.currency_code": (None, "to_iso4217()"),
    "Customer.terms_code": (None, "TERMS_MAP lookup"),
    "TaxQuote.jurisdiction": ("Account.shipping_address", "geocode(ship-to)"),
}
HUMAN_EDIT_CONFIDENCE = 0.97


class DemoError(Exception):
    """A request the demo cannot honour. ``status`` maps to an HTTP status code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class IntentPlan:
    text: str
    source_systems: list[str]
    target_systems: list[str]
    trigger_node: str
    operations: list[str]
    roadmap_id: str | None = None
    source_objects: list[str] = field(default_factory=list)
    trigger_object: str | None = None


class DemoOrchestrator:
    def __init__(self, mode: Mode = "scripted", learning_enabled: bool = True, agent: LiveAgent | None = None) -> None:
        self._agent = agent
        self.reset(mode=mode, learning_enabled=learning_enabled)

    # ================================================================ state
    def reset(self, mode: Mode | None = None, learning_enabled: bool | None = None) -> None:
        seed = load_seed()
        self.seed = seed
        self.requested_mode: Mode = mode or getattr(self, "requested_mode", "scripted")
        self.learning_enabled = (
            learning_enabled if learning_enabled is not None else getattr(self, "learning_enabled", True)
        )
        self.kg = KnowledgeGraph.from_seed(seed)
        self.roadmap: dict[str, RoadmapItem] = {r["id"]: RoadmapItem.model_validate(r) for r in seed["roadmap"]}
        self.field_catalog: dict[str, dict[str, Any]] = seed["fieldCatalog"]
        self.op_catalog: dict[str, dict[str, Any]] = seed["operations"]
        self.context_store: dict[str, Any] = copy.deepcopy(seed["contextStore"])
        self.context_store.setdefault("changeRequests", [])
        self.known_specs: dict[str, dict[str, Any]] = {"sys-esign": load_fixture_json("esign_openapi_v2_4.json")}
        self.integrations: dict[str, Integration] = {}
        self.releases: dict[str, ApiRelease] = {}
        self.diffs: dict[str, OpenApiDiff] = {}
        self.capabilities: dict[str, list[str]] = {}
        self.trace: list[TraceEvent] = []
        self.chat: list[ChatMessage] = []
        self.kg_history: list[dict[str, Any]] = []
        self.apps: MockApps = create_mock_apps()
        self.mcp = build_mcp_runtime(self.apps, self.known_specs["sys-esign"])
        self.learning = LearningService(self.kg, enabled=self.learning_enabled)
        self.drafter = MappingDrafter(self.kg, self.field_catalog)
        self.sandbox = sandbox_factory()
        self.pipeline = DeploymentPipeline(self.mcp, self.sandbox)
        self._step = "setup"
        self._n = 0
        self.mode: Mode = "scripted"
        if self.requested_mode == "live":
            if live_available():
                self.mode = "live"
                if self._agent is None:
                    self._agent = LiveAgent()
            else:
                logger.warning(
                    "live mode requested but ANTHROPIC_API_KEY is not set; falling back to scripted mode"
                )
                self._emit("llm", "Live mode requested but ANTHROPIC_API_KEY is not set: running scripted", tone="warn")
        self._snapshot()

    def _snapshot(self) -> None:
        self.kg_history.append({"step": self._step, **self.kg.counts()})

    def _emit(
        self,
        agent: AgentKind,
        text: str,
        to: AgentKind | None = None,
        lane: int | None = None,
        tone: TraceTone | None = None,
        detail: str | None = None,
    ) -> TraceEvent:
        self._n += 1
        event = TraceEvent(id=f"e{self._n}", agent=agent, to=to, lane=lane, text=text, tone=tone, detail=detail,
                           step=self._step)
        self.trace.append(event)
        logger.debug("trace", extra={"event": event.to_json()})
        return event

    def _say(self, role: Literal["user", "agent"], text: str, options: list[str] | None = None) -> None:
        self.chat.append(ChatMessage(id=f"c{len(self.chat) + 1}", role=role, text=text, options=options))

    def _label(self, node_id: str) -> str:
        node = self.kg.get_node(node_id)
        return node.label if node else node_id

    def _short(self, system_id: str) -> str:
        return self._label(system_id).replace(" (ours)", "")

    def get_integration(self, integration_id: str) -> Integration:
        integ = self.integrations.get(integration_id)
        if integ is None:
            raise DemoError(404, "not_found", f"unknown integration {integration_id!r}")
        return integ

    # ============================================================ live mode
    async def _live(self, system: str, prompt: str, tools: list[AgentTool]) -> str | None:
        """Run the live agent; on failure log, trace a warning and return None (scripted continues)."""
        if self.mode != "live" or self._agent is None:
            return None

        async def on_call(name: str, args: dict[str, Any]) -> None:
            target: AgentKind = "kg" if name.startswith(("kg_", "find_")) else (
                "mcp" if name.startswith(("mcp_", "test_", "run_")) else "context")
            self._emit("llm", f"[live] {name}({', '.join(f'{k}={v}' for k, v in args.items())})", to=target,
                       tone="call")

        try:
            result = await self._agent.run(system, prompt, tools, on_tool_call=on_call)
        except LiveAgentError as exc:
            logger.warning("live agent failed, continuing scripted", extra={"error": str(exc)})
            self._emit("llm", f"Live LLM unavailable ({exc}); continuing with the scripted plan", tone="warn")
            return None
        self._emit("llm", f"[live] {model_name()} finished in {result.turns} turn(s), {len(result.calls)} tool calls",
                   tone="info")
        return result.text.strip() or None

    def _live_tools(self, plan: IntentPlan | None = None, integration: Integration | None = None) -> list[AgentTool]:
        async def kg_neighbours(args: dict[str, Any]) -> Any:
            node = self.kg.find_by_label(str(args.get("label", ""))) or self.kg.get_node(str(args.get("label", "")))
            if node is None:
                raise LookupError(f"no KG node {args.get('label')!r}")
            return [f"{n.id}: {n.label} ({n.type})" for n in self.kg.neighbours(node.id)]

        async def find_patterns(args: dict[str, Any]) -> Any:
            systems = set(args.get("systems") or (plan.source_systems + plan.target_systems if plan else []))
            return [{"pattern": m.pattern.label, "confidence": m.pattern.confidence,
                     "rules": [r.label for r in m.rules]} for m in self.learning.find_applicable(systems)]

        async def test_api(args: dict[str, Any]) -> Any:
            prefix = str(args.get("system", "")).removeprefix("sys-")
            await self.mcp.call(f"{prefix}.health", {})
            return {"health": "ok", "tools": [t.name for t in self.mcp.tools if t.name.startswith(prefix + ".")]}

        async def context_prior(args: dict[str, Any]) -> Any:
            return self.context_store["integrations"]

        async def current_draft(args: dict[str, Any]) -> Any:
            if integration is None:
                return []
            return [r.to_json() for r in integration.mappings]

        async def roadmap_scores(args: dict[str, Any]) -> Any:
            return [r.to_json() for r in self.roadmap.values()]

        obj = {"type": "object", "properties": {}, "additionalProperties": False}
        return [
            AgentTool("kg_neighbours", "List KG neighbours of a node (by label or id).",
                      {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"],
                       "additionalProperties": False}, kg_neighbours),
            AgentTool("find_patterns", "Learned integration patterns (and rules) applicable to a set of system ids.",
                      {"type": "object", "properties": {"systems": {"type": "array", "items": {"type": "string"}}},
                       "additionalProperties": False}, find_patterns),
            AgentTool("test_api", "Health-check a target system via its MCP tools and list its tools.",
                      {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"],
                       "additionalProperties": False}, test_api),
            AgentTool("context_prior_integrations", "Prior integrations from the context store.", obj, context_prior),
            AgentTool("kg_current_draft", "The current drafted mapping rows with origins and confidence.", obj,
                      current_draft),
            AgentTool("kg_roadmap", "Roadmap items with fit scores from the roadmap matcher.", obj, roadmap_scores),
        ]

    # ============================================================== intents
    def _parse_intent(self, text: str, roadmap_id: str | None = None) -> IntentPlan:
        low = text.lower()
        trigger = next((node for kw, node in TRIGGER_KEYWORDS.items() if kw in low), None)
        if trigger is None and any(w in low for w in SIGNING_WORDS) and "contract" in low:
            hooks = [e.source for e in self.kg.edges("TRIGGERS", target="proc-contract-signed")]
            if not hooks:
                raise DemoError(409, "no_trigger", 'No API exposes "Contract signed" yet; check for API releases first')
            trigger = hooks[0]
        if trigger is None:
            raise DemoError(422, "unparsed_intent", "Could not find a trigger in the intent (e.g. 'closed-won').")
        trig_node = self.kg.require_node(trigger)
        trigger_system = trig_node.system or "sys-crm"
        ops = [op for op, meta in self.op_catalog.items() if any(kw in low for kw in meta["keywords"])]
        if "billing.create_invoice" in ops and "billing.create_customer" not in ops and "customer" in low:
            ops.insert(0, "billing.create_customer")
        sources = [trigger_system]
        targets = sorted({self.op_catalog[o]["system"] for o in ops}, key=lambda s: (s == "sys-tax", s))
        trigger_object: str | None = None
        source_objects = ["Account", "Opportunity"]
        if trigger_system != "sys-crm":
            ops.insert(0, "crm.match_contact")
            sources.append("sys-crm")
            targets.insert(0, "sys-crm")
            trigger_object = "Agreement"
            source_objects = ["Agreement", "Account", "Opportunity"]
        if not ops:
            raise DemoError(422, "unparsed_intent", "Could not find a target operation in the intent.")
        if roadmap_id is None:
            best = max(
                self.roadmap.values(),
                key=lambda r: text_overlap(r.intent + " " + r.description, content_words(text)),
            )
            if text_overlap(best.intent, content_words(text)) >= 0.3 and best.status != "shipped":
                roadmap_id = best.id
        return IntentPlan(text=text, source_systems=sources, target_systems=targets, trigger_node=trigger,
                          operations=ops, roadmap_id=roadmap_id, source_objects=source_objects,
                          trigger_object=trigger_object)

    def _slots(self, ops: list[str]) -> list[Slot]:
        return [Slot(target=t, target_system=self.op_catalog[o]["system"]) for o in ops
                for t in self.op_catalog[o]["slots"]]

    def _refresh(self, integ: Integration) -> None:
        integ.confidence = integration_confidence(integ.mappings, integ.human_edits)
        integ.code = codegen.render(integ)
        integ.tools_used = sorted(
            {self.op_catalog[o]["mcpTool"] for o in integ.operations}
            | ({"esign.get_agreement_fields", "esign.get_signers"} if integ.trigger_node != "proc-closedwon" else
               {"crm.get_account"})
        )
        endpoints = [self.op_catalog[o]["endpoint"] for o in integ.operations if self.op_catalog[o]["endpoint"]]
        if integ.trigger_node and integ.trigger_node.startswith("ep-"):
            endpoints.insert(0, integ.trigger_node)
        integ.endpoints = endpoints

    # =============================================================== loop 1
    async def loop1_intent(self, text: str = LOOP1_INTENT) -> Integration:
        self._step = "l1-plan"
        self._say("user", text)
        self._emit("user", f'"{text}"', to="llm", tone="call")
        plan = self._parse_intent(text)
        src, tgt = self._short(plan.source_systems[0]), self._short(plan.target_systems[0])
        self._emit("llm", f"Parse intent → source: {src}, target: {tgt}, trigger: {self._label(plan.trigger_node)}",
                   tone="info")
        systems = set(plan.source_systems + plan.target_systems)

        async def lane_context() -> None:
            self._emit("llm", f"Retrieve previous {src} integrations", to="context", lane=1, tone="call")
            prior = [c for c in self.context_store["integrations"] if c["sourceSystem"] in plan.source_systems]
            history = [c for c in prior if c["targetSystem"] in plan.target_systems]
            names = ", ".join(c["name"] for c in prior) or "none"
            self._emit("context", f"{len(prior)} prior integration(s) ({names}). "
                       + ("" if history else f"No {tgt} history."), to="llm", lane=1, tone="return")

        async def lane_kg() -> None:
            self._emit("llm", f"Query {src} schema and {tgt} patterns", to="kg", lane=2, tone="call")
            patterns = self.learning.find_applicable(systems)
            pairs = []
            for a in ["bo-account", "bo-opportunity"]:
                for b in self.kg.nodes("business_object", system=plan.target_systems[0]):
                    s = object_similarity(self._label(a).lower(), b.label.lower())
                    if s:
                        pairs.append(f"{self._label(a)} ≈ {b.label} ({s:.2f})")
            pat = (f'Pattern "{patterns[0].pattern.label}" ({patterns[0].pattern.confidence:.2f})' if patterns
                   else f"No learned {src} → {tgt} pattern.")
            self._emit("kg", f"Semantic mappings: {', '.join(pairs) or 'none'}. {pat}", to="llm", lane=2,
                       tone="return")

        async def lane_mcp() -> None:
            prefix = plan.target_systems[0].removeprefix("sys-")
            self._emit("llm", f"Test {tgt} API connectivity", to="mcp", lane=3, tone="call")
            await self.mcp.call(f"{prefix}.health", {})
            self._emit("mcp", f"{prefix}.health → GET /health → 200", to="external", lane=3, tone="call")
            caps = [t.name for t in self.mcp.tools if t.name.startswith(prefix + ".") and not t.name.endswith("health")]
            self._emit("mcp", f"Capabilities: {', '.join(caps)}", to="llm", lane=3, tone="return")

        await asyncio.gather(lane_context(), lane_kg(), lane_mcp())

        patterns = self.learning.find_applicable(systems)
        rows = self.drafter.draft(self._slots(plan.operations), plan.source_objects, plan.trigger_object, patterns,
                                  id_prefix="m1")
        integ = Integration(
            id=f"int-{len(self.integrations) + 1}",
            name=f"{self._label(plan.trigger_node).replace('Deal ', '').capitalize()} → {tgt.split(' /')[0]} customer",
            loop=1, source=src, target=tgt, trigger=self._label(plan.trigger_node), initiated_by="user_intent",
            mappings=rows, turns=1, roadmap_id=plan.roadmap_id, intent=text, operations=plan.operations,
            trigger_node=plan.trigger_node, source_systems=plan.source_systems, target_systems=plan.target_systems,
            service_principal=f"{(plan.roadmap_id or 'integration').removeprefix('rm-')}-agent",
        )
        integ.pattern_id, integ.pattern_label = pattern_identity(integ)
        self._refresh(integ)
        self.integrations[integ.id] = integ
        if plan.roadmap_id:
            item = self.roadmap[plan.roadmap_id]
            item.status, item.integration_id = "in_progress", integ.id

        inferred = sum(r.origin in ("kg_inferred", "kg_reused") for r in rows)
        low = sum(r.origin == "llm_new" for r in rows)
        scripted_text = (
            f'Plan: on "{integ.trigger}" in {src}, create a Customer in {tgt}. I found {len(rows)} field mappings: '
            f"{inferred} inferred from the knowledge graph, {low} new with low confidence. "
            f"{tgt} API connectivity tested OK."
        )
        live_text = await self._live(
            "You are the LLM agent of a product-management integration platform (PMAIP). A product manager states "
            "an integration intent. Use the tools to check prior integrations, the knowledge graph and API "
            "connectivity (call independent tools in parallel), then answer the PM with a concise plan (<=100 words) "
            "and two options: A real-time (recommended) or B nightly batch.",
            f"Intent: {text}\nParsed: trigger={integ.trigger}, source={src}, targets={', '.join(plan.target_systems)}",
            self._live_tools(plan, integ),
        )
        self._say("agent", live_text or scripted_text,
                  options=["A · Real-time on closed-won (recommended)", "B · Nightly batch"])
        self._emit("llm", "Present integration plan with options", to="user", tone="success")
        return integ

    def _latest(self, loop: int) -> Integration:
        candidates = [i for i in self.integrations.values() if i.loop == loop]
        if not candidates:
            raise DemoError(409, "no_integration", f"no Loop {loop} integration yet")
        return candidates[-1]

    async def loop1_refine(self, text: str = LOOP1_REFINE, integration_id: str | None = None) -> Integration:
        integ = self.get_integration(integration_id) if integration_id else self._latest(1)
        self._step = "l1-tax"
        self._say("user", text)
        self._emit("user", f'"{text}"', to="llm", tone="call")
        low = text.lower()
        new_ops = [op for op, meta in self.op_catalog.items()
                   if op not in integ.operations and any(kw in low for kw in meta["keywords"])]
        if not new_ops:
            raise DemoError(422, "unparsed_refinement", "Refinement did not name a new capability (e.g. tax).")
        self._emit("llm", "Store modification request", to="context", lane=1, tone="call")
        self.context_store["changeRequests"].append({"integration": integ.id, "text": text})
        labels = ", ".join(self._short(self.op_catalog[o]["system"]) for o in new_ops)
        self._emit("llm", f"Query {labels.lower()} patterns", to="kg", lane=2, tone="call")
        for op in new_ops:
            prefix = op.split(".")[0]
            await self.mcp.call(f"{prefix}.health", {})
            ep = self._label(self.op_catalog[op]["endpoint"])
            self._emit("mcp", f"Test {self._short(self.op_catalog[op]['system'])} {ep} → 200", to="external", lane=3,
                       tone="call")
        integ.operations = integ.operations + new_ops
        integ.target_systems = sorted(set(integ.target_systems) | {self.op_catalog[o]["system"] for o in new_ops},
                                      key=lambda s: (s == "sys-tax", s))
        patterns = self.learning.find_applicable(set(integ.source_systems + integ.target_systems))
        rows = self.drafter.draft(self._slots(new_ops), ["Account", "Opportunity"], None, patterns, id_prefix="m1")
        flagged = [r for r in rows if r.flag]
        if flagged:
            self._emit("kg", "No learned rule for " + ", ".join(
                f"{r.target} ({(r.flag or '').removeprefix('Low confidence: ')})" for r in flagged
            ) + f"; guessed {flagged[0].source}", to="llm", lane=2, tone="warn")
        else:
            self._emit("kg", "Learned rules cover the new mappings", to="llm", lane=2, tone="success")
        integ.mappings = integ.mappings + rows
        integ.turns += 1
        integ.pattern_id, integ.pattern_label = pattern_identity(integ)
        self._refresh(integ)
        first = rows[0]
        self._say("agent", f"Updated plan: call {', '.join(new_ops)} before creating the customer. "
                  f"{first.target} from {first.source}"
                  + (f" ({first.flag.lower()})" if first.flag else "") + ". Ready for review.")
        self._emit("llm", "Present updated plan", to="user", tone="success")
        self._step = "l1-review"
        await self._open_review(integ)
        return integ

    # ============================================================== review
    async def _open_review(self, integ: Integration, lane: int | None = None) -> None:
        what = ("subscribe agreement.completed, replay signed agreements"
                if integ.trigger_node != "proc-closedwon" else "synthetic closed-won deals")
        self._emit("llm", f"Sandbox ({integ.id}): {what}", to="mcp", lane=lane, tone="call")
        integ.tests = await run_synthetic_tests(integ, self.sandbox)
        failing = [t for t in integ.tests if not t.passed]
        self._emit("mcp", f"{len(integ.tests) - len(failing)}/{len(integ.tests)} synthetic tests pass"
                   + (f"; failing: {', '.join(t.name for t in failing)}" if failing else ""),
                   to="llm", lane=lane, tone="warn" if failing else "return")
        integ.risks = assess_risks(integ, self.kg, self.field_catalog, self.context_store["integrations"])
        integ.status = "in_review"
        if integ.roadmap_id:
            self.roadmap[integ.roadmap_id].status = "in_review"

    def _engineer_fixes(self, integ: Integration) -> list[HumanEdit]:
        edits: list[HumanEdit] = []
        for test in integ.tests:
            if test.passed or not test.target_field or test.target_field not in ENGINEER_FIXES:
                continue
            source, transform = ENGINEER_FIXES[test.target_field]
            edits.append(HumanEdit(target=test.target_field, source=source, transform=transform))
        return edits

    async def review(self, integration_id: str, edits: list[HumanEdit] | None = None) -> Integration:
        integ = self.get_integration(integration_id)
        if integ.status not in ("planned", "in_review"):
            raise DemoError(409, "not_reviewable", f"{integ.id} is {integ.status!r}")
        self._step = f"l{integ.loop}-review"
        if integ.status == "planned" or not integ.tests:
            await self._open_review(integ)
        if edits is None:
            edits = self._engineer_fixes(integ)
        for edit in edits:
            row = next((r for r in integ.mappings if r.target == edit.target), None)
            if row is None:
                raise DemoError(422, "unknown_mapping", f"{integ.id} has no mapping targeting {edit.target!r}")
            if edit.source:
                row.source = edit.source
            if edit.transform:
                row.transform = edit.transform
            row.origin, row.confidence, row.flag = "human_edit", HUMAN_EDIT_CONFIDENCE, None
            integ.edit_log.append(edit)
            integ.human_edits += 1
            self._emit("user", f"{edit.by} corrects {edit.target}: {row.source} → {row.transform}", to="llm",
                       tone="call")
        if edits:
            self._emit("llm", f"Recorded {len(edits)} human edit(s) for learning", to="context", tone="info")
        integ.tests = await run_synthetic_tests(integ, self.sandbox)
        integ.risks = assess_risks(integ, self.kg, self.field_catalog, self.context_store["integrations"])
        self._refresh(integ)
        for risk in integ.risks:
            self._emit("governance", f"{risk.label} ({risk.level}): {risk.note}",
                       tone="warn" if risk.level == "high" else "success")
        failing = [t.name for t in integ.tests if not t.passed]
        self._emit("governance", f"Review packet for {integ.id}: {len(integ.tests) - len(failing)}/{len(integ.tests)}"
                   f" tests green, {integ.human_edits} human edit(s), confidence {integ.confidence:.2f}",
                   tone="warn" if failing else "success")
        return integ

    def approve(self, integration_id: str, role: str, by: str, comment: str = "") -> Integration:
        integ = self.get_integration(integration_id)
        self._step = f"l{integ.loop}-approve-{role}"
        try:
            approve(integ, role, by, comment)
        except GovernanceError as exc:
            self._emit("governance", f"Approval rejected: {exc}", tone="warn")
            raise DemoError(422 if exc.code in ("unknown_role", "missing_approver") else 409, exc.code, str(exc)) from exc
        self._emit("governance", f"{role.upper()} approval: {by}: \"{comment}\"", tone="success")
        if integ.status == "approved":
            self._emit("governance", "Both approvals in (PM + Eng): deploy allowed", tone="success")
        return integ

    # ============================================================== deploy
    async def deploy(self, integration_id: str) -> Integration:
        integ = self.get_integration(integration_id)
        self._step = f"l{integ.loop}-deploy"
        try:
            ensure_deployable(integ)
        except GovernanceError as exc:
            self._emit("governance", f"Deploy blocked: {exc}", tone="warn")
            raise DemoError(409, exc.code, str(exc)) from exc
        self._emit("user", '"Approve and deploy"', to="llm", tone="call")
        self._emit("llm", "Store final configuration", to="context", tone="call")
        self.context_store["integrations"].append(
            {"id": integ.id, "name": integ.name, "roadmapId": integ.roadmap_id, "pattern": integ.pattern_id,
             "status": "deploying", "uses": [], "sourceSystem": integ.source_systems[0],
             "targetSystem": integ.target_systems[-1]}
        )
        self._emit("mcp", "Deploy integration (staged rollout)", to="external", tone="call")

        async def on_stage(stage: Any) -> None:
            self._emit("mcp", f"{stage.name}: {stage.note}", to="llm",
                       tone="success" if stage.status == "passed" else "warn")

        result = await self.pipeline.deploy(integ, on_stage)
        ctx = next(c for c in self.context_store["integrations"] if c["id"] == integ.id)
        if not result.ok:
            ctx["status"] = "rolled_back"
            self._emit("mcp", f"Automatic rollback at {result.failed_stage}: {result.reason}", to="llm", tone="warn")
            return integ
        ctx["status"] = "live"
        self._emit("mcp", "Production: healthy", to="llm", tone="success")
        if integ.roadmap_id:
            item = self.roadmap[integ.roadmap_id]
            item.status, item.column = "shipped", "shipped"
        if "signer_email" in integ.migrations:
            self._apply_signer_migration()
        await self._learn(integ)
        return integ

    def _apply_signer_migration(self) -> None:
        deprecated = {e.target: e.source for e in self.kg.edges("DEPRECATES")}
        for ci in self.context_store["integrations"]:
            for used in list(ci.get("uses", [])):
                if used in deprecated:
                    ci["uses"] = [deprecated[used] if u == used else u for u in ci["uses"]]
                    self._emit("mcp", f'Patch "{ci["name"]}": {self._label(used)} → {self._label(deprecated[used])}',
                               to="external", tone="call")
                    if ci.get("roadmapId") in self.roadmap:
                        item = self.roadmap[ci["roadmapId"]]
                        item.impacted = False
                        item.fit_rationale = f"Migrated to {self._label(deprecated[used]).split('.', 1)[-1]} (v3)."
                    if ci.get("pattern") and self.kg.has_node(ci["pattern"]):
                        self.learning.apply_migration(ci["pattern"], used, deprecated[used], "loop2")

    async def _learn(self, integ: Integration) -> None:
        self._step = f"l{integ.loop}-learn"
        learned_in: Literal["loop1", "loop2"] = "loop1" if integ.loop == 1 else "loop2"
        if not self.learning.enabled:
            self._emit("kg", "KG learning disabled: no write-back", tone="warn")
            self._snapshot()
            return
        self._emit("llm", f"Learn pattern from successful integration {integ.id}"
                   + ("; reinforce reused pattern" if integ.patterns_applied else ""), to="kg", tone="call")
        report = self.learning.learn(integ, learned_in)
        self._emit("kg", report.summary(), tone="success")
        self._snapshot()

    # =============================================================== loop 2
    async def _fetch_spec(self, prefix: str) -> dict[str, Any]:
        app = getattr(self.apps, prefix)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.local") as client:
            response = await client.get("/openapi.json")
        if response.status_code != 200:
            raise DemoError(502, "spec_fetch_failed", f"{prefix} spec fetch returned HTTP {response.status_code}")
        spec: dict[str, Any] = response.json()
        return spec

    async def check_api_releases(self) -> list[ApiRelease]:
        found: list[ApiRelease] = []
        for system, prefix in WATCHED_SYSTEMS.items():
            self._step = "l2-release"
            app_label = self._label(system)
            self._emit("mcp", f"GET {app_label} /openapi.json", to="external", tone="call")
            spec = await self._fetch_spec(prefix)
            old = self.known_specs[system]
            new_v, old_v = spec["info"]["version"], old["info"]["version"]
            if new_v == old_v:
                self._emit("mcp", f"{app_label}: no new release (v{new_v})", tone="info")
                continue
            diff = diff_specs(old, spec)
            major = new_v.split(".")[0]
            rel_id = f"rel-{prefix}-v{major}"
            short = lambda v: "v" + ".".join(v.split(".")[:2])  # noqa: E731
            release = ApiRelease(
                id=rel_id, app=app_label, system=system, version=short(new_v), previous_version=short(old_v),
                published_at=str(spec["info"].get("x-release-date", "")), status="analyzing",
                changes=diff.to_api_changes(),
                affected_operations=[f"{e.key} ({'; '.join(e.reasons)})" for e in diff.changed_endpoints],
            )
            self.releases[rel_id] = release
            self.diffs[rel_id] = diff
            self._emit("mcp", f"New release detected: {app_label} {release.version} (was {release.previous_version})",
                       tone="success")

            self._step = "l2-analyze"
            self._emit("mcp", f"Diff {release.previous_version} → {release.version}: {diff.summary()}", tone="info")
            tools = tools_for_release(spec, diff, prefix)
            self.mcp.register_tools(prefix, tools)
            release.mcp_tools = [t.name for t in tools]
            self._emit("mcp", f"Generated MCP tools: {', '.join(release.mcp_tools)}", tone="success",
                       detail="\n".join(str(t.mcp_definition()) for t in tools))
            self._emit("mcp", "Register new capabilities in the knowledge graph", to="kg", tone="call")
            reg = register_capabilities(self.kg, spec, diff, system, "loop2")
            self.capabilities[rel_id] = reg.all_nodes
            self.known_specs[system] = spec
            self._snapshot()

            self._step = "l2-align"
            alignments = compute_alignments(self.kg, reg, diff)
            if self.mode == "live" and self._agent is not None:
                try:
                    alignments = await refine_with_llm(alignments, self._agent)
                    self._emit("llm", "[live] alignment verdicts from Claude applied", to="kg", tone="info")
                except (LiveAgentError, ValueError) as exc:
                    logger.warning("llm alignment refinement failed; using heuristics", extra={"error": str(exc)})
                    self._emit("llm", f"LLM refinement unavailable ({exc}); heuristic alignments kept", tone="warn")
            new_edges = set(apply_alignments(self.kg, alignments, "loop2"))
            lane = 0
            for a in alignments:
                if not a.accepted:
                    continue
                if a.edge_type == "EQUIVALENT_TO":
                    lane += 1
                    self._emit("kg", f"{a.source_label} ≡ {a.target_label} ({a.confidence:.2f})", lane=lane,
                               tone="success", detail=a.reason)
                elif a.edge_type == "TRIGGERS":
                    unblocks = [r for r in self.roadmap.values() if a.target in r.requires and r.status == "blocked"]
                    self._emit("kg", f'{a.source_label.split(" ", 1)[-1]} TRIGGERS "{a.target_label}" → unblocks '
                               f"{len(unblocks)} roadmap item(s)", tone="success", detail=a.reason)
                elif a.edge_type == "DEPRECATES":
                    users = [c for c in self.context_store["integrations"]
                             if c.get("status") == "live" and a.target in c.get("uses", [])]
                    self._emit("kg", f"{a.source_label.split('.', 1)[-1]} DEPRECATES {a.target_label} → "
                               f"{len(users)} live integration(s) affected", tone="warn", detail=a.reason)
            self._snapshot()

            self._step = "l2-match"
            self._emit("llm", "Which roadmap intents REQUIRE nodes reachable from the new capability?", to="kg",
                       tone="call")
            capability_text = " ".join(e.summary for e in diff.added_endpoints) + " " + " ".join(
                w.name + " " + w.summary for w in diff.added_webhooks)
            matches = match_roadmap(self.kg, list(self.roadmap.values()), reg.all_nodes, new_edges, app_label,
                                    capability_text, self.context_store["integrations"])
            impact: list[str] = []
            for m in sorted((m for m in matches if not m.impacted), key=lambda m: -(m.fit_score or 0)):
                item = self.roadmap[m.item_id]
                item.fit_score, item.fit_rationale = m.fit_score, m.rationale
                if m.unblocked:
                    item.status, item.blocked_reason = "in_progress", None
                    self._emit("kg", f"{item.title}: all required nodes now satisfied", to="llm", lane=1,
                               tone="return")
                    impact.append(f"Unblocks roadmap: {item.title} (fit {m.fit_score:.2f})")
                elif m.partial:
                    self._emit("kg", f"{item.title}: partial ({m.rationale})", to="llm", lane=2, tone="return")
                    impact.append(f"Partially enables: {item.title} (fit {m.fit_score:.2f})")
            scored = sorted((m for m in matches if m.fit_score is not None), key=lambda m: -(m.fit_score or 0))
            self._emit("llm", "Fit scores: " + " · ".join(f"{m.title} {m.fit_score:.2f}" for m in scored), tone="info")
            for m in matches:
                if m.impacted:
                    item = self.roadmap[m.item_id]
                    item.impacted, item.fit_rationale = True, m.rationale
                    self._emit("llm", f'Impact on "{item.title}": {m.rationale.removeprefix("Impacted: ")}',
                               tone="warn")
                    impact.append(f'Breaking: "{item.title}" uses a deprecated field')
            release.impact = impact
            release.status = "analyzed"
            live_text = await self._live(
                "You are the LLM agent of PMAIP. A partner shipped a new API version. Using the tools, explain to "
                "the product manager in <=80 words which roadmap items it unblocks or breaks.",
                f"Release {app_label} {release.version}: {diff.summary()}.",
                self._live_tools(),
            )
            if live_text:
                self._say("agent", live_text)
            found.append(release)
        return found

    async def draft_opportunity(self, roadmap_id: str) -> Integration:
        item = self.roadmap.get(roadmap_id)
        if item is None:
            raise DemoError(404, "not_found", f"unknown roadmap item {roadmap_id!r}")
        if item.fit_score is None:
            raise DemoError(409, "not_matched", f"{roadmap_id} has not been matched to a new capability yet")
        if item.status == "blocked":
            raise DemoError(409, "blocked", f"{roadmap_id} is still blocked: {item.blocked_reason}")
        existing = next((i for i in self.integrations.values() if i.roadmap_id == roadmap_id and i.loop == 2), None)
        if existing is not None:
            return existing
        self._step = "l2-draft"
        self._emit("llm", f'Intent source: roadmap item "{item.title}" (stored intent)', tone="info")
        plan = self._parse_intent(item.intent, roadmap_id=roadmap_id)
        systems = set(plan.source_systems + plan.target_systems)
        flow = " → ".join(self._short(s).split(" /")[0].replace(" App", "")
                          for s in plan.source_systems + [t for t in plan.target_systems
                                                          if t not in plan.source_systems and t != "sys-tax"])
        self._emit("llm", f"Find applicable patterns for {flow}", to="kg", lane=1, tone="call")
        patterns = self.learning.find_applicable(systems)
        if not self.learning.enabled:
            self._emit("kg", "KG learning disabled: no learned patterns returned", to="llm", lane=1, tone="warn")
        elif patterns:
            top = patterns[0]
            self._emit("kg", f'Pattern "{top.pattern.label.removeprefix("Pattern: ")}" ({top.pattern.confidence:.2f})'
                       f" + {len(top.rules)} rules" + (f"; +{len(patterns) - 1} more" if len(patterns) > 1 else ""),
                       to="llm", lane=1, tone="return")
        else:
            self._emit("kg", "No applicable learned patterns", to="llm", lane=1, tone="warn")

        trigger = self.kg.require_node(plan.trigger_node)
        event = (trigger.props or {}).get("event", trigger.label)
        trig_edge = self.kg.get_edge(plan.trigger_node, "TRIGGERS", "proc-contract-signed")
        rows: list[MappingRow] = [
            MappingRow(id="m2-trigger", source=f"webhook {event}", target="Opportunity.stage = Contract Signed",
                       origin="kg_inferred", confidence=round(trig_edge.confidence or 0.9, 2) if trig_edge else 0.9)
        ]
        rows += self.drafter.draft(self._slots(plan.operations), plan.source_objects, plan.trigger_object, patterns,
                                   id_prefix="m2")
        used_patterns = sorted({r.pattern_id for r in rows if r.pattern_id})
        deprecated = {e.target for e in self.kg.edges("DEPRECATES")}
        migrations = sorted({self._label(u).split(".")[-1] for c in self.context_store["integrations"]
                             if c.get("status") == "live" for u in c.get("uses", []) if u in deprecated})
        integ = Integration(
            id=f"int-{len(self.integrations) + 1}", name=f"{item.title.replace(' automation', '')}: "
            "signed agreement → customer + first invoice", loop=2, source=self._short(plan.source_systems[0]),
            target=" → ".join(self._short(s) for s in plan.target_systems if s != "sys-tax"),
            trigger=f"{event} (new in {self.releases[next(iter(self.releases))].version})" if self.releases else event,
            initiated_by="agent_from_roadmap", mappings=rows, turns=0, roadmap_id=roadmap_id, intent=item.intent,
            operations=plan.operations, trigger_node=plan.trigger_node, source_systems=plan.source_systems,
            target_systems=plan.target_systems, patterns_applied=used_patterns, migrations=migrations,
            rules_applied=sorted({r.rule_id for r in rows if r.rule_id}),
            service_principal="c2c-agent" if roadmap_id == "rm-contract-to-cash" else f"{roadmap_id}-agent",
        )
        if used_patterns:
            main = max(used_patterns, key=lambda p: sum(r.pattern_id == p for r in rows))
            integ.pattern_reused = self._label(main).removeprefix("Pattern: ")
        integ.pattern_id, integ.pattern_label = pattern_identity(integ)
        self._refresh(integ)
        self.integrations[integ.id] = integ
        item.integration_id = integ.id

        await self._open_review(integ, lane=2)
        failing = [t for t in integ.tests if not t.passed]
        reused = sum(r.origin == "kg_reused" for r in rows)
        low = sum(r.origin == "llm_new" for r in rows)
        scripted_text = (
            f'{self.releases[next(iter(self.releases))].app if self.releases else "The new API"} unblocks your '
            f'roadmap item "{item.title}". I drafted an integration: {reused} of {len(rows)} mappings reused from the '
            f"knowledge graph, {low} low confidence. "
            + ("All sandbox tests pass. " if not failing else f"{len(failing)} sandbox tests fail. ")
            + (f"It also bundles the {', '.join(migrations)} migration. " if migrations else "")
            + "Sent for review."
        )
        live_text = await self._live(
            "You are the LLM agent of PMAIP. You drafted an integration on your own from a stored roadmap intent. "
            "Use the tools to inspect the draft and patterns, then write a <=90-word note to the PM and engineer.",
            f"Roadmap intent: {item.intent}\nIntegration: {integ.name}",
            self._live_tools(plan, integ),
        )
        self._say("agent", live_text or scripted_text)
        self._emit("llm", f"Open review: {item.title} " + ("(needs PM + Eng)" if not failing else "(fixes needed)"),
                   to="governance", tone="call" if failing else "success")
        return integ

    # ========================================================= full loops
    async def run_loop1(self) -> Integration:
        integ = await self.loop1_intent(LOOP1_INTENT)
        await self.loop1_refine(LOOP1_REFINE, integ.id)
        await self.review(integ.id)
        self.approve(integ.id, "pm", *PM)
        self.approve(integ.id, "eng", *ENG)
        return await self.deploy(integ.id)

    async def run_loop2(self) -> Integration:
        releases = await self.check_api_releases()
        if not releases:
            raise DemoError(409, "no_release", "no new API release detected")
        integ = await self.draft_opportunity("rm-contract-to-cash")
        await self.review(integ.id)
        self.approve(integ.id, "pm", "Priya (PM)", "Delivers the Contract-to-cash intent.")
        self.approve(integ.id, "eng", "Marco (Eng)", "Approved. Canary + auto-rollback configured.")
        return await self.deploy(integ.id)

    # ============================================================== views
    def metrics(self) -> dict[str, Any]:
        per = {
            i.id: {
                "loop": i.loop, "initiatedBy": i.initiated_by, "turns": i.turns, "humanEdits": i.human_edits,
                "mappings": len(i.mappings), "reused": sum(r.origin == "kg_reused" for r in i.mappings),
                "confidence": i.confidence, "status": i.status,
            }
            for i in self.integrations.values()
        }
        return {"integrations": per, "kg": self.kg.counts(), "learningEnabled": self.learning_enabled}

    def state(self) -> dict[str, Any]:
        snap = self.kg.snapshot()
        return {
            "mode": self.mode,
            "requestedMode": self.requested_mode,
            "options": {"learningEnabled": self.learning_enabled},
            "nodes": snap["nodes"],
            "edges": snap["edges"],
            "counts": snap["counts"],
            "roadmap": [r.to_json() for r in self.roadmap.values()],
            "integrations": [i.to_json() for i in self.integrations.values()],
            "apiReleases": [r.to_json() for r in self.releases.values()],
            "pipelines": {i.id: [s.to_json() for s in i.pipeline] for i in self.integrations.values() if i.pipeline},
            "kgHistory": self.kg_history,
            "chat": [c.to_json() for c in self.chat],
            "traceLength": len(self.trace),
            "metrics": self.metrics(),
            "contextStore": self.context_store,
        }

    def trace_json(self, since: int = 0) -> list[dict[str, Any]]:
        return [e.to_json() for e in self.trace[since:]]


__all__ = ["DemoOrchestrator", "DemoError", "McpToolError", "LOOP1_INTENT", "LOOP1_REFINE"]
