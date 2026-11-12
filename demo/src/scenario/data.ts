// The PMAIP demo storyline. Generic product names throughout:
//   CRM Platform (ours) — a leading CRM SaaS whose PM owns the roadmap
//   Billing / ERP, Tax Service — downstream finance systems
//   e-Signature App — document signing for contract management (ships a new API in Loop 2)
import type {
  KGEdge,
  KGNode,
  LearnedIn,
  MappingRow,
  NodeType,
  EdgeType,
  PipelineStage,
  RoadmapItem,
  Step,
} from './types'

const n = (
  id: string,
  label: string,
  type: NodeType,
  system: string | undefined,
  learnedIn: LearnedIn = 'seed',
  extra: Partial<KGNode> = {},
): KGNode => ({ id, label, type, system, learnedIn, ...extra })

const e = (source: string, type: EdgeType, target: string, learnedIn: LearnedIn = 'seed'): KGEdge => ({
  id: `${source}--${type}--${target}`,
  source,
  target,
  type,
  learnedIn,
})

export const SYSTEMS = {
  crm: 'sys-crm',
  billing: 'sys-billing',
  tax: 'sys-tax',
  esign: 'sys-esign',
} as const

export const SYSTEM_LABELS: Record<string, string> = {
  'sys-crm': 'CRM Platform (ours)',
  'sys-billing': 'Billing / ERP',
  'sys-tax': 'Tax Service',
  'sys-esign': 'e-Signature App',
}

// ---------------------------------------------------------------- seed KG
export const SEED_NODES: KGNode[] = [
  n('sys-crm', 'CRM Platform (ours)', 'system', undefined),
  n('sys-billing', 'Billing / ERP', 'system', undefined),
  n('sys-tax', 'Tax Service', 'system', undefined),
  n('sys-esign', 'e-Signature App', 'system', undefined),

  n('bo-account', 'Account', 'business_object', 'sys-crm'),
  n('bo-opportunity', 'Opportunity', 'business_object', 'sys-crm'),
  n('bo-contact', 'Contact', 'business_object', 'sys-crm'),
  n('bo-contract', 'Contract', 'business_object', 'sys-crm'),
  n('f-acc-name', 'Account.name', 'data_field', 'sys-crm'),
  n('f-acc-billing', 'Account.billing_address', 'data_field', 'sys-crm'),
  n('f-acc-shipping', 'Account.shipping_address', 'data_field', 'sys-crm'),
  n('f-acc-currency', 'Account.currency', 'data_field', 'sys-crm'),
  n('f-opp-amount', 'Opportunity.amount', 'data_field', 'sys-crm'),
  n('f-opp-terms', 'Opportunity.payment_terms', 'data_field', 'sys-crm'),
  n('f-contact-email', 'Contact.email', 'data_field', 'sys-crm'),

  n('bo-customer', 'Customer', 'business_object', 'sys-billing'),
  n('bo-invoice', 'Invoice', 'business_object', 'sys-billing'),
  n('f-cust-name', 'Customer.legal_name', 'data_field', 'sys-billing'),
  n('f-cust-addr', 'Customer.address', 'data_field', 'sys-billing'),
  n('f-cust-currency', 'Customer.currency_code', 'data_field', 'sys-billing'),
  n('f-cust-terms', 'Customer.terms_code', 'data_field', 'sys-billing'),
  n('f-inv-total', 'Invoice.total', 'data_field', 'sys-billing'),
  n('ep-billing-customers', 'POST /customers', 'api_endpoint', 'sys-billing'),
  n('ep-billing-invoices', 'POST /invoices', 'api_endpoint', 'sys-billing'),

  n('bo-taxquote', 'TaxQuote', 'business_object', 'sys-tax'),
  n('f-tax-jur', 'TaxQuote.jurisdiction', 'data_field', 'sys-tax'),
  n('ep-tax-calc', 'POST /tax/calculate', 'api_endpoint', 'sys-tax'),

  n('bo-envelope', 'Envelope (v2)', 'business_object', 'sys-esign'),
  n('f-env-signer', 'Envelope.signer_email', 'data_field', 'sys-esign'),
  n('ep-esign-v2-send', 'POST /v2/envelopes', 'api_endpoint', 'sys-esign'),

  n('proc-closedwon', 'Deal closed-won', 'process', 'sys-crm'),
  n('proc-contract-signed', 'Contract signed', 'process', undefined, 'seed', {
    note: 'No API exposes this event yet',
  }),
  n('pat-crm-esign-send', 'Pattern: send contract for signature (v2)', 'pattern', undefined, 'seed', {
    confidence: 0.9,
  }),

  n('rm-contract-to-cash', 'Roadmap: Contract-to-cash', 'roadmap', undefined),
  n('rm-billing-sync', 'Roadmap: Closed-won → billing', 'roadmap', undefined),
  n('rm-renewals', 'Roadmap: Self-serve renewals', 'roadmap', undefined),
]

export const SEED_EDGES: KGEdge[] = [
  ...['bo-account', 'bo-opportunity', 'bo-contact', 'bo-contract', 'proc-closedwon'].map((id) =>
    e(id, 'BELONGS_TO', 'sys-crm'),
  ),
  ...['bo-customer', 'bo-invoice', 'ep-billing-customers', 'ep-billing-invoices'].map((id) =>
    e(id, 'BELONGS_TO', 'sys-billing'),
  ),
  ...['bo-taxquote', 'ep-tax-calc'].map((id) => e(id, 'BELONGS_TO', 'sys-tax')),
  ...['bo-envelope', 'ep-esign-v2-send'].map((id) => e(id, 'BELONGS_TO', 'sys-esign')),
  e('bo-account', 'HAS_FIELD', 'f-acc-name'),
  e('bo-account', 'HAS_FIELD', 'f-acc-billing'),
  e('bo-account', 'HAS_FIELD', 'f-acc-shipping'),
  e('bo-account', 'HAS_FIELD', 'f-acc-currency'),
  e('bo-opportunity', 'HAS_FIELD', 'f-opp-amount'),
  e('bo-opportunity', 'HAS_FIELD', 'f-opp-terms'),
  e('bo-contact', 'HAS_FIELD', 'f-contact-email'),
  e('bo-customer', 'HAS_FIELD', 'f-cust-name'),
  e('bo-customer', 'HAS_FIELD', 'f-cust-addr'),
  e('bo-customer', 'HAS_FIELD', 'f-cust-currency'),
  e('bo-customer', 'HAS_FIELD', 'f-cust-terms'),
  e('bo-invoice', 'HAS_FIELD', 'f-inv-total'),
  e('bo-taxquote', 'HAS_FIELD', 'f-tax-jur'),
  e('bo-envelope', 'HAS_FIELD', 'f-env-signer'),
  e('proc-closedwon', 'APPLIES_TO', 'bo-opportunity'),
  e('sys-crm', 'CONNECTS_TO', 'sys-esign'),
  e('pat-crm-esign-send', 'IMPLEMENTS', 'ep-esign-v2-send'),
  e('pat-crm-esign-send', 'APPLIES_TO', 'bo-contract'),
  e('rm-contract-to-cash', 'REQUIRES', 'proc-contract-signed'),
  e('rm-contract-to-cash', 'REQUIRES', 'bo-invoice'),
  e('rm-billing-sync', 'REQUIRES', 'proc-closedwon'),
  e('rm-billing-sync', 'REQUIRES', 'bo-customer'),
  e('rm-renewals', 'REQUIRES', 'bo-contract'),
]

