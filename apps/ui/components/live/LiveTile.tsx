"use client";

/**
 * One scenario on the wall.
 *
 * Shows the human label, seed, worker and progress over the live feed while
 * running, then transitions in place to a pass/fail card with the recorded
 * video (failures land loud — a red border and status chip, not a vanished
 * tile). Clicking focuses the tile large in the wall's overlay.
 *
 * Motion is deliberately cheap and short: the tile pops in when a worker picks
 * the scenario up, and pulses once in the outcome's colour when the verdict
 * lands, so a running -> passed flip is seen live on a projector. Both are
 * disabled under `prefers-reduced-motion`.
 *
 * Visibility is observed here (IntersectionObserver) and reported up; the
 * wall decides which visible tiles may hold a streaming connection.
 */

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

import type { Scenario, ScenarioStatus } from "@/lib/types";

import LiveFeed from "./LiveFeed";
import RecordedVideo from "./RecordedVideo";

const STATUS_TONE: Record<ScenarioStatus, string> = {
  pending: "text-status-pending",
  running: "text-status-running",
  passed: "text-status-passed",
  failed: "text-status-failed",
  error: "text-status-error",
};

const BORDER_TONE: Record<ScenarioStatus, string> = {
  pending: "border-surface-border",
  running: "border-status-running/50",
  passed: "border-status-passed/50",
  failed: "border-status-failed",
  error: "border-status-error",
};

/** Kept in sync with the `pop` / `settle-*` durations in tailwind.config.ts. */
const ENTER_MS = 300;
const SETTLE_MS = 560;

function isVerdict(status: ScenarioStatus): boolean {
  return status === "passed" || status === "failed" || status === "error";
}

export interface LiveTileProps {
  runId: string;
  scenario: Scenario;
  /** May hold an open MJPEG connection (visible + inside the stream budget). */
  streaming: boolean;
  /** Scripted replay — synthetic feed instead of HTTP. */
  synthetic: boolean;
  onVisibility: (scenarioId: string, visible: boolean) => void;
  onFocus: (scenarioId: string) => void;
}

export default function LiveTile({
  runId,
  scenario,
  streaming,
  synthetic,
  onVisibility,
  onFocus,
}: LiveTileProps) {
  const rootRef = useRef<HTMLButtonElement | null>(null);
  // The tile pops in once, then never again; without this the entry animation
  // would re-trigger every time the class list changes.
  const [entering, setEntering] = useState(true);
  const [settled, setSettled] = useState<ScenarioStatus | null>(null);
  const lastStatus = useRef<ScenarioStatus>(scenario.status);

  useEffect(() => {
    const timer = window.setTimeout(() => setEntering(false), ENTER_MS);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const previous = lastStatus.current;
    lastStatus.current = scenario.status;
    // Only the transition into a verdict is worth a pulse — a tile that
    // arrives already finished (late mount, replay scrub) just pops in.
    if (previous === scenario.status || previous === "pending") return;
    if (!isVerdict(scenario.status)) return;
    setSettled(scenario.status);
    const timer = window.setTimeout(() => setSettled(null), SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [scenario.status]);

  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => onVisibility(scenario.id, entry.isIntersecting),
      { threshold: 0.15 },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
      onVisibility(scenario.id, false);
    };
  }, [scenario.id, onVisibility]);

  const finished = isVerdict(scenario.status);
  const progressPct = Math.round((scenario.progress ?? 0) * 100);

  return (
    <button
      ref={rootRef}
      type="button"
      onClick={() => onFocus(scenario.id)}
      className={clsx(
        "group relative flex w-full flex-col overflow-hidden rounded-lg border text-left",
        "bg-surface-raised transition-colors duration-500 hover:border-sky-500/70",
        BORDER_TONE[scenario.status],
        settled === "passed" && "animate-settle-pass",
        settled !== null && settled !== "passed" && "animate-settle-fail",
        settled === null && entering && "animate-pop",
        "motion-reduce:animate-none",
      )}
      aria-label={`scenario ${scenario.label} — ${scenario.status}`}
    >
      <div className="relative aspect-video w-full overflow-hidden bg-slate-950">
        {finished && scenario.video_path ? (
          <RecordedVideo
            scenario={scenario}
            videoPath={scenario.video_path}
            className="h-full w-full object-cover"
          />
        ) : finished ? (
          <div className="flex h-full w-full items-center justify-center">
            <span
              className={clsx(
                "font-mono text-lg font-semibold uppercase tracking-widest",
                STATUS_TONE[scenario.status],
              )}
            >
              {scenario.status}
            </span>
          </div>
        ) : (
          <LiveFeed
            runId={runId}
            scenario={scenario}
            streaming={streaming}
            synthetic={synthetic}
            className="h-full w-full"
          />
        )}

        {/* Progress rail along the bottom of the feed. */}
        {scenario.status === "running" && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-slate-800">
            <div
              className="h-full bg-status-running transition-[width] duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        )}
      </div>

      <div className="flex items-baseline justify-between gap-2 px-2.5 py-1.5">
        <div className="min-w-0">
          <div className="truncate text-xs text-slate-200">{scenario.label}</div>
          <div className="font-mono text-[10px] text-slate-500">
            seed {scenario.seed}
            {scenario.worker_id ? ` · ${scenario.worker_id}` : ""}
            {scenario.status === "running" && scenario.sim_time_s !== null
              ? ` · t=${scenario.sim_time_s.toFixed(1)}s`
              : ""}
          </div>
        </div>
        <span
          className={clsx(
            "shrink-0 font-mono text-[10px] uppercase tracking-widest",
            STATUS_TONE[scenario.status],
          )}
        >
          {scenario.status === "running" ? `${progressPct}%` : scenario.status}
        </span>
      </div>
    </button>
  );
}
