"use client";

/**
 * Stage progress rail — where the run is, top to bottom.
 *
 * Renders STAGE_ORDER as a vertical rail with the current stage highlighted,
 * completed stages dimmed, and the terminal state called out. The one component
 * that answers "how far along is this?" at a glance from across a room.
 */

import clsx from "clsx";

import {
  STAGE_ORDER,
  TERMINAL_STAGES,
  type Agent,
  type RobotModel,
  type Run,
  type Stage,
} from "@/lib/types";

export interface PipelineTimelineProps {
  /** Current stage, or null before the run object arrives. */
  stage: Stage | null;
  /** Full run, for the terminal-state summary at the foot of the rail. */
  run: Run | null;
  /** Roster, for the agent count next to fan-out stages. */
  agents?: Agent[];
}

/** Stages that fan out one agent per cluster, and the roles that staff them. */
const FANOUT_ROLES: Partial<Record<Stage, Agent["role"]>> = {
  RESOLVE_MODEL: "modeler",
  BUILD_HARNESS: "harness_builder",
  DESIGN_SCENARIOS: "scenario_designer",
  INVESTIGATE: "investigator",
  FIX: "fixer",
  VERIFY: "reviewer",
  REPORT: "reporter",
};

type StageState = "done" | "active" | "todo";

/**
 * Where a stage sits relative to the run. A terminal stage off the happy path
 * (`FAILED_UNRESOLVED`, `PASSED_CLEAN`) means everything on the rail is behind
 * us, so the rail shows the whole thing as done.
 */
function stageState(stage: Stage, current: Stage | null): StageState {
  if (current === null) return "todo";
  if (stage === current) return "active";

  const currentIndex = STAGE_ORDER.indexOf(current);
  const index = STAGE_ORDER.indexOf(stage);
  if (currentIndex === -1) {
    // Off-rail terminal state: PASSED_CLEAN skips the repair stages.
    if (current === "PASSED_CLEAN") {
      return index <= STAGE_ORDER.indexOf("RUN_SUITE") ? "done" : "todo";
    }
    return "done";
  }
  return index < currentIndex ? "done" : "todo";
}

function verdictLine(run: Run | null): { text: string; tone: string } {
  if (run === null) return { text: "awaiting run", tone: "text-slate-500" };
  switch (run.stage) {
    case "PASSED_CLEAN":
      return { text: "Passed clean — nothing to fix", tone: "text-status-passed" };
    case "PR_OPENED":
      return { text: "Fixed — pull request opened", tone: "text-status-passed" };
    case "FAILED_UNRESOLVED":
      return {
        text: "Unresolved — iteration budget exhausted",
        tone: "text-status-failed",
      };
    default:
      return { text: `in progress · ${run.stage}`, tone: "text-status-running" };
  }
}

export default function PipelineTimeline({
  stage,
  run,
  agents = [],
}: PipelineTimelineProps) {
  const verdict = verdictLine(run);
  const terminal = run !== null && TERMINAL_STAGES.includes(run.stage);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="stub-label">Pipeline</div>

      <ol className="mt-3 space-y-0">
        {STAGE_ORDER.map((s, i) => {
          const state = stageState(s, stage);
          const role = FANOUT_ROLES[s];
          const count = role
            ? agents.filter((agent) => agent.role === role).length
            : 0;

          return (
            <li key={s} className="relative flex items-start gap-2 pb-3 last:pb-0">
              {i < STAGE_ORDER.length - 1 && (
                <span
                  aria-hidden
                  className={clsx(
                    "absolute left-[5px] top-4 h-full w-px",
                    state === "done" ? "bg-status-passed/40" : "bg-surface-border",
                  )}
                />
              )}
              <span
                aria-hidden
                className={clsx(
                  "relative mt-1 h-[11px] w-[11px] shrink-0 rounded-full border",
                  state === "done" && "border-status-passed bg-status-passed/70",
                  state === "active" &&
                    "animate-pulse border-status-running bg-status-running",
                  state === "todo" && "border-surface-border bg-surface",
                )}
              />
              <div className="min-w-0 flex-1">
                <div
                  className={clsx(
                    "font-mono text-[11px] leading-4",
                    state === "active" && "font-semibold text-status-running",
                    state === "done" && "text-slate-400",
                    state === "todo" && "text-slate-600",
                  )}
                >
                  {s}
                </div>
                {count > 0 && (
                  <div className="font-mono text-[10px] text-slate-500">
                    {count} agent{count === 1 ? "" : "s"}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      <div
        className={clsx(
          "mt-3 border-t border-surface-border pt-3 text-xs",
          verdict.tone,
          terminal && "font-semibold",
        )}
      >
        {verdict.text}
      </div>

      {run?.suite && (
        <p className="mt-2 font-mono text-[10px] text-slate-500">
          suite {run.suite.passed}/{run.suite.total} passed
        </p>
      )}
      {run?.robot_model && <ModelProvenance model={run.robot_model} />}
      {run?.error && (
        <p className="mt-2 text-[11px] text-status-error">{run.error}</p>
      )}
    </div>
  );
}

/**
 * Where the simulated robot came from.
 *
 * The suite's verdict only means something if the model is the right robot, so
 * an approximate model and an unknown license are stated rather than hidden.
 */
function ModelProvenance({ model }: { model: RobotModel }) {
  const steps = model.processing_steps ?? [];
  return (
    <div className="mt-2 space-y-1 border-t border-surface-border pt-2 font-mono text-[10px] text-slate-500">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span className="truncate text-slate-400">
          {model.name ?? "model"} · {model.source}
        </span>
        {model.dof != null && <span>{model.dof} dof</span>}
        {model.approximate && (
          <span className="text-status-blocked">approximate</span>
        )}
        {model.cache_hit && <span>cached</span>}
      </p>
      {model.provenance && (
        <p className="break-words" title={model.provenance}>
          {model.provenance}
        </p>
      )}
      {steps.length > 0 && <p className="truncate">{steps.join(" → ")}</p>}
      <p>
        {model.license ?? (
          <span className="text-status-blocked">license unknown</span>
        )}
      </p>
    </div>
  );
}
