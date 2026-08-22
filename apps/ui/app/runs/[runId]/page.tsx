"use client";

/**
 * MISSION CONTROL — the live view of one run.
 *
 * This is the screen the whole project is pointed at: it shows that an
 * engineering team of agents exists, what each member is doing right now, and
 * what the simulations are doing *right now*.
 *
 * Layout intent (top to bottom, projector-first):
 *   1. counters — the run's vital signs: running/queued/passed/failed,
 *      agents working, worker pool saturation, current stage;
 *   2. the live simulation wall — one tile per running scenario with its
 *      feed, progress and worker; failures land loud and stay visible;
 *   3. the working row — PipelineTimeline | AgentGrid + AgentOpsPanel +
 *      ScenarioMatrix | TeamChat + AgentGraph;
 *   4. evidence — VideoCompare, DiffViewer, ReportView once they exist.
 *
 * All state comes from one stream (`useLiveRun` wraps `useEventStream`) — no
 * child fetches anything.
 */

import clsx from "clsx";
import Link from "next/link";
import { use, useState } from "react";

import AgentGraph from "@/components/AgentGraph";
import AgentGrid from "@/components/AgentGrid";
import AgentOpsPanel from "@/components/agents/AgentOpsPanel";
import DiffViewer from "@/components/DiffViewer";
import LiveCounters from "@/components/live/LiveCounters";
import LiveWall from "@/components/live/LiveWall";
import { useLiveRun } from "@/components/live/useLiveRun";
import PipelineTimeline from "@/components/PipelineTimeline";
import ReportView from "@/components/ReportView";
import ScenarioMatrix from "@/components/ScenarioMatrix";
import TeamChat from "@/components/TeamChat";
import VideoCompare from "@/components/VideoCompare";
import type { Ref } from "@/lib/types";
import type { ConnectionState } from "@/lib/useEventStream";
import { isMockRun } from "@/lib/useEventStream";

const CONNECTION_TONE: Record<ConnectionState, string> = {
  connecting: "text-slate-400",
  open: "text-status-passed",
  reconnecting: "text-status-blocked",
  closed: "text-slate-500",
};

export default function MissionControlPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = use(params);
  const state = useLiveRun(runId);
  const [focusedAgent, setFocusedAgent] = useState<string | null>(null);
  const [hoveredCluster, setHoveredCluster] = useState<string | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);

  const replay = state.replay || isMockRun(runId);
  const stale = state.connection === "reconnecting" || state.connection === "closed";

  const onSelectRef = (ref: Ref): void => {
    if (ref.type === "scenario") setSelectedScenario(ref.id);
    if (ref.type === "cluster") setHoveredCluster(ref.id);
    if (ref.type === "agent") setFocusedAgent(ref.id);
  };

  return (
    <main className="p-6">
      {/* A silently stale dashboard is worse than a loud one. */}
      {stale && !replay && (
        <div className="mb-4 rounded border border-status-blocked/60 bg-amber-950/30 px-3 py-2 text-xs text-status-blocked">
          {state.connection === "reconnecting"
            ? "Stream dropped — reconnecting and replaying from the last event seen. Numbers below may be behind."
            : "Stream closed. This view is a snapshot, not live."}
        </div>
      )}
      {state.error !== null && (
        <div className="mb-4 rounded border border-status-error/60 bg-rose-950/30 px-3 py-2 text-xs text-status-error">
          Infrastructure error: {state.error}
        </div>
      )}

      <header className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="flex items-baseline gap-2">
            <Link
              href="/"
              className="font-mono text-xs text-slate-500 hover:text-sky-400"
            >
              ← runs
            </Link>
            <h1 className="font-mono text-lg font-semibold">{runId}</h1>
            {replay && (
              <span className="rounded border border-sky-700 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-sky-300">
                replay
              </span>
            )}
          </div>
          <p className="text-sm text-slate-400">
            {state.run?.repo ?? "—"} @ {state.run?.commit_sha?.slice(0, 7) ?? "—"}
            {state.run?.commit_message ? ` · ${state.run.commit_message}` : ""}
          </p>
        </div>
        <div className="text-right">
          <span
            className={clsx(
              "stub-label",
              CONNECTION_TONE[state.connection],
            )}
          >
            {replay ? "scripted replay" : state.connection} · seq {state.seq}
          </span>
          {state.run?.pull_request_url && (
            <div>
              <a
                href={state.run.pull_request_url}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-xs text-sky-400 hover:underline"
              >
                pull request →
              </a>
            </div>
          )}
        </div>
      </header>

      {/* 1. Vital signs — always above the fold. */}
      <LiveCounters
        stage={state.run?.stage ?? null}
        scenarios={state.scenarios}
        agents={state.agents}
        pool={state.live.pool}
      />

      {/* 2. The live simulation wall. */}
      <div className="mt-4">
        <LiveWall
          runId={runId}
          scenarios={state.scenarios}
          live={state.live}
          synthetic={replay}
        />
      </div>

      {/* 3. The working row. */}
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[220px_1fr_380px]">
        <aside>
          <PipelineTimeline
            stage={state.run?.stage ?? null}
            run={state.run}
            agents={state.agents}
          />
        </aside>

        <section className="space-y-4">
          <AgentGrid
            agents={state.agents}
            activeClusterId={hoveredCluster}
            focusedAgentId={focusedAgent}
          />
          <AgentOpsPanel
            runId={runId}
            agents={state.agents}
            onFocusAgent={setFocusedAgent}
          />
          <ScenarioMatrix
            scenarios={state.scenarios}
            clusters={state.clusters}
            selectedScenarioId={selectedScenario}
            onSelectScenario={setSelectedScenario}
            onHoverCluster={setHoveredCluster}
          />
        </section>

        <aside className="space-y-4">
          <TeamChat
            messages={state.messages}
            agents={state.agents}
            onSelectRef={onSelectRef}
          />
          <AgentGraph
            agents={state.agents}
            messages={state.messages}
            onSelectAgent={setFocusedAgent}
          />
        </aside>
      </div>

      {/* 4. Evidence, once the run produces it. */}
      <section className="mt-4 space-y-4">
        <VideoCompare incidents={state.report?.incidents ?? []} />
        <DiffViewer diff={state.report?.diff ?? null} />
        <ReportView report={state.report} />
      </section>
    </main>
  );
}