// ---------------------------------------------------------------- roadmap
export const SEED_ROADMAP: RoadmapItem[] = [
  {
    id: 'rm-billing-sync',
    title: 'Closed-won accounts sync to billing',
    description: 'Finance re-keys every new customer from CRM into Billing. Remove the swivel-chair.',
    intent: 'Every closed-won account should become a billing customer automatically.',
    column: 'now',
    status: 'planned',
    quarter: 'Q4 2026',
    owner: 'PM · Revenue Cloud',
    requires: ['proc-closedwon', 'bo-customer'],
  },
  {
    id: 'rm-forecast',
    title: 'AI pipeline forecasting',
    description: 'Forecast quarter-end pipeline from opportunity history.',
    intent: 'Give sales leaders a weekly forecast with confidence bands.',
    column: 'now',
    status: 'in_progress',
    quarter: 'Q4 2026',
    owner: 'PM · Insights',
    requires: [],
  },
  {
    id: 'rm-contract-to-cash',
    title: 'Contract-to-cash automation',
    description: 'Signed contract → billing customer → first invoice with tax, no manual hand-off.',
    intent:
      'When a customer signs their contract, create the billing customer and first invoice automatically, with tax.',
    column: 'next',
    status: 'blocked',
    quarter: 'Q1 2027',
    owner: 'PM · Revenue Cloud',
    requires: ['proc-contract-signed', 'bo-invoice'],
    blockedReason: 'e-Signature App v2 has no "agreement completed" event or signed-field API',
  },
  {
    id: 'rm-renewals',
    title: 'Self-serve renewals',
    description: 'Customers renew in-app; renewal contract generated and signed.',
    intent: 'Let customers renew without talking to sales.',
    column: 'later',
    status: 'planned',
    quarter: 'Q2 2027',
    owner: 'PM · Revenue Cloud',
    requires: ['bo-contract'],
  },
  {
    id: 'rm-partner',
    title: 'Partner portal SSO',
    description: 'Resellers sign in with their own identity provider.',
    intent: 'Partners should not need a separate password.',
    column: 'later',
    status: 'planned',
    quarter: 'Q2 2027',
    owner: 'PM · Platform',
    requires: [],
  },
  {
    id: 'rm-esign-send',
    title: 'Send contracts for e-signature',
    description: 'Reps send contracts from an Opportunity via the e-Signature App.',
    intent: 'Reps send contracts for signature without leaving CRM.',
    column: 'shipped',
    status: 'shipped',
    quarter: 'Q2 2026',
    owner: 'PM · Revenue Cloud',
    requires: ['bo-contract'],
  },
]

// ---------------------------------------------------------------- helpers
const allPassed = (notes: [string, string, string, string]): PipelineStage[] => [
  { name: 'sandbox', status: 'passed', note: notes[0] },
  { name: 'staging', status: 'passed', note: notes[1] },
  { name: 'canary', status: 'passed', note: notes[2] },
  { name: 'production', status: 'passed', note: notes[3] },
]

const m = (
  id: string,
  source: string,
  target: string,
  origin: MappingRow['origin'],
  confidence: number,
  transform?: string,
  flag?: string,
): MappingRow => ({ id, source, target, origin, confidence, transform, flag })

const INT1_CODE = `@on_event("crm.opportunity.closed_won")
async def sync_customer(opp: Opportunity, mcp: MCPClient) -> None:
    acct = await mcp.crm.get_account(opp.account_id)
    tax = await mcp.tax.calculate(
        jurisdiction=acct.shipping_address,   # rule learned from review
        amount=opp.amount,
    )
    await mcp.billing.create_customer(
        legal_name=acct.name,
        address=acct.billing_address,
        currency_code=to_iso4217(acct.currency),
        terms_code=TERMS_MAP[opp.payment_terms],
        tax_profile=tax.profile_id,
    )`

const INT2_CODE = `@on_webhook("esign.agreement.completed")   # new in Agreements API v3
async def contract_to_cash(evt: AgreementCompleted, mcp: MCPClient) -> None:
    fields = await mcp.esign.get_agreement_fields(evt.agreement_id)
    opp = await mcp.crm.find_opportunity(contract_id=evt.agreement_id)
    await mcp.crm.update_opportunity(opp.id, stage="Contract Signed")

    # Reused from KG: "CRM -> Billing customer sync with tax" (learned in Loop 1)
    customer = await patterns.crm_billing_sync(opp, mcp)

    await mcp.billing.create_invoice(
        customer_id=customer.id,
        total=fields.amount,
        terms_code=TERMS_MAP[fields.payment_terms],
    )`

const INT2_CODE_NO_LEARNING = `@on_webhook("esign.agreement.completed")
async def contract_to_cash(evt: AgreementCompleted, mcp: MCPClient) -> None:
    fields = await mcp.esign.get_agreement_fields(evt.agreement_id)
    acct = await mcp.crm.get_account_for_agreement(evt.agreement_id)
    tax = await mcp.tax.calculate(
        jurisdiction=acct.billing_address,    # ⚠ same mistake as Loop 1
        amount=fields.amount,
    )
    customer = await mcp.billing.create_customer(
        legal_name=acct.name,
        currency_code=acct.currency,          # ⚠ not ISO 4217
        terms_code=fields.payment_terms,      # ⚠ free text, not a terms code
    )
    await mcp.billing.create_invoice(customer_id=customer.id, total=fields.amount)`

// Loop 1 mappings (first draft → after tax → after human review)
const INT1_DRAFT: MappingRow[] = [
  m('m1-name', 'Account.name', 'Customer.legal_name', 'kg_inferred', 0.86),
  m('m1-addr', 'Account.billing_address', 'Customer.address', 'kg_inferred', 0.82),
  m('m1-curr', 'Account.currency', 'Customer.currency_code', 'llm_new', 0.55, 'copy', 'Low confidence: format unknown'),
  m('m1-terms', 'Opportunity.payment_terms', 'Customer.terms_code', 'llm_new', 0.51, 'copy', 'Low confidence: enum vs free text'),
]
const INT1_TAX: MappingRow[] = [
  m('m1-tax', 'Account.billing_address', 'TaxQuote.jurisdiction', 'llm_new', 0.48, 'geocode', 'Low confidence: which address?'),
  m('m1-amt', 'Opportunity.amount', 'TaxQuote.amount', 'kg_inferred', 0.84),
]
const INT1_FIXED: MappingRow[] = [
  m('m1-curr', 'Account.currency', 'Customer.currency_code', 'human_edit', 0.97, 'to_iso4217()'),
  m('m1-terms', 'Opportunity.payment_terms', 'Customer.terms_code', 'human_edit', 0.97, 'TERMS_MAP lookup'),
  m('m1-tax', 'Account.shipping_address', 'TaxQuote.jurisdiction', 'human_edit', 0.97, 'geocode(ship-to)'),
]

