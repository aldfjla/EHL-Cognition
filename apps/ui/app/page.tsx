"use client";

/**
 * Runs index — every CI run, newest first.
 *
 * Entry point of the dashboard. Each row links to that run's mission control
 * page. Kept plain on purpose: the interesting screen is the run detail, and
 * this one exists to get there.
 *
 * Live on its own small subscription to `WS /ws/runs` so a push appears here
 * without a refresh; the per-run stream is the run page's business.
 */

import clsx from "clsx";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import * as api from "@/lib/api";
import { LIVE_MOCK_RUN_ID } from "@/lib/mockLive";
import { MOCK_RUN_ID } from "@/lib/mockRun";
import type { Run, Stage } from "@/lib/types";
import { WS_BASE } from "@/lib/useEventStream";

const STAGE_TONE: Partial<Record<Stage, string>> = {
  PASSED_CLEAN: "text-status-passed",
  PR_OPENED: "text-status-passed",
  FAILED_UNRESOLVED: "text-status-failed",
};

function byNewest(a: Run, b: Run): number {
  return Date.parse(b.created_at) - Date.parse(a.created_at);
}

function upsert(runs: Run[], run: Run): Run[] {
  const at = runs.findIndex((existing) => existing.id === run.id);
  if (at === -1) return [run, ...runs].sort(byNewest);
  const next = runs.slice();
  next[at] = { ...next[at], ...run };
  return next;
}

export default function RunsIndexPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (): Promise<void> => {
    try {
      const fetched = await api.listRuns();
      setRuns(fetched.slice().sort(byNewest));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Index-level feed: only run.* events matter here.
  useEffect(() => {
    let closed = false;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = (): void => {
      if (closed) return;
      const ws = new WebSocket(`${WS_BASE}/ws/runs`);
      socket = ws;

      ws.onmessage = (frame: MessageEvent<string>) => {
        let payload: unknown;
        try {
          payload = JSON.parse(frame.data);
        } catch {
          return;
        }
        const event = payload as { type?: string; data?: unknown };
        if (event.type === "run.created" || event.type === "run.finished") {
          setRuns((prev) => upsert(prev, event.data as Run));
          return;
        }
        // A stage change carries only the stage; the run id is on the envelope.
        if (event.type === "run.stage_changed") {
          const envelope = payload as { run_id?: string; data?: { stage?: Stage } };
          const stage = envelope.data?.stage;
          const runId = envelope.run_id;
          if (!stage || !runId) return;
          setRuns((prev) =>
            prev.map((run) => (run.id === runId ? { ...run, stage } : run)),
          );
        }
      };

      ws.onclose = () => {
        if (closed) return;
        // The index is not the demo surface; a slow, quiet retry is enough.
        timer = setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      closed = true;
      if (timer !== null) clearTimeout(timer);
      if (socket !== null) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, []);

  return (
    <main className="mx-auto max-w-5xl p-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Robot CI</h1>
        <p className="mt-1 text-sm text-slate-400">
          Autonomous CI for robot control code. Every push is simulated,
          debugged and fixed without a human in the loop.
        </p>
      </header>

      {error !== null && (
        <div className="mb-4 rounded border border-status-blocked/60 bg-amber-950/30 px-3 py-2 text-xs text-status-blocked">
          API unreachable ({error}). Start it with{" "}
          <code className="font-mono">make api</code>, or open the{" "}
          <Link
            href={`/runs/${MOCK_RUN_ID}`}
            className="text-sky-300 hover:underline"
          >
            scripted replay
          </Link>{" "}
          — it needs no backend.
        </div>
      )}

      <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
        <div className="stub-label">
          Runs · {runs.length}
          {loading ? " · loading" : ""}
        </div>
        {runs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">
            No runs yet. Trigger one with{" "}
            <code className="font-mono text-slate-300">make seed</code> or push
            to the watched repo.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-surface-border">
            {runs.map((run) => (
              <li key={run.id}>
                <Link
                  href={`/runs/${run.id}`}
                  className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 hover:bg-slate-900/60"
                >
                  <span className="font-mono text-sm text-sky-400">{run.id}</span>
                  <span className="font-mono text-xs text-slate-400">
                    {run.repo}@{run.branch} · {run.commit_sha.slice(0, 7)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-slate-500">
                    {run.commit_message}
                  </span>
                  {run.suite && (
                    <span className="font-mono text-xs text-slate-400">
                      {run.suite.passed}/{run.suite.total}
                    </span>
                  )}
                  <span
                    className={clsx(
                      "font-mono text-[10px] uppercase tracking-widest",
                      STAGE_TONE[run.stage] ?? "text-status-running",
                    )}
                  >
                    {run.stage}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="mt-4 text-xs text-slate-500">
        No API running?{" "}
        <Link
          href={`/runs/${MOCK_RUN_ID}`}
          className="font-mono text-sky-400 hover:underline"
        >
          /runs/{MOCK_RUN_ID}
        </Link>{" "}
        plays a scripted run entirely in the browser, and{" "}
        <Link
          href={`/runs/${LIVE_MOCK_RUN_ID}`}
          className="font-mono text-sky-400 hover:underline"
        >
          /runs/{LIVE_MOCK_RUN_ID}
        </Link>{" "}
        demos the live simulation wall. Both are labelled as replays.
      </p>
    </main>
  );
}
