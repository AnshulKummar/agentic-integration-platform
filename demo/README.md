# PMAIP click-through demo

Live: https://anshulkummar.github.io/agentic-integration-platform/

A presenter-driven demo of the DSI 2026 paper *Agentic integration of platform APIs for digital product management*.

- **Loop 1 (intent-centric, Figs. 2–3):** a PM asks to connect CRM → Billing, then adds tax. Review needs 3 human corrections. After both approvals it deploys, and the KG learns a pattern plus 3 rules.
- **Loop 2 (new API, agent-initiated):** the e-Signature App ships API v3, and the agent maps it to the blocked "Contract-to-cash" roadmap item. It drafts the integration by reusing the learned pattern (7/9 mappings, 0 corrections), gets PM + Eng approval, and rolls out in stages.
- **Proof:** Loop 1 vs Loop 2 metrics, KG growth, and a **KG learning OFF** ablation switch that replays Loop 2 without learning.

Controls: `→`/Space next, `←` back, `R` restart. Each step is deep-linkable, e.g. `#s=11` or `#s=16&kg=off`.

```bash
npm ci
npm run dev        # http://localhost:5173
npm test           # vitest
npm run build      # static site in dist/
```

The storyline is data (`src/scenario/data.ts`), and `src/engine/state.ts` folds the steps into UI state. The runnable backend that implements the same loops is in `../app/demo` (see the root README).