const INT2_LEARNED: MappingRow[] = [
  m('m2-trigger', 'webhook agreement.completed', 'Opportunity.stage = Contract Signed', 'kg_inferred', 0.9),
  m('m2-signer', 'Agreement.signers[].email', 'Contact.email', 'kg_inferred', 0.92, 'match first signer'),
  m('m2-name', 'Account.name', 'Customer.legal_name', 'kg_reused', 0.95),
  m('m2-addr', 'Account.billing_address', 'Customer.address', 'kg_reused', 0.94),
  m('m2-curr', 'Account.currency', 'Customer.currency_code', 'kg_reused', 0.96, 'to_iso4217()'),
  m('m2-terms', 'Agreement.fields.payment_terms', 'Customer.terms_code', 'kg_reused', 0.93, 'TERMS_MAP lookup'),
  m('m2-tax', 'Account.shipping_address', 'TaxQuote.jurisdiction', 'kg_reused', 0.96, 'geocode(ship-to)'),
  m('m2-taxamt', 'Agreement.fields.amount', 'TaxQuote.amount', 'kg_reused', 0.9),
  m('m2-total', 'Agreement.fields.amount', 'Invoice.total', 'kg_reused', 0.91, '+ tax'),
]
const INT2_UNLEARNED: MappingRow[] = [
  m('m2-trigger', 'webhook agreement.completed', 'Opportunity.stage = Contract Signed', 'kg_inferred', 0.88),
  m('m2-signer', 'Agreement.signers[].email', 'Contact.email', 'kg_inferred', 0.9, 'match first signer'),
  m('m2-name', 'Account.name', 'Customer.legal_name', 'kg_inferred', 0.85),
  m('m2-addr', 'Account.billing_address', 'Customer.address', 'kg_inferred', 0.81),
  m('m2-curr', 'Account.currency', 'Customer.currency_code', 'llm_new', 0.52, 'copy', 'Low confidence: format unknown'),
  m('m2-terms', 'Agreement.fields.payment_terms', 'Customer.terms_code', 'llm_new', 0.49, 'copy', 'Low confidence: enum vs free text'),
  m('m2-tax', 'Account.billing_address', 'TaxQuote.jurisdiction', 'llm_new', 0.47, 'geocode', 'Low confidence: which address?'),
  m('m2-taxamt', 'Agreement.fields.amount', 'TaxQuote.amount', 'kg_inferred', 0.83),
  m('m2-total', 'Agreement.fields.amount', 'Invoice.total', 'llm_new', 0.6),
]
const INT2_UNLEARNED_FIXED: MappingRow[] = [
  m('m2-curr', 'Account.currency', 'Customer.currency_code', 'human_edit', 0.97, 'to_iso4217()'),
  m('m2-terms', 'Agreement.fields.payment_terms', 'Customer.terms_code', 'human_edit', 0.97, 'TERMS_MAP lookup'),
  m('m2-tax', 'Account.shipping_address', 'TaxQuote.jurisdiction', 'human_edit', 0.97, 'geocode(ship-to)'),
]

const GOVERNANCE_RISKS = [
  { label: 'Data access boundary', level: 'medium' as const, note: 'Reads signer emails (PII). Scoped to Contact.email only; no document bodies.' },
  { label: 'Identity propagation', level: 'low' as const, note: 'Runs as service principal "c2c-agent"; actions attributed in audit log.' },
  { label: 'Tool budget', level: 'low' as const, note: '4 MCP tools, max 20 calls per event, 30s timeout.' },
  { label: 'Deprecated field', level: 'medium' as const, note: 'Existing "send for signature" integration uses signer_email (removed in v3). Migration bundled.' },
]

