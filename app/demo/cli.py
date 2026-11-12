"""Terminal demo: run Loop 1 / Loop 2 and pretty-print the agent trace.

    python app/demo/run.py loop1|loop2|all [--live] [--no-learning] [--json] [--show-code]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from app.demo.models import TraceEvent
from app.demo.orchestrator import DemoError, DemoOrchestrator

STEP_TITLES = {
    "setup": "Setup",
    "l1-plan": "Loop 1 · A PM states an intent; agents plan in parallel",
    "l1-tax": "Loop 1 · The PM refines the intent (tax)",
    "l1-review": "Loop 1 · Review: synthetic tests and human edits",
    "l1-approve-pm": "Loop 1 · Product approval",
    "l1-approve-eng": "Loop 1 · Engineering approval",
    "l1-deploy": "Loop 1 · Staged deploy",
    "l1-learn": "Loop 1 · The knowledge graph learns",
    "l2-release": "Loop 2 · The e-Signature App ships a new API",
    "l2-analyze": "Loop 2 · MCP agent diffs the spec and generates tools",
    "l2-align": "Loop 2 · KG agent aligns the new schema",
    "l2-match": "Loop 2 · Roadmap matcher scores stored intents",
    "l2-draft": "Loop 2 · Agent drafts the integration from what it learned",
    "l2-review": "Loop 2 · Review packet and governance checks",
    "l2-approve-pm": "Loop 2 · Product approval",
    "l2-approve-eng": "Loop 2 · Engineering approval",
    "l2-deploy": "Loop 2 · Staged deploy with auto-rollback",
    "l2-learn": "Loop 2 · The knowledge graph compounds",
}
AGENT_NAMES = {"user": "USER", "llm": "LLM", "kg": "KG", "mcp": "MCP", "governance": "GOV", "context": "CTX",
               "external": "EXT"}
AGENT_COLORS = {"user": "97", "llm": "95", "kg": "92", "mcp": "96", "governance": "93", "context": "94",
                "external": "90"}
TONE_MARK = {"call": "→", "return": "←", "info": "·", "warn": "!", "success": "✓"}
TONE_COLORS = {"warn": "33", "success": "32"}


class Printer:
    def __init__(self, color: bool) -> None:
        self.color = color
        self.step: str | None = None

    def c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def event(self, e: TraceEvent) -> None:
        if e.step != self.step:
            self.step = e.step
            print()
            print(self.c("1", f"── {STEP_TITLES.get(e.step or '', e.step or '')} " + "─" * 8))
        who = AGENT_NAMES[e.agent] + (f" → {AGENT_NAMES[e.to]}" if e.to else "")
        lane = f"‖{e.lane} " if e.lane else "   "
        mark = TONE_MARK.get(e.tone or "info", "·")
        text = self.c(TONE_COLORS[e.tone], e.text) if e.tone in TONE_COLORS else e.text
        print(f"  {lane}{self.c(AGENT_COLORS[e.agent], f'{who:<11}')} {mark} {text}")


def _summary(orch: DemoOrchestrator, p: Printer) -> None:
    m = orch.metrics()
    print()
    print(p.c("1", "── Summary " + "─" * 30))
    print(f"  KG learning: {'ON' if m['learningEnabled'] else 'OFF (ablation)'}   mode: {orch.mode}")
    print(f"  {'integration':<8} {'loop':<5} {'started by':<20} {'turns':<6} {'edits':<6} {'reused':<8} "
          f"{'conf':<5} status")
    for iid, s in m["integrations"].items():
        print(f"  {iid:<8} {s['loop']:<5} {s['initiatedBy']:<20} {s['turns']:<6} {s['humanEdits']:<6} "
              f"{str(s['reused']) + '/' + str(s['mappings']):<8} {s['confidence']:<5} {s['status']}")
    k = m["kg"]
    print(f"  KG: {k['nodes']} nodes, {k['edges']} edges, {k['patterns']} patterns, {k['rules']} rules")


async def _run(args: argparse.Namespace) -> DemoOrchestrator:
    orch = DemoOrchestrator(mode="live" if args.live else "scripted", learning_enabled=not args.no_learning)
    printer = Printer(color=not args.no_color and sys.stdout.isatty() and "NO_COLOR" not in os.environ)
    start = 0
    if args.command in ("loop1", "all", "loop2"):
        await orch.run_loop1()
        if args.command == "loop2":
            start = len(orch.trace)
            if not args.json:
                print(printer.c("2", "(Loop 1 ran silently first so the KG has something to learn from.)"))
    if args.command in ("loop2", "all"):
        await orch.run_loop2()
    events = orch.trace[start:]
    if args.json:
        payload: dict[str, Any] = {"trace": [e.to_json() for e in events], "metrics": orch.metrics()}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return orch
    for e in events:
        printer.event(e)
    if args.show_code:
        for integ in orch.integrations.values():
            print()
            print(printer.c("1", f"── Generated code: {integ.id} ({integ.name})"))
            print(integ.code)
    _summary(orch, printer)
    return orch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmaip-demo", description="Run the PMAIP demo loops in the terminal.")
    parser.add_argument("command", choices=["loop1", "loop2", "all"])
    parser.add_argument("--live", action="store_true", help="use Claude (needs ANTHROPIC_API_KEY); else scripted")
    parser.add_argument("--no-learning", action="store_true", help="ablation: disable KG write-back and retrieval")
    parser.add_argument("--json", action="store_true", help="print the trace as JSON")
    parser.add_argument("--show-code", action="store_true", help="print the generated integration code")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--log-level", default=os.environ.get("DEMO_LOG_LEVEL", "WARNING"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(_run(args))
    except DemoError as exc:
        logging.getLogger(__name__).error("demo failed: %s (%s)", exc, exc.code)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
