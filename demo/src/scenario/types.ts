// Shared contract for the PMAIP demo. The scenario (data.ts) is a list of Steps;
// the engine folds steps 0..current into a DemoState that every view renders.

export type AgentKind = 'user' | 'llm' | 'kg' | 'mcp' | 'governance' | 'context' | 'external'

export type TraceTone = 'call' | 'return' | 'info' | 'warn' | 'success'

export interface TraceEvent {
  id: string
  agent: AgentKind
  /** Target of the call, e.g. 'kg' when the LLM agent queries the KG agent. */
  to?: AgentKind
  /** Events sharing a lane number run in parallel (Fig. 1 rows). */
  lane?: number
  text: string
  detail?: string
  tone?: TraceTone
}

export type NodeType =
  | 'system'
  | 'business_object'
  | 'data_field'
  | 'api_endpoint'
  | 'pattern'
  | 'rule'
  | 'process'
  | 'roadmap'

export type EdgeType =
  | 'BELONGS_TO'
  | 'HAS_FIELD'
  | 'MAPS_TO'
  | 'EQUIVALENT_TO'
  | 'CONNECTS_TO'
  | 'TRIGGERS'
  | 'REQUIRES'
  | 'DERIVED_FROM'
  | 'APPLIES_TO'
  | 'IMPLEMENTS'
  | 'DEPRECATES'

export type LearnedIn = 'seed' | 'loop1' | 'loop2'

export interface KGNode {
  id: string
  label: string
  type: NodeType
  /** Owning system node id, used for clustering/colour. */
  system?: string
  learnedIn: LearnedIn
  /** 0..1, shown for patterns and rules. */
  confidence?: number
  note?: string
}

export interface KGEdge {
  id: string
  source: string
  target: string
  type: EdgeType
  learnedIn: LearnedIn
}

export type RoadmapColumn = 'now' | 'next' | 'later' | 'shipped'
export type RoadmapStatus = 'planned' | 'blocked' | 'in_progress' | 'in_review' | 'shipped'

export interface RoadmapItem {
  id: string
  title: string
  description: string
  /** The stored product intent, in natural language. */
  intent: string
  column: RoadmapColumn
  status: RoadmapStatus
  quarter: string
  owner: string
  /** KG node ids this item needs. */
  requires: string[]
  blockedReason?: string
  /** Set by the roadmap-matcher agent, 0..1. */
  fitScore?: number
  fitRationale?: string
  integrationId?: string
}

export type ApiChangeKind = 'added' | 'deprecated' | 'changed'

export interface ApiChange {
  kind: ApiChangeKind
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'WEBHOOK' | 'FIELD'
  path: string
  summary: string
}

export type ApiReleaseStatus = 'new' | 'analyzing' | 'analyzed'

export interface ApiRelease {
  id: string
  app: string
  version: string
  previousVersion: string
  publishedAt: string
  status: ApiReleaseStatus
  changes: ApiChange[]
  /** MCP tools auto-generated from the spec. */
  mcpTools?: string[]
  impact?: string[]
}

export type MappingOrigin = 'kg_reused' | 'kg_inferred' | 'llm_new' | 'human_edit'

export interface MappingRow {
  id: string
  source: string
  target: string
  transform?: string
  origin: MappingOrigin
  confidence: number
  flag?: string
}

export interface TestResult {
  name: string
  passed: boolean
}

export interface RiskCheck {
  label: string
  level: 'low' | 'medium' | 'high'
  note: string
}

export interface Approval {
  role: 'pm' | 'eng'
  by: string
  comment: string
}

export type IntegrationStatus =
  | 'planned'
  | 'in_review'
  | 'approved'
  | 'deploying'
  | 'live'

export interface Integration {
  id: string
  name: string
  loop: 1 | 2
  source: string
  target: string
  trigger: string
  status: IntegrationStatus
  /** Who started it: a person stating intent, or the agent from a stored roadmap intent. */
  initiatedBy: 'user_intent' | 'agent_from_roadmap'
  mappings: MappingRow[]
  code: string
  tests: TestResult[]
  risks: RiskCheck[]
  approvals: { pm: Approval | null; eng: Approval | null }
  humanEdits: number
  turns: number
  confidence: number
  patternReused?: string
}

export type PipelineStageName = 'sandbox' | 'staging' | 'canary' | 'production'
export type PipelineStageStatus = 'pending' | 'running' | 'passed'

export interface PipelineStage {
  name: PipelineStageName
  status: PipelineStageStatus
  note: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'agent'
  text: string
  options?: string[]
}

export type View = 'roadmap' | 'graph' | 'workspace' | 'inbox' | 'review' | 'pipeline' | 'learning'

export type GateKind = 'type_intent' | 'approve_pm' | 'approve_eng' | 'action'

export interface Gate {
  kind: GateKind
  /** Button label. */
  label: string
  /** For type_intent: the text the presenter "types". */
  prefill?: string
}

export interface StepEffects {
  trace?: TraceEvent[]
  chat?: ChatMessage[]
  kg?: { addNodes?: KGNode[]; addEdges?: KGEdge[]; updateNodes?: (Partial<KGNode> & { id: string })[] }
  /** Node ids to spotlight while this step is current (not accumulated). */
  highlight?: string[]
  roadmap?: (Partial<RoadmapItem> & { id: string })[]
  apiRelease?: Partial<ApiRelease> & { id: string }
  integration?: Partial<Integration> & { id: string }
  /** Replace these mapping rows (by id) or append if new. */
  mappings?: { integrationId: string; rows: MappingRow[] }
  pipeline?: { integrationId: string; stages: PipelineStage[] }
}

export interface Step extends StepEffects {
  id: string
  scene: 'setup' | 'loop1' | 'loop2' | 'proof'
  title: string
  view: View
  /** Presenter narration, shown in the story panel. */
  narration: string
  /** Paper anchor, e.g. "Fig. 2" or "Findings: integration governance". */
  paperRef?: string
  /** If set, the presenter must take this action to advance. */
  gate?: Gate
  /** Overrides applied when the KG-learning ablation switch is OFF. */
  learningOff?: Partial<StepEffects> & { narration?: string; gate?: Gate; title?: string }
}

export interface DemoOptions {
  learningEnabled: boolean
}

export interface KGSnapshot {
  stepIndex: number
  nodes: number
  edges: number
  patterns: number
}

export interface DemoState {
  stepIndex: number
  step: Step
  options: DemoOptions
  /** Trace events from the current step only. */
  trace: TraceEvent[]
  /** All trace events so far (for the full log). */
  traceLog: TraceEvent[]
  chat: ChatMessage[]
  nodes: KGNode[]
  edges: KGEdge[]
  highlight: string[]
  roadmap: RoadmapItem[]
  apiReleases: ApiRelease[]
  integrations: Integration[]
  pipelines: Record<string, PipelineStage[]>
  kgHistory: KGSnapshot[]
}
