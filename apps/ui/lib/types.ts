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
  | "agent.status_changed"
  | "agent.activity"
  | "message.sent"
  | "scenario.created"
  | "scenario.started"
  | "scenario.finished"
  | "suite.progress"
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

// TODO(build): add discriminated-union narrowing on RunEvent["type"] so
// reducers get typed payloads instead of Record<string, unknown>.
