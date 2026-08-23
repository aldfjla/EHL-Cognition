/**
 * TypeScript mirror of `packages/contracts/schemas/*.json`.
 *
 * Hand-maintained on purpose — see `packages/contracts/README.md` for why, and
 * for the codegen escape hatch when this stops being reasonable. Each interface
 * names the schema file it tracks; changing one without the other is a bug.
 */

// ---- run.json -------------------------------------------------------------

export type Stage =
  | "TRIGGERED"
  | "RESOLVE_MODEL"
  | "BUILD_HARNESS"
  | "DESIGN_SCENARIOS"
  | "RUN_SUITE"
  | "CLUSTER_FAILURES"
  | "INVESTIGATE"
  | "FIX"
  | "VERIFY"
  | "REPORT"
  | "PR_OPENED"
  | "PASSED_CLEAN"
  | "FAILED_UNRESOLVED";

/** Happy-path order, used by PipelineTimeline to lay out the rail. */
export const STAGE_ORDER: Stage[] = [
  "TRIGGERED",
  "RESOLVE_MODEL",
  "BUILD_HARNESS",
  "DESIGN_SCENARIOS",
  "RUN_SUITE",
  "CLUSTER_FAILURES",
  "INVESTIGATE",
  "FIX",
  "VERIFY",
  "REPORT",
  "PR_OPENED",
];

export const TERMINAL_STAGES: Stage[] = [
  "PASSED_CLEAN",
  "PR_OPENED",
  "FAILED_UNRESOLVED",
];

export interface RobotModel {
  source: "menagerie" | "repo" | "generated";
  name?: string;
  model_path: string;
  dof?: number | null;
  confidence?: number | null;
  /** The concrete file, Menagerie entry or signal behind the pick. */
  provenance?: string | null;
  /** Only set when the source model states a license we recognise. */
  license?: string | null;
  processing_steps?: string[];
  approximate?: boolean;
  cache_hit?: boolean;
}

export interface SuiteStats {
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  baseline_pass_rate?: number | null;
}

export interface Run {
  id: string;
  stage: Stage;
  repo: string;
  branch: string;
  commit_sha: string;
  commit_message: string;
  pushed_by: string;
  robot_model: RobotModel | null;
  suite: SuiteStats | null;
  pull_request_url: string | null;
  report_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

/**
 * `GET /runs/{id}` response: the run plus everything mission control needs for
 * first paint, in one round trip (see apps/api/app/routers/runs.py).
 */
export interface RunDetail extends Run {
  scenarios: Scenario[];
  clusters: Cluster[];
}

// ---- repo.json ------------------------------------------------------------

/** Derived from run history: "running" while any run is non-terminal. */
export type RepoStatus = "dormant" | "running";

export interface ConnectedRepo {
  id: string;
  full_name: string;
  branch: string;
  suite_size: number | null;
  robot_menagerie: string;
  created_at: string;
  last_push_at: string | null;
  status: RepoStatus;
  latest_run: { id: string; stage: Stage; created_at: string } | null;
}

export interface MenagerieModelInfo {
  name: string;
  dof: number | null;
  kind: string;
}

/** POST /repos response: the repo plus what to paste into GitHub settings. */
export interface ConnectRepoResponse {
  repo: ConnectedRepo;
  webhook: { url: string; secret_configured: boolean };
}

// ---- internal database browser -------------------------------------------

export interface InternalDbColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
}

export interface InternalDbTable {
  name: string;
  primary_key: string | null;
  row_count: number;
  columns: InternalDbColumn[];
}

export type InternalDbValue = string | number | boolean | null;
export type InternalDbRow = Record<string, InternalDbValue>;

export interface InternalDbRows {
  columns: InternalDbColumn[];
  rows: InternalDbRow[];
  total: number;
}

// ---- agent.json -----------------------------------------------------------

export type Role =
  | "modeler"
  | "harness_builder"
  | "scenario_designer"
  | "investigator"
  | "fixer"
  | "reviewer"
  | "reporter";

/** Display names for the team roster. Keep in sync with docs/AGENT_ROLES.md. */
export const ROLE_LABELS: Record<Role, string> = {
  modeler: "Hardware Engineer",
  harness_builder: "Test Infrastructure",
  scenario_designer: "QA Lead",
  investigator: "Debugging Engineer",
  fixer: "Fix Engineer",
  reviewer: "Tech Lead",
  reporter: "Engineering Manager",
};

