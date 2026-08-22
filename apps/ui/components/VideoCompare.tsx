"use client";

/**
 * Before/after clips of the same scenario seed.
 *
 * The closing argument of the demo: the identical randomized world, the robot
 * failing on the left and succeeding on the right, with nothing between them
 * but an agent's patch. Same seed on both sides or the comparison means
 * nothing — that constraint is why simkit's determinism is non-negotiable.
 */

import { useCallback, useRef } from "react";

import { artifactUrl } from "@/lib/api";
import type { Incident } from "@/lib/types";

export interface VideoCompareProps {
  /** Incidents carrying before/after artifact paths. */
  incidents: Incident[];
}

/** One pair, with play/pause kept in lockstep so the eye can compare frames. */
function Pair({ incident }: { incident: Incident }) {
  const before = useRef<HTMLVideoElement | null>(null);
  const after = useRef<HTMLVideoElement | null>(null);

  const sync = useCallback((action: "play" | "pause" | "restart") => {
    for (const node of [before.current, after.current]) {
      if (node === null) continue;
      if (action === "restart") {
        node.currentTime = 0;
        void node.play();
      } else if (action === "play") {
        void node.play();
      } else {
        node.pause();
      }
    }
  }, []);

  return (
    <article className="rounded border border-surface-border p-3">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-slate-200">{incident.title}</h3>
        <span
          className={
            incident.status === "fixed"
              ? "font-mono text-[10px] uppercase tracking-widest text-status-passed"
              : "font-mono text-[10px] uppercase tracking-widest text-status-failed"
          }
        >
          {incident.status}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => sync("restart")}
            className="rounded border border-surface-border px-2 py-0.5 font-mono text-[10px] text-slate-300 hover:border-sky-500 hover:text-sky-300"
          >
            replay both
          </button>
          <button
            type="button"
            onClick={() => sync("pause")}
            className="rounded border border-surface-border px-2 py-0.5 font-mono text-[10px] text-slate-300 hover:border-sky-500 hover:text-sky-300"
          >
            pause
          </button>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {(
          [
            ["before", incident.before_video, before, "text-status-failed"],
            ["after", incident.after_video, after, "text-status-passed"],
          ] as const
        ).map(([side, path, ref, tone]) => (
          <div key={side}>
            <div className={`stub-label ${tone}`}>{side}</div>
            {path === null ? (
              <div className="mt-1 flex aspect-video items-center justify-center rounded bg-slate-900 text-xs text-slate-500">
                no clip
              </div>
            ) : (
              <video
                ref={ref}
                src={artifactUrl(path)}
                muted
                loop
                playsInline
                controls
                onPlay={() => sync("play")}
                className="mt-1 w-full rounded bg-black"
              />
            )}
            <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
              {incident.cluster_id} · same seed both sides
            </p>
          </div>
        ))}
      </div>
    </article>
  );
}

export default function VideoCompare({ incidents }: VideoCompareProps) {
  const withVideo = incidents.filter((i) => i.before_video || i.after_video);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="stub-label">Before / after · {withVideo.length} incidents</div>
      {withVideo.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No video evidence yet.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {withVideo.map((incident) => (
            <Pair key={incident.cluster_id} incident={incident} />
          ))}
        </div>
      )}
    </div>
  );
}