// ---------------------------------------------------------------- storyline
export const STEPS: Step[] = [
  // ============================================================ SETUP
  {
    id: 'setup-roadmap',
    scene: 'setup',
    title: 'Our product roadmap',
    view: 'roadmap',
    narration:
      'We are the product team of a leading CRM SaaS. Each roadmap item is a stored intent written in plain business language. "Contract-to-cash" is blocked: the e-Signature App we integrate with has no API that tells us when a contract is signed.',
    paperRef: 'Discussion: evolving product management practices',
  },
  {
    id: 'setup-kg',
    scene: 'setup',
    title: 'The knowledge graph: institutional memory',
    view: 'graph',
    narration:
      'Our platform keeps a knowledge graph of the systems we connect to, their business objects, fields and APIs, the integration patterns we have learned, and the roadmap items that depend on them. Today it knows nothing about how CRM data should map into Billing.',
    paperRef: 'System architecture: knowledge graph component',
    highlight: ['sys-crm', 'sys-billing', 'sys-tax', 'sys-esign', 'rm-contract-to-cash'],
  },

  // ============================================================ LOOP 1 — intent-centric (Figs. 2–3)
  {
    id: 'l1-intent',
    scene: 'loop1',
    title: 'A PM states an intent',
    view: 'workspace',
    narration:
      'Loop 1 follows the paper exactly. A product manager describes what they want in business language. No ticket, no spec.',
    paperRef: 'Fig. 1: intent-centric process',
    gate: {
      kind: 'type_intent',
      label: 'Send',
      prefill: 'Connect our CRM to the billing system so every closed-won account becomes a billing customer.',
    },
  },
  {
    id: 'l1-plan',
    scene: 'loop1',
    title: 'Agents plan in parallel',
    view: 'workspace',
    narration:
      'The LLM agent fans out in parallel. It asks the context store for prior CRM integrations, asks the KG agent for schemas and billing patterns, and uses the MCP agent to test the Billing API. Then it presents a plan with options. Several mappings are low confidence: the KG has never seen CRM → Billing before.',
    paperRef: 'Fig. 2',
    chat: [
      {
        id: 'c1-u',
        role: 'user',
        text: 'Connect our CRM to the billing system so every closed-won account becomes a billing customer.',
      },
      {
        id: 'c1-a',
        role: 'agent',
        text: 'Plan: on "Deal closed-won" in CRM, create a Customer in Billing / ERP. I found 4 field mappings: 2 inferred from the knowledge graph, 2 new with low confidence. Billing API connectivity tested OK (POST /customers).',
        options: ['A · Real-time on closed-won (recommended)', 'B · Nightly batch'],
      },
    ],
    trace: [
      { id: 't1-1', agent: 'user', to: 'llm', text: '"Connect our CRM to the billing system…"', tone: 'call' },
      { id: 't1-2', agent: 'llm', text: 'Parse intent → source: CRM, target: Billing / ERP, trigger: Deal closed-won', tone: 'info' },
      { id: 't1-3', agent: 'llm', to: 'context', lane: 1, text: 'Retrieve previous CRM integrations', tone: 'call' },
      { id: 't1-4', agent: 'llm', to: 'kg', lane: 2, text: 'Query CRM schema and billing patterns', tone: 'call' },
      { id: 't1-5', agent: 'llm', to: 'mcp', lane: 3, text: 'Test Billing API connectivity', tone: 'call' },
      { id: 't1-6', agent: 'context', to: 'llm', lane: 1, text: '1 prior integration (CRM → e-Signature, send contract). No billing history.', tone: 'return' },
      { id: 't1-7', agent: 'kg', to: 'llm', lane: 2, text: 'Semantic mappings: Account ≈ Customer (0.86). No learned CRM → Billing pattern.', tone: 'return' },
      { id: 't1-8', agent: 'mcp', to: 'external', lane: 3, text: 'GET /health, OPTIONS /customers → 200', tone: 'call' },
      { id: 't1-9', agent: 'mcp', to: 'llm', lane: 3, text: 'Capabilities: POST /customers, POST /invoices', tone: 'return' },
      { id: 't1-10', agent: 'llm', to: 'user', text: 'Present integration plan with options', tone: 'success' },
    ],
    highlight: ['bo-account', 'bo-customer', 'sys-billing'],
    roadmap: [{ id: 'rm-billing-sync', status: 'in_progress', integrationId: 'int-1' }],
    integration: {
      id: 'int-1',
      name: 'Closed-won → Billing customer',
      loop: 1,
      source: 'CRM Platform',
      target: 'Billing / ERP',
      trigger: 'Deal closed-won',
      status: 'planned',
      initiatedBy: 'user_intent',
      mappings: INT1_DRAFT,
      code: INT1_CODE,
      tests: [],
      risks: [],
      approvals: { pm: null, eng: null },
      humanEdits: 0,
      turns: 1,
      confidence: 0.66,
    },
  },
  {
    id: 'l1-modify',
    scene: 'loop1',
    title: 'The PM refines the intent',
    view: 'workspace',
    narration: 'The PM refines the request in plain language, as in Fig. 3.',
    paperRef: 'Fig. 3',
    gate: { kind: 'type_intent', label: 'Send', prefill: 'Modify to include tax calculations.' },
  },
  {
    id: 'l1-tax',
    scene: 'loop1',
    title: 'Updated plan with tax',
    view: 'workspace',
    narration:
      'The LLM agent stores the change request, asks the KG agent for tax-calculation patterns, and shows an updated plan. The KG has no tax pattern for this pair yet, so the agent guesses which address sets the tax jurisdiction. That guess is wrong.',
    paperRef: 'Fig. 3',
    chat: [
      { id: 'c2-u', role: 'user', text: 'Modify to include tax calculations.' },
      {
        id: 'c2-a',
        role: 'agent',
        text: 'Updated plan: call Tax Service POST /tax/calculate before creating the customer. Jurisdiction from Account.billing_address (low confidence: no learned rule). Ready for review.',
      },
    ],
    trace: [
      { id: 't2-1', agent: 'user', to: 'llm', text: '"Modify to include tax calculations"', tone: 'call' },
      { id: 't2-2', agent: 'llm', to: 'context', lane: 1, text: 'Store modification request', tone: 'call' },
      { id: 't2-3', agent: 'llm', to: 'kg', lane: 2, text: 'Query tax calculation patterns', tone: 'call' },
      { id: 't2-4', agent: 'mcp', to: 'external', lane: 3, text: 'Test Tax Service POST /tax/calculate → 200', tone: 'call' },
      { id: 't2-5', agent: 'kg', to: 'llm', lane: 2, text: 'Tax logic options: jurisdiction by billing or ship-to address. No learned rule.', tone: 'warn' },
      { id: 't2-6', agent: 'llm', to: 'user', text: 'Present updated plan', tone: 'success' },
    ],
    highlight: ['sys-tax', 'f-tax-jur', 'f-acc-billing', 'f-acc-shipping'],
    integration: { id: 'int-1', turns: 2, confidence: 0.62 },
    mappings: { integrationId: 'int-1', rows: INT1_TAX },
  },
  {
    id: 'l1-review',
    scene: 'loop1',
    title: 'Review: first time needs the most human feedback',
    view: 'review',
    narration:
      'The plan goes to review. Synthetic tests catch one error, and the engineer fixes three mappings: currency must be ISO 4217, payment terms map to a terms code, and tax jurisdiction comes from the ship-to address. The paper notes that early rounds need the most human feedback.',
    paperRef: 'Findings: learning dynamics',
    integration: {
      id: 'int-1',
      status: 'in_review',
      tests: [
        { name: 'Creates customer for closed-won deal', passed: true },
        { name: 'Currency accepted by Billing (ISO 4217)', passed: false },
        { name: 'Tax uses ship-to when ship-to ≠ bill-to', passed: false },
        { name: 'Idempotent on webhook retry', passed: true },
      ],
      risks: [
        { label: 'Data access boundary', level: 'low', note: 'Account and Opportunity fields only.' },
        { label: 'Identity propagation', level: 'low', note: 'Runs as service principal "billing-sync-agent".' },
      ],
    },
    gate: { kind: 'action', label: 'Engineer corrects 3 mappings' },
  },
  {
    id: 'l1-fixed',
    scene: 'loop1',
    title: 'Mappings corrected; tests pass',
    view: 'review',
    narration:
      'The fixes are recorded as human edits. The platform keeps them so it can learn from them later. Now governance applies: product approves the business fit and engineering approves the code.',
    paperRef: 'Findings: integration governance',
    mappings: { integrationId: 'int-1', rows: INT1_FIXED },
    integration: {
      id: 'int-1',
      humanEdits: 3,
      confidence: 0.81,
      tests: [
        { name: 'Creates customer for closed-won deal', passed: true },
        { name: 'Currency accepted by Billing (ISO 4217)', passed: true },
        { name: 'Tax uses ship-to when ship-to ≠ bill-to', passed: true },
        { name: 'Idempotent on webhook retry', passed: true },
      ],
    },
    gate: { kind: 'approve_pm', label: 'Approve as Product Manager' },
  },
  {
    id: 'l1-approve-eng',
    scene: 'loop1',
    title: 'Engineering approval',
    view: 'review',
    narration: 'Product has approved. Engineering reviews the generated code and test results.',
    paperRef: 'Findings: integration governance',
    integration: {
      id: 'int-1',
      approvals: { pm: { role: 'pm', by: 'Priya (PM)', comment: 'Matches the roadmap intent.' }, eng: null },
    },
    gate: { kind: 'approve_eng', label: 'Approve as Engineering' },
  },
  {
    id: 'l1-deploy',
    scene: 'loop1',
    title: 'Approve and deploy',
    view: 'pipeline',
    narration:
      'With both approvals, the MCP agent stores the final configuration and deploys in stages: sandbox, staging, canary, production. The roadmap item ships.',
    paperRef: 'Fig. 3: "Approve and deploy"',
    integration: {
      id: 'int-1',
      status: 'live',
      approvals: {
        pm: { role: 'pm', by: 'Priya (PM)', comment: 'Matches the roadmap intent.' },
        eng: { role: 'eng', by: 'Marco (Eng)', comment: 'Tests green, rollback plan OK.' },
      },
    },
    pipeline: {
      integrationId: 'int-1',
      stages: allPassed([
        '24 synthetic tests passed',
        'Replayed 50 historical deals',
        '5% of closed-won events for 1h, 0 errors',
        'Live for all tenants',
      ]),
    },
    roadmap: [{ id: 'rm-billing-sync', status: 'shipped', column: 'shipped' }],
    trace: [
      { id: 't3-1', agent: 'user', to: 'llm', text: '"Approve and deploy"', tone: 'call' },
      { id: 't3-2', agent: 'llm', to: 'context', text: 'Store final configuration', tone: 'call' },
      { id: 't3-3', agent: 'mcp', to: 'external', text: 'Deploy integration (staged rollout)', tone: 'call' },
      { id: 't3-4', agent: 'mcp', to: 'llm', text: 'Production: healthy', tone: 'success' },
    ],
  },
  {
    id: 'l1-learn',
    scene: 'loop1',
    title: 'The knowledge graph learns',
    view: 'graph',
    narration:
      'This is the self-improving step. The successful integration and the three human corrections go back into the KG as a reusable pattern and three rules, plus new field mappings. The platform now holds institutional memory it did not have an hour ago.',
    paperRef: 'Findings: "updating the knowledge graph for subsequent use"',
    trace: [
      { id: 't4-1', agent: 'llm', to: 'kg', text: 'Learn pattern from successful integration int-1', tone: 'call' },
      { id: 't4-2', agent: 'kg', text: '+1 pattern, +3 rules from human edits, +6 mappings, +1 equivalence', tone: 'success' },
    ],
    kg: {
      addNodes: [
        n('pat-crm-billing', 'Pattern: CRM → Billing customer sync with tax', 'pattern', undefined, 'loop1', { confidence: 0.81 }),
        n('rule-tax-shipto', 'Rule: tax jurisdiction = ship-to address', 'rule', undefined, 'loop1', { confidence: 0.97 }),
        n('rule-iso-currency', 'Rule: currency → ISO 4217', 'rule', undefined, 'loop1', { confidence: 0.97 }),
        n('rule-terms-map', 'Rule: payment terms → billing terms code', 'rule', undefined, 'loop1', { confidence: 0.97 }),
      ],
      addEdges: [
        e('bo-account', 'EQUIVALENT_TO', 'bo-customer', 'loop1'),
        e('f-acc-name', 'MAPS_TO', 'f-cust-name', 'loop1'),
        e('f-acc-billing', 'MAPS_TO', 'f-cust-addr', 'loop1'),
        e('f-acc-currency', 'MAPS_TO', 'f-cust-currency', 'loop1'),
        e('f-opp-terms', 'MAPS_TO', 'f-cust-terms', 'loop1'),
        e('f-acc-shipping', 'MAPS_TO', 'f-tax-jur', 'loop1'),
        e('sys-crm', 'CONNECTS_TO', 'sys-billing', 'loop1'),
        e('sys-billing', 'CONNECTS_TO', 'sys-tax', 'loop1'),
        e('pat-crm-billing', 'IMPLEMENTS', 'ep-billing-customers', 'loop1'),
        e('pat-crm-billing', 'IMPLEMENTS', 'ep-tax-calc', 'loop1'),
        e('rule-tax-shipto', 'APPLIES_TO', 'pat-crm-billing', 'loop1'),
        e('rule-iso-currency', 'APPLIES_TO', 'pat-crm-billing', 'loop1'),
        e('rule-terms-map', 'APPLIES_TO', 'pat-crm-billing', 'loop1'),
        e('pat-crm-billing', 'IMPLEMENTS', 'rm-billing-sync', 'loop1'),
      ],
    },
    highlight: ['pat-crm-billing', 'rule-tax-shipto', 'rule-iso-currency', 'rule-terms-map'],
    learningOff: {
      narration:
        'Ablation: KG learning is OFF. The integration works, but nothing is written back. The three human corrections live only in this one integration\'s code.',
      trace: [{ id: 't4-1', agent: 'kg', text: 'KG learning disabled: no write-back', tone: 'warn' }],
      kg: { addNodes: [], addEdges: [], updateNodes: [] },
      highlight: [],
    },
  },

  // ============================================================ LOOP 2 — new API, agent-initiated
  {
    id: 'l2-release',
    scene: 'loop2',
    title: 'The e-Signature App ships a new API',
    view: 'inbox',
    narration:
      'Weeks later, nobody has typed anything. The e-Signature App publishes Agreements API v3. It adds an "agreement completed" webhook and a signed-fields endpoint, and deprecates a field we use today. The platform watches partner API specs.',
    paperRef: 'Introduction: "as APIs evolve"',
    apiRelease: {
      id: 'rel-esign-v3',
      app: 'e-Signature App',
      version: 'v3.0',
      previousVersion: 'v2.4',
      publishedAt: '2026-11-10',
      status: 'new',
      changes: [
        { kind: 'added', method: 'POST', path: '/v3/agreements', summary: 'Create agreement from template' },
        { kind: 'added', method: 'GET', path: '/v3/agreements/{id}/fields', summary: 'Signed form data (amount, term, payment terms)' },
        { kind: 'added', method: 'WEBHOOK', path: 'agreement.completed', summary: 'Fires when all parties have signed' },
        { kind: 'deprecated', method: 'FIELD', path: 'envelope.signer_email', summary: 'Replaced by signers[].email' },
      ],
    },
  },
  {
    id: 'l2-analyze',
    scene: 'loop2',
    title: 'MCP agent reads and wraps the spec',
    view: 'inbox',
    narration:
      'The MCP agent fetches the OpenAPI spec, compares it with v2.4, and generates MCP tool wrappers for the new endpoints. Other agents can now call the new capability through one standard interface.',
    paperRef: 'MCP component; Falk et al. (2025): REST → MCP',
    trace: [
      { id: 't5-1', agent: 'mcp', to: 'external', text: 'GET e-Signature App /openapi.json (v3.0)', tone: 'call' },
      { id: 't5-2', agent: 'mcp', text: 'Diff v2.4 → v3.0: +2 endpoints, +1 webhook, 1 deprecated field', tone: 'info' },
      { id: 't5-3', agent: 'mcp', text: 'Generated MCP tools: esign.create_agreement, esign.get_agreement_fields, esign.subscribe_completed, esign.get_signers', tone: 'success' },
      { id: 't5-4', agent: 'mcp', to: 'kg', text: 'Register new capabilities in the knowledge graph', tone: 'call' },
    ],
    apiRelease: {
      id: 'rel-esign-v3',
      status: 'analyzed',
      mcpTools: ['esign.create_agreement', 'esign.get_agreement_fields', 'esign.subscribe_completed', 'esign.get_signers'],
    },
    kg: {
      addNodes: [
        n('bo-agreement', 'Agreement (v3)', 'business_object', 'sys-esign', 'loop2'),
        n('f-agr-signers', 'Agreement.signers[].email', 'data_field', 'sys-esign', 'loop2'),
        n('f-agr-amount', 'Agreement.fields.amount', 'data_field', 'sys-esign', 'loop2'),
        n('f-agr-terms', 'Agreement.fields.payment_terms', 'data_field', 'sys-esign', 'loop2'),
        n('ep-esign-v3-fields', 'GET /v3/agreements/{id}/fields', 'api_endpoint', 'sys-esign', 'loop2'),
        n('ep-esign-v3-webhook', 'WEBHOOK agreement.completed', 'api_endpoint', 'sys-esign', 'loop2'),
      ],
      addEdges: [
        e('bo-agreement', 'BELONGS_TO', 'sys-esign', 'loop2'),
        e('ep-esign-v3-fields', 'BELONGS_TO', 'sys-esign', 'loop2'),
        e('ep-esign-v3-webhook', 'BELONGS_TO', 'sys-esign', 'loop2'),
        e('bo-agreement', 'HAS_FIELD', 'f-agr-signers', 'loop2'),
        e('bo-agreement', 'HAS_FIELD', 'f-agr-amount', 'loop2'),
        e('bo-agreement', 'HAS_FIELD', 'f-agr-terms', 'loop2'),
      ],
    },
    highlight: ['bo-agreement', 'ep-esign-v3-webhook', 'ep-esign-v3-fields'],
  },
  {
    id: 'l2-align',
    scene: 'loop2',
    title: 'KG agent aligns the new schema',
    view: 'graph',
    narration:
      'The KG agent works out what the new objects mean. An Agreement is our Contract. Signers are our Contacts. The new webhook is the "Contract signed" event our roadmap has been waiting for. The deprecated field is linked to its replacement.',
    paperRef: 'KG component: semantic equivalence across systems',
    trace: [
      { id: 't6-1', agent: 'kg', lane: 1, text: 'Agreement (v3) ≡ Contract (0.93)', tone: 'success' },
      { id: 't6-2', agent: 'kg', lane: 2, text: 'signers[].email ≡ Contact.email (0.92)', tone: 'success' },
      { id: 't6-3', agent: 'kg', lane: 3, text: 'fields.amount ≡ Opportunity.amount; fields.payment_terms ≡ Opportunity.payment_terms', tone: 'success' },
      { id: 't6-4', agent: 'kg', text: 'agreement.completed TRIGGERS "Contract signed" → unblocks 1 roadmap item', tone: 'success' },
      { id: 't6-5', agent: 'kg', text: 'signers[].email DEPRECATES Envelope.signer_email → 1 live integration affected', tone: 'warn' },
    ],
    kg: {
      addEdges: [
        e('bo-agreement', 'EQUIVALENT_TO', 'bo-contract', 'loop2'),
        e('f-agr-signers', 'EQUIVALENT_TO', 'f-contact-email', 'loop2'),
        e('f-agr-amount', 'EQUIVALENT_TO', 'f-opp-amount', 'loop2'),
        e('f-agr-terms', 'EQUIVALENT_TO', 'f-opp-terms', 'loop2'),
        e('ep-esign-v3-webhook', 'TRIGGERS', 'proc-contract-signed', 'loop2'),
        e('f-agr-signers', 'DEPRECATES', 'f-env-signer', 'loop2'),
      ],
      updateNodes: [{ id: 'proc-contract-signed', note: 'Now exposed by e-Signature App v3 webhook' }],
    },
    highlight: ['bo-agreement', 'bo-contract', 'proc-contract-signed', 'rm-contract-to-cash', 'f-env-signer'],
  },
  {
    id: 'l2-match',
    scene: 'loop2',
    title: 'Agent maps the capability to the roadmap',
    view: 'roadmap',
    narration:
      'The roadmap-matcher agent scores every stored intent against the new capability. Contract-to-cash is a 0.91 fit and is no longer blocked. The agent also flags that the shipped "send for signature" integration uses the deprecated field, catching a breaking change before it breaks.',
    paperRef: 'Findings: "catching breaking changes early"',
    trace: [
      { id: 't7-1', agent: 'llm', to: 'kg', text: 'Which roadmap intents REQUIRE nodes reachable from the new capability?', tone: 'call' },
      { id: 't7-2', agent: 'kg', to: 'llm', lane: 1, text: 'Contract-to-cash: all required nodes now satisfied', tone: 'return' },
      { id: 't7-3', agent: 'kg', to: 'llm', lane: 2, text: 'Self-serve renewals: partial (needs create-from-template)', tone: 'return' },
      { id: 't7-4', agent: 'llm', text: 'Fit scores: Contract-to-cash 0.91 · Self-serve renewals 0.64 · Partner SSO 0.04', tone: 'info' },
      { id: 't7-5', agent: 'llm', text: 'Impact: "Send contracts for e-signature" uses Envelope.signer_email (deprecated)', tone: 'warn' },
    ],
    roadmap: [
      {
        id: 'rm-contract-to-cash',
        status: 'in_progress',
        blockedReason: undefined,
        fitScore: 0.91,
        fitRationale: 'agreement.completed supplies the missing "Contract signed" trigger; signed fields supply amount and terms.',
        integrationId: 'int-2',
      },
      { id: 'rm-renewals', fitScore: 0.64, fitRationale: 'POST /v3/agreements can generate renewal contracts; pricing logic still missing.' },
      { id: 'rm-partner', fitScore: 0.04, fitRationale: 'Unrelated to e-signature.' },
      { id: 'rm-esign-send', fitRationale: 'Impacted: uses deprecated Envelope.signer_email. Migration required before v2 sunset.' },
    ],
    apiRelease: {
      id: 'rel-esign-v3',
      impact: [
        'Unblocks roadmap: Contract-to-cash automation (fit 0.91)',
        'Partially enables: Self-serve renewals (fit 0.64)',
        'Breaking: "Send contracts for e-signature" uses deprecated signer_email',
      ],
    },
    highlight: ['rm-contract-to-cash', 'proc-contract-signed'],
  },
  {
    id: 'l2-draft',
    scene: 'loop2',
    title: 'Agent drafts the integration from what it learned',
    view: 'workspace',
    narration:
      'No one asked. Starting from the stored roadmap intent, the agent drafts the contract-to-cash integration. It pulls the pattern learned in Loop 1 from the KG, so 7 of 9 mappings, including all three human corrections, are reused. Sandbox tests pass on the first run.',
    paperRef: 'Competitive dynamics: pattern libraries create compound advantage',
    chat: [
      {
        id: 'c3-a',
        role: 'agent',
        text: 'e-Signature App v3 unblocks your roadmap item "Contract-to-cash automation". I drafted an integration: on agreement.completed, mark the Opportunity signed, create the billing customer (reusing the CRM → Billing pattern, with tax), then raise the first invoice. 7 of 9 mappings reused from the knowledge graph. All sandbox tests pass. It also bundles the signer_email migration. Sent for review.',
      },
    ],
    trace: [
      { id: 't8-1', agent: 'llm', text: 'Intent source: roadmap item "Contract-to-cash automation" (stored intent)', tone: 'info' },
      { id: 't8-2', agent: 'llm', to: 'kg', lane: 1, text: 'Find applicable patterns for e-Signature → CRM → Billing', tone: 'call' },
      { id: 't8-3', agent: 'kg', to: 'llm', lane: 1, text: 'Pattern "CRM → Billing customer sync with tax" (0.81) + 3 rules', tone: 'return' },
      { id: 't8-4', agent: 'llm', to: 'mcp', lane: 2, text: 'Sandbox: subscribe agreement.completed, replay 20 signed agreements', tone: 'call' },
      { id: 't8-5', agent: 'mcp', to: 'llm', lane: 2, text: '20/20 customers + invoices created, tax correct', tone: 'return' },
      { id: 't8-6', agent: 'llm', to: 'governance', text: 'Open review: Contract-to-cash (needs PM + Eng)', tone: 'success' },
    ],
    integration: {
      id: 'int-2',
      name: 'Contract-to-cash: signed agreement → customer + first invoice',
      loop: 2,
      source: 'e-Signature App',
      target: 'CRM Platform → Billing / ERP',
      trigger: 'agreement.completed (new in v3)',
      status: 'in_review',
      initiatedBy: 'agent_from_roadmap',
      mappings: INT2_LEARNED,
      code: INT2_CODE,
      tests: [
        { name: 'Signed agreement creates customer + invoice', passed: true },
        { name: 'Currency accepted by Billing (ISO 4217)', passed: true },
        { name: 'Tax uses ship-to when ship-to ≠ bill-to', passed: true },
        { name: 'Idempotent on webhook retry', passed: true },
        { name: 'signer_email migration: send flow still works', passed: true },
      ],
      risks: GOVERNANCE_RISKS,
      approvals: { pm: null, eng: null },
      humanEdits: 0,
      turns: 0,
      confidence: 0.88,
      patternReused: 'CRM → Billing customer sync with tax',
    },
    roadmap: [{ id: 'rm-contract-to-cash', status: 'in_review' }],
    learningOff: {
      narration:
        'Ablation: KG learning is OFF. The agent cannot see the Loop 1 pattern, so it starts from scratch. It repeats the same three mistakes: wrong tax address, raw currency, free-text terms. Sandbox tests fail.',
      chat: [
        {
          id: 'c3-a',
          role: 'agent',
          text: 'e-Signature App v3 unblocks "Contract-to-cash automation". I drafted an integration, but no learned patterns were available. 4 mappings are low confidence and 2 sandbox tests fail. Sent for review with fixes needed.',
        },
      ],
      trace: [
        { id: 't8-1', agent: 'llm', text: 'Intent source: roadmap item "Contract-to-cash automation" (stored intent)', tone: 'info' },
        { id: 't8-2', agent: 'llm', to: 'kg', lane: 1, text: 'Find applicable patterns for e-Signature → CRM → Billing', tone: 'call' },
        { id: 't8-3', agent: 'kg', to: 'llm', lane: 1, text: 'KG learning disabled: no learned patterns returned', tone: 'warn' },
        { id: 't8-4', agent: 'llm', to: 'mcp', lane: 2, text: 'Sandbox: subscribe agreement.completed, replay 20 signed agreements', tone: 'call' },
        { id: 't8-5', agent: 'mcp', to: 'llm', lane: 2, text: '20/20 created, but 7 rejected currency codes and 6 wrong tax jurisdictions', tone: 'warn' },
        { id: 't8-6', agent: 'llm', to: 'governance', text: 'Open review: Contract-to-cash (fixes needed)', tone: 'call' },
      ],
      integration: {
        id: 'int-2',
        name: 'Contract-to-cash: signed agreement → customer + first invoice',
        loop: 2,
        source: 'e-Signature App',
        target: 'CRM Platform → Billing / ERP',
        trigger: 'agreement.completed (new in v3)',
        status: 'in_review',
        initiatedBy: 'agent_from_roadmap',
        mappings: INT2_UNLEARNED,
        code: INT2_CODE_NO_LEARNING,
        tests: [
          { name: 'Signed agreement creates customer + invoice', passed: true },
          { name: 'Currency accepted by Billing (ISO 4217)', passed: false },
          { name: 'Tax uses ship-to when ship-to ≠ bill-to', passed: false },
          { name: 'Idempotent on webhook retry', passed: true },
          { name: 'signer_email migration: send flow still works', passed: true },
        ],
        risks: GOVERNANCE_RISKS,
        approvals: { pm: null, eng: null },
        humanEdits: 0,
        turns: 0,
        confidence: 0.58,
      },
    },
  },
  {
    id: 'l2-review',
    scene: 'loop2',
    title: 'Review packet for product and engineering',
    view: 'review',
    narration:
      'The review packet shows the roadmap intent it serves, every mapping and where it came from, the generated code, test results, and governance checks: data access boundary, identity propagation, tool budget. Nothing needs fixing.',
    paperRef: 'Discussion: security considerations (Liu et al., 2026)',
    gate: { kind: 'action', label: 'Run governance checks' },
    learningOff: {
      narration:
        'Without learning, review looks like Loop 1 again. The engineer makes the same three corrections a second time. That is the cost of a platform that does not learn.',
      gate: { kind: 'action', label: 'Engineer corrects 3 mappings (again)' },
    },
  },
  {
    id: 'l2-approve-pm',
    scene: 'loop2',
    title: 'Product approval',
    view: 'review',
    narration:
      'Governance checks pass. The PM confirms this delivers the roadmap intent: contract signed, customer and first invoice created with tax.',
    paperRef: 'Findings: integration governance',
    trace: [
      { id: 't9-1', agent: 'governance', text: 'Data boundary: PII scoped to Contact.email ✓', tone: 'success' },
      { id: 't9-2', agent: 'governance', text: 'Identity: service principal c2c-agent, audit log on ✓', tone: 'success' },
      { id: 't9-3', agent: 'governance', text: 'Tool budget: 4 tools, ≤20 calls/event ✓', tone: 'success' },
    ],
    gate: { kind: 'approve_pm', label: 'Approve as Product Manager' },
    learningOff: {
      narration: 'After the same three fixes, tests pass. The PM approves.',
      mappings: { integrationId: 'int-2', rows: INT2_UNLEARNED_FIXED },
      integration: {
        id: 'int-2',
        humanEdits: 3,
        confidence: 0.81,
        tests: [
          { name: 'Signed agreement creates customer + invoice', passed: true },
          { name: 'Currency accepted by Billing (ISO 4217)', passed: true },
          { name: 'Tax uses ship-to when ship-to ≠ bill-to', passed: true },
          { name: 'Idempotent on webhook retry', passed: true },
          { name: 'signer_email migration: send flow still works', passed: true },
        ],
      },
    },
  },
  {
    id: 'l2-approve-eng',
    scene: 'loop2',
    title: 'Engineering approval',
    view: 'review',
    narration: 'Engineering signs off on code, tests and rollback plan. The policy needs both approvals before anything reaches production.',
    paperRef: 'Findings: integration governance',
    integration: {
      id: 'int-2',
      approvals: { pm: { role: 'pm', by: 'Priya (PM)', comment: 'Delivers the Contract-to-cash intent.' }, eng: null },
    },
    gate: { kind: 'approve_eng', label: 'Approve as Engineering' },
  },
  {
    id: 'l2-deploy',
    scene: 'loop2',
    title: 'Implemented to production automatically',
    view: 'pipeline',
    narration:
      'Both approvals are in, so the agent implements on its own: sandbox, staging, a 5% canary with automatic rollback, then production. The signer_email migration ships too. The roadmap item that was blocked this morning is now shipped.',
    paperRef: 'Fig. 3: deploy via MCP agent',
    integration: {
      id: 'int-2',
      status: 'live',
      approvals: {
        pm: { role: 'pm', by: 'Priya (PM)', comment: 'Delivers the Contract-to-cash intent.' },
        eng: { role: 'eng', by: 'Marco (Eng)', comment: 'Approved. Canary + auto-rollback configured.' },
      },
    },
    pipeline: {
      integrationId: 'int-2',
      stages: allPassed([
        '31 synthetic tests passed',
        'Replayed 120 signed agreements',
        '5% of agreements for 2h, 0 errors, auto-rollback armed',
        'Live; signer_email migration complete',
      ]),
    },
    roadmap: [
      { id: 'rm-contract-to-cash', status: 'shipped', column: 'shipped' },
      { id: 'rm-esign-send', fitRationale: 'Migrated to signers[].email (v3).' },
    ],
    trace: [
      { id: 't10-1', agent: 'mcp', to: 'external', text: 'Subscribe agreement.completed (production)', tone: 'call' },
      { id: 't10-2', agent: 'mcp', to: 'external', text: 'Patch "send for signature": signer_email → signers[].email', tone: 'call' },
      { id: 't10-3', agent: 'mcp', to: 'llm', text: 'Production: healthy', tone: 'success' },
    ],
  },
  {
    id: 'l2-learn',
    scene: 'loop2',
    title: 'The knowledge graph compounds',
    view: 'graph',
    narration:
      'The KG learns again. The Loop 1 pattern was reused with no edits, so its confidence rises. A new contract-to-cash pattern is derived from it. Each integration teaches the platform something the next one can use.',
    paperRef: 'Competitive dynamics: "each new integration teaches the system"',
    trace: [
      { id: 't11-1', agent: 'llm', to: 'kg', text: 'Learn pattern from int-2; reinforce reused pattern', tone: 'call' },
      { id: 't11-2', agent: 'kg', text: 'Pattern "CRM → Billing" confidence 0.81 → 0.92; +1 derived pattern; +3 mappings', tone: 'success' },
    ],
    kg: {
      addNodes: [
        n('pat-c2c', 'Pattern: contract-to-cash (e-Signature → CRM → Billing)', 'pattern', undefined, 'loop2', { confidence: 0.88 }),
      ],
      addEdges: [
        e('pat-c2c', 'DERIVED_FROM', 'pat-crm-billing', 'loop2'),
        e('pat-c2c', 'IMPLEMENTS', 'ep-esign-v3-webhook', 'loop2'),
        e('pat-c2c', 'IMPLEMENTS', 'ep-billing-invoices', 'loop2'),
        e('pat-c2c', 'IMPLEMENTS', 'rm-contract-to-cash', 'loop2'),
        e('f-agr-amount', 'MAPS_TO', 'f-inv-total', 'loop2'),
        e('sys-esign', 'CONNECTS_TO', 'sys-billing', 'loop2'),
        e('pat-crm-esign-send', 'IMPLEMENTS', 'f-agr-signers', 'loop2'),
      ],
      updateNodes: [{ id: 'pat-crm-billing', confidence: 0.92, note: 'Reused once with 0 human edits' }],
    },
    highlight: ['pat-c2c', 'pat-crm-billing', 'rm-contract-to-cash'],
    learningOff: {
      narration:
        'Ablation: with learning OFF, nothing new reaches the KG. The next integration will start from scratch again and need the same corrections.',
      trace: [{ id: 't11-1', agent: 'kg', text: 'KG learning disabled: no write-back', tone: 'warn' }],
      kg: { addNodes: [], addEdges: [], updateNodes: [] },
      highlight: [],
    },
  },

  // ============================================================ PROOF
  {
    id: 'proof',
    scene: 'proof',
    title: 'Proof: a self-improving platform',
    view: 'learning',
    narration:
      'Loop 1 needed 2 conversation turns and 3 human corrections. Loop 2 was started by the agent from a stored roadmap intent and needed none, because the KG remembered. Turn KG learning off and replay Loop 2 to see the difference. This is the paper\'s compound advantage, in miniature.',
    paperRef: 'Conclusions; Wessel et al. (2025): four mechanisms',
    learningOff: {
      narration:
        'Ablation result: with KG learning OFF, Loop 2 needed the same 3 human corrections as Loop 1, and the graph did not grow. The agent still found the new API and matched it to the roadmap, but it did not get better. Turn learning back on to see the compound advantage.',
    },
  },
]

export const SCENES: { id: Step['scene']; label: string }[] = [
  { id: 'setup', label: 'Setup' },
  { id: 'loop1', label: 'Loop 1 · Intent' },
  { id: 'loop2', label: 'Loop 2 · New API' },
  { id: 'proof', label: 'Proof' },
]