export type AgentStatus =
  | "queued"
  | "starting"
  | "working"
  | "blocked"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface Agent {
  id: string;
  run_id: string;
  session_id: string | null;
  session_url: string | null;
  role: Role;
  title: string;
  task: string;
  status: AgentStatus;
  iteration: number;
  max_iterations: number;
  cluster_id: string | null;
  scenario_ids: string[];
  parent_agent_id: string | null;
  finding_ids: string[];
  last_activity: string | null;
  /** Bounded history of transcript lines, newest last. Drives the live feed. */
  activity_log?: { text: string; ts: string }[];
  /** Embeddable live view of the agent's machine, when the session has one. */
  desktop_url: string | null;
  /** The failure being worked on, in the oracle's words. Not our instruction. */
  issue: string | null;
  /** Coarse phase inside the agent's own work. */
  step: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

// ---- message.json ---------------------------------------------------------

export type Speaker = Role | "orchestrator";
export type Recipient = Speaker | "broadcast";

export type MessageKind =
  | "hypothesis"
  | "finding"
  | "question"
  | "answer"
  | "verdict"
  | "handoff";

export interface Ref {
  type: "scenario" | "finding" | "artifact" | "commit" | "cluster" | "agent";
  id: string;
  label?: string;
}

export interface Message {
  id: string;
  run_id: string;
  from_agent_id: string | null;
  to_agent_id: string | null;
  from_role: Speaker;
  to_role: Recipient;
  kind: MessageKind;
  body: string;
  refs: Ref[];
  ts: string;
}

// ---- scenario.json --------------------------------------------------------

export type ScenarioStatus = "pending" | "running" | "passed" | "failed" | "error";

export interface CriterionResult {
  id: string;
  passed: boolean;
  value?: number | string | null;
  threshold?: number | string | null;
  detail?: string | null;
}

export interface Scenario {
  id: string;
  run_id: string;
  index: number;
  seed: number;
  label: string;
  params: Record<string, number | string | boolean | unknown[]>;
  status: ScenarioStatus;
  attempt: number;
  duration_s: number | null;
  sim_time_s: number | null;
  criteria: CriterionResult[];
  diagnosis: string | null;
  cluster_id: string | null;
  video_path: string | null;
  /** Latest rendered frame while running; null once video_path takes over. */
  live_frame_path: string | null;
  worker_id: string | null;
  /** Fraction of the simulated horizon completed. Advisory only. */
  progress: number | null;
  trace_path: string | null;
  error: string | null;
}

export interface Cluster {
  id: string;
  run_id: string;
  label: string;
  scenario_ids: string[];
  signature: string;
  size: number;
}

// ---- finding.json ---------------------------------------------------------

export type FindingKind =
  | "root_cause"
  | "patch"
  | "constraint"
  | "observation"
  | "verification";

export type FindingStatus = "proposed" | "confirmed" | "refuted" | "superseded";

export interface Finding {
  id: string;
  run_id: string;
  author_agent_id: string | null;
  author_role: Speaker;
  kind: FindingKind;
  summary: string;
  detail: string;
  cluster_id: string | null;
  scenario_ids: string[];
  files: string[];
  confidence: number;
  status: FindingStatus;
  superseded_by: string | null;
  created_at: string;
}

// ---- report.json ----------------------------------------------------------

export type Verdict = "clean" | "fixed" | "unresolved";

export interface Incident {
  cluster_id: string;
  title: string;
  affected_scenarios: number;
  root_cause: string;
  resolution: string;
  files_changed: string[];
  before_video: string | null;
  after_video: string | null;
  status: "fixed" | "unresolved";
}

export interface Report {
  id: string;
  run_id: string;
  verdict: Verdict;
  title: string;
  summary: string;
  incidents: Incident[];
  diff: string | null;
  before: SuiteStats | null;
  after: SuiteStats | null;
  pull_request_url: string | null;
  markdown_path: string | null;
  created_at: string;
}

// ---- event.json -----------------------------------------------------------

export type EventType =
  | "run.created"
  | "run.stage_changed"
  | "run.finished"
  | "agent.created"
  | "agent.updated"
  | "agent.status_changed"
  | "agent.activity"
  | "message.sent"
  | "scenario.created"
  | "scenario.started"
  | "scenario.progress"
  | "scenario.finished"
  | "suite.progress"
  | "worker.pool_changed"
  | "finding.created"
  | "finding.updated"
  | "artifact.created"
  | "report.created"
  | "error";

export interface RunEvent<T = Record<string, unknown>> {
  id: string;
  run_id: string;
  seq: number;
  type: EventType;
  ts: string;
  data: T;
}

// ---- typed payloads -------------------------------------------------------

/**
 * Payload shapes per event type, from docs/EVENT_PROTOCOL.md. Full objects for
 * `*.created`/`*.finished`, partial patches for `*_changed`.
 */
export interface EventPayloads {
  "run.created": Run;
  "run.stage_changed": { stage: Stage; previous_stage: Stage | null };
  "run.finished": Run;
  "agent.created": Agent;
  "agent.status_changed": {
    agent_id: string;
    status: AgentStatus;
    previous_status: AgentStatus | null;
    /** Present when a terminal status is emitted. */
    finished_at?: string | null;
  };
  "agent.updated": Partial<Agent> & { agent_id: string };
  "agent.activity": { agent_id: string; text: string; ts: string };
  "message.sent": Message;
  "scenario.created": Scenario;
  "scenario.started": { scenario_id: string; worker_id?: string | null };
  "scenario.progress": {
    scenario_id: string;
    progress: number;
    sim_time_s: number;
    live_frame_path?: string | null;
  };
  "scenario.finished": Scenario;
  "suite.progress": {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    running?: number;
    workers?: number;
  };
  "worker.pool_changed": {
    workers: number;
    busy: number;
    queued: number;
    reason?: string;
  };
  "finding.created": Finding;
  "finding.updated": {
    finding_id: string;
    status: FindingStatus;
    superseded_by?: string | null;
  };
  "artifact.created": {
    kind: string;
    path: string;
    scenario_id?: string | null;
    run_id: string;
  };
  "report.created": Report;
  error: { stage: Stage | null; message: string; fatal: boolean };
}

/**
 * Discriminated union over `type`, so reducers get narrowed payloads instead of
 * `Record<string, unknown>`.
 */
export type TypedRunEvent = {
  [K in EventType]: RunEvent<EventPayloads[K]> & { type: K };
}[EventType];
