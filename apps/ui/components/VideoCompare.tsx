"use client";

/**
 * Before/after clips of the same scenario seed.
 *
 * The closing argument of the demo: the identical randomized world, the robot
 * failing on the left and succeeding on the right, with nothing between them
 * but an agent's patch. Same seed on both sides or the comparison means
 * nothing — that constraint is why simkit's determinism is non-negotiable.
 */

import type { Incident } from "@/lib/types";

export interface VideoCompareProps {
  /** Incidents carrying before/after artifact paths. */
  incidents: Incident[];
}

export default function VideoCompare({ incidents }: VideoCompareProps) {
  // TODO(build): paired <video> elements per incident via api.artifactUrl(),
  // synced play/pause across the pair, loop, and a seed label under each.
  const withVideo = incidents.filter((i) => i.before_video || i.after_video);

  return (
    <div className="stub">
      <div className="stub-label">Before / after · {withVideo.length} incidents</div>
      {withVideo.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No video evidence yet.</p>
      ) : (
        <ul className="mt-2 space-y-1 text-xs text-slate-400">
          {withVideo.map((i) => (
            <li key={i.cluster_id}>{i.title}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
