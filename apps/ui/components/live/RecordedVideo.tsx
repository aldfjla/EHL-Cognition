"use client";

/**
 * The recorded video of a finished scenario, with an honest fallback: if the
 * artifact fails to load (missing file, 404), render the scenario's status
 * word instead of a blank player.
 */

import clsx from "clsx";
import { useState } from "react";

import * as api from "@/lib/api";
import type { Scenario, ScenarioStatus } from "@/lib/types";

const STATUS_TONE: Record<ScenarioStatus, string> = {
  pending: "text-status-pending",
  running: "text-status-running",
  passed: "text-status-passed",
  failed: "text-status-failed",
  error: "text-status-error",
};

export interface RecordedVideoProps {
  scenario: Scenario;
  videoPath: string;
  /** Show player controls (the focused view does; wall tiles do not). */
  controls?: boolean;
  className?: string;
}

export default function RecordedVideo({
  scenario,
  videoPath,
  controls = false,
  className,
}: RecordedVideoProps) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-1">
        <span
          className={clsx(
            "font-mono text-lg font-semibold uppercase tracking-widest",
            STATUS_TONE[scenario.status],
          )}
        >
          {scenario.status}
        </span>
        <span className="font-mono text-[10px] text-slate-500">
          recording unavailable
        </span>
      </div>
    );
  }

  return (
    <video
      src={api.artifactUrl(videoPath)}
      className={className}
      onError={() => setFailed(true)}
      controls={controls}
      muted
      loop
      playsInline
      autoPlay
    />
  );
}
