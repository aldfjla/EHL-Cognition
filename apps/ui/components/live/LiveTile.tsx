"use client";

/**
 * One scenario on the wall.
 *
 * Shows the human label, seed, worker and progress over the live feed while
 * running, then transitions in place to a pass/fail card with the recorded
 * video (failures land loud — a red border and status chip, not a vanished
 * tile). Clicking focuses the tile large in the wall's overlay.
 *
 * Visibility is observed here (IntersectionObserver) and reported up; the
 * wall decides which visible tiles may hold a streaming connection.
 */

import clsx from "clsx";
import { useEffect, useRef } from "react";

import * as api from "@/lib/api";
import type { Scenario, ScenarioStatus } from "@/lib/types";

import LiveFeed from "./LiveFeed";

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

  const finished =
    scenario.status === "passed" ||
    scenario.status === "failed" ||
    scenario.status === "error";
  const progressPct = Math.round((scenario.progress ?? 0) * 100);

  return (
    <button
      ref={rootRef}
      type="button"
      onClick={() => onFocus(scenario.id)}
      className={clsx(
        "group relative flex w-full flex-col overflow-hidden rounded-lg border text-left transition-colors",
        "bg-surface-raised hover:border-sky-500/70",
        BORDER_TONE[scenario.status],
        scenario.status === "failed" && "animate-land",
      )}
      aria-label={`scenario ${scenario.label} — ${scenario.status}`}
    >
      <div className="relative aspect-video w-full overflow-hidden bg-slate-950">
        {finished && scenario.video_path ? (
          <video
            src={api.artifactUrl(scenario.video_path)}
            className="h-full w-full object-cover"
            muted
            loop
            playsInline
            autoPlay
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
