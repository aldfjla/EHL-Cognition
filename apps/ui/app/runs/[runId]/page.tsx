"use client";

/**
 * MISSION CONTROL — the live view of one run.
 *
 * This is the screen the whole project is pointed at: it shows that an
 * engineering team of agents exists, what each member is doing right now, and
 * how they are talking to each other.
 *
 * Layout intent (three columns on a wide projector):
 *   left    - PipelineTimeline: where the run is, top to bottom
 *   centre  - AgentGrid over ScenarioMatrix: the team and the evidence
 *   right   - TeamChat over AgentGraph: the conversation and its shape
 * with DiffViewer, VideoCompare and ReportView appearing below once the run
 * reaches VERIFY and REPORT.
 *
 * All state comes from `useEventStream` — no child fetches anything.
 */

import { use } from "react";

import AgentGraph from "@/components/AgentGraph";
import AgentGrid from "@/components/AgentGrid";
import DiffViewer from "@/components/DiffViewer";
import PipelineTimeline from "@/components/PipelineTimeline";
import ReportView from "@/components/ReportView";
import ScenarioMatrix from "@/components/ScenarioMatrix";
import TeamChat from "@/components/TeamChat";
import VideoCompare from "@/components/VideoCompare";
import { useEventStream } from "@/lib/useEventStream";

export default function MissionControlPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = use(params);
  const state = useEventStream(runId);

  // TODO(build): render a connection banner when state.connection is
  // "reconnecting" — a silently stale dashboard is worse than a loud one.

  return (
    <main className="p-6">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <h1 className="font-mono text-lg font-semibold">{runId}</h1>
          <p className="text-sm text-slate-400">
            {state.run?.repo ?? "—"} @ {state.run?.commit_sha?.slice(0, 7) ?? "—"}
          </p>
        </div>
        <span className="stub-label">{state.connection}</span>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[220px_1fr_380px]">
        <aside>
          <PipelineTimeline stage={state.run?.stage ?? null} run={state.run} />
        </aside>

        <section className="space-y-4">
          <AgentGrid agents={state.agents} />
          <ScenarioMatrix scenarios={state.scenarios} clusters={state.clusters} />
        </section>

        <aside className="space-y-4">
          <TeamChat messages={state.messages} agents={state.agents} />
          <AgentGraph agents={state.agents} messages={state.messages} />
        </aside>
      </div>

      <section className="mt-4 space-y-4">
        <VideoCompare incidents={state.report?.incidents ?? []} />
        <DiffViewer diff={state.report?.diff ?? null} />
        <ReportView report={state.report} />
      </section>
    </main>
  );
}
