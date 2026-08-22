"use client";

/**
 * MISSION CONTROL — the live view of one run.
 *
 * This is the screen the whole project is pointed at: it shows that an
 * engineering team of agents exists, what each member is doing right now, and
 * what the simulations are doing *right now*.
 *
 * Layout intent: the vital signs (counters, pipeline rail) stay above the fold
 * on every view; everything else is split across four tabs so no single screen
 * has to carry the whole run:
 *   1 Overview   — the live simulation wall plus the team chat;
 *   2 Agents     — roster, chain of command, agent ops, communication graph;
 *   3 Scenarios  — the full randomized-world matrix;
 *   4 Evidence   — videos, diff and report once the run produces them.
 *
 * Cross-references (a chat chip naming a scenario, a graph node naming an
 * agent) switch to the right tab and focus the target — the tabs organize the
 * screen, they never hide a lead.
 *
 * All state comes from one stream (`useLiveRun` wraps `useEventStream`) — no
 * child fetches anything.
 */

import clsx from "clsx";
import Link from "next/link";
import { use, useEffect, useState } from "react";

import AgentGraph from "@/components/AgentGraph";
import AgentGrid from "@/components/AgentGrid";
import AgentTree from "@/components/AgentTree";
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

type Tab = "overview" | "agents" | "scenarios" | "evidence";

const TABS: Array<{ id: Tab; label: string; key: string }> = [
  { id: "overview", label: "Overview", key: "1" },
  { id: "agents", label: "Agents", key: "2" },
  { id: "scenarios", label: "Scenarios", key: "3" },
  { id: "evidence", label: "Evidence", key: "4" },
];

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

export default function MissionControlPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = use(params);
  const state = useLiveRun(runId);
  const [tab, setTab] = useState<Tab>("overview");
  const [focusedAgent, setFocusedAgent] = useState<string | null>(null);
  const [hoveredCluster, setHoveredCluster] = useState<string | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);

  const replay = state.replay || isMockRun(runId);
  const stale = state.connection === "reconnecting" || state.connection === "closed";

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isEditable(event.target)) return;
      const target = TABS.find(({ key }) => key === event.key);
      if (target !== undefined) setTab(target.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (state.missing) {
    return (
      <main className="px-6 py-8">
        <div className="mx-auto max-w-2xl rounded border border-surface-border bg-surface-raised p-8">
          <p className="stub-label text-status-blocked">run lookup · 404</p>
          <h1 className="mt-3 font-mono text-2xl font-semibold text-slate-100">
            This run doesn&apos;t exist
          </h1>
          <p className="mt-3 text-sm leading-6 text-slate-400">
            <span className="font-mono text-slate-200">{runId}</span> may have
            been deleted, or the ID may be mistyped.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link
              href="/runs"
              className="inline-flex items-center gap-3 rounded-full border border-surface-border px-5 py-2.5 font-mono text-base font-medium text-slate-200 transition-colors hover:border-sky-500/70 hover:bg-surface-raised hover:text-sky-300"
            >
              <span aria-hidden className="text-2xl leading-none">
                ←
              </span>
              Back to runs
            </Link>
            <Link
              href="/runs/run_replay_demo"
              className="font-mono text-xs text-sky-400 hover:underline"
            >
              view scripted demo run →
            </Link>
          </div>
        </div>
      </main>
    );
  }

  const onSelectRef = (ref: Ref): void => {
    if (ref.type === "scenario") {
      setSelectedScenario(ref.id);
      setTab("scenarios");
    }
    if (ref.type === "cluster") setHoveredCluster(ref.id);
    if (ref.type === "agent") {
      setFocusedAgent(ref.id);
      setTab("agents");
    }
  };

  const focusAgent = (agentId: string | null): void => {
    setFocusedAgent(agentId);
    if (agentId !== null) setTab("agents");
  };

  return (
    <main className="px-6 py-8">
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

      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <Link
              href="/runs"
              className="inline-flex items-center gap-3 rounded-full border border-surface-border px-5 py-2.5 font-mono text-base font-medium text-slate-200 transition-colors hover:border-sky-500/70 hover:bg-surface-raised hover:text-sky-300"
            >
              <span aria-hidden className="text-2xl leading-none">
                ←
              </span>
              Back
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
          {state.run?.pull_request_url ? (
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
          ) : (
            // A pull request only exists when the whole suite came back green;
            // say so rather than leaving an empty space that reads as pending.
            state.run?.stage === "FAILED_UNRESOLVED" && (
              <div className="font-mono text-xs text-status-failed">
                no pull request — suite still red
              </div>
            )
          )}
        </div>
      </header>

      {/* Vital signs — always above the fold, on every tab. */}
      <LiveCounters
        stage={state.run?.stage ?? null}
        scenarios={state.scenarios}
        agents={state.agents}
        pool={state.live.pool}
      />

      <nav className="mt-6 flex items-center gap-1 border-b border-surface-border">
        {TABS.map(({ id, label, key }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={clsx(
              "-mb-px flex items-center gap-2 rounded-t border-b-2 px-3 py-2 font-mono text-xs",
              tab === id
                ? "border-sky-500 text-sky-300"
                : "border-transparent text-slate-500 hover:text-slate-300",
            )}
          >
            {label}
            <kbd className="rounded bg-surface-raised px-1 text-[10px] text-slate-600">
              {key}
            </kbd>
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <div className="mt-6 grid grid-cols-1 gap-5 xl:grid-cols-[220px_1fr_380px]">
          <aside>
            <PipelineTimeline
              stage={state.run?.stage ?? null}
              run={state.run}
              agents={state.agents}
            />
          </aside>
          <section>
            <LiveWall
              runId={runId}
              scenarios={state.scenarios}
              live={state.live}
              synthetic={replay}
            />
          </section>
          <aside>
            <TeamChat
              messages={state.messages}
              agents={state.agents}
              onSelectRef={onSelectRef}
            />
          </aside>
        </div>
      )}

      {tab === "agents" && (
        <div className="mt-6 grid grid-cols-1 gap-5 xl:grid-cols-[1fr_380px]">
          <section className="space-y-5">
            <AgentTree
              agents={state.agents}
              messages={state.messages}
              incidents={state.report?.incidents ?? []}
              focusedAgentId={focusedAgent}
              onFocusAgent={setFocusedAgent}
            />
            <AgentGrid
              agents={state.agents}
              activeClusterId={hoveredCluster}
              focusedAgentId={focusedAgent}
            />
            <AgentOpsPanel
              runId={runId}
              agents={state.agents}
              onFocusAgent={focusAgent}
            />
          </section>
          <aside className="space-y-5">
            <AgentGraph
              agents={state.agents}
              messages={state.messages}
              onSelectAgent={focusAgent}
            />
            <TeamChat
              messages={state.messages}
              agents={state.agents}
              onSelectRef={onSelectRef}
            />
          </aside>
        </div>
      )}

      {tab === "scenarios" && (
        <div className="mt-6 space-y-5">
          <ScenarioMatrix
            scenarios={state.scenarios}
            clusters={state.clusters}
            selectedScenarioId={selectedScenario}
            onSelectScenario={setSelectedScenario}
            onHoverCluster={setHoveredCluster}
          />
          <LiveWall
            runId={runId}
            scenarios={state.scenarios}
            live={state.live}
            synthetic={replay}
          />
        </div>
      )}

      {tab === "evidence" && (
        <section className="mt-6 space-y-5">
          <VideoCompare incidents={state.report?.incidents ?? []} />
          <DiffViewer diff={state.report?.diff ?? null} />
          <ReportView report={state.report} />
        </section>
      )}
    </main>
  );
}
