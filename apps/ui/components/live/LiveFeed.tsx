"use client";

/**
 * The video area of one wall tile.
 *
 * Connection budget is the design constraint: an MJPEG stream is an open HTTP
 * connection for as long as the <img> is mounted, and thirty tiles times one
 * stream each is the failure mode this component exists to prevent. So:
 *
 *  - `streaming` (visible, running, feed known) mounts the MJPEG <img>;
 *  - otherwise a still `live.jpg` is polled at a slow cadence — the throttled
 *    `scenario.progress` event already proves the tile is alive;
 *  - no known frame, or a failed load, shows an honest placeholder — never a
 *    broken image and never an eternal spinner;
 *  - the scripted replay draws a synthetic canvas vignette instead (there is
 *    no server to stream from).
 */

import { useEffect, useState } from "react";

import type { Scenario } from "@/lib/types";

import { liveFrameUrl, liveStreamUrl } from "./liveState";
import SyntheticFeed from "./SyntheticFeed";

/** Refresh cadence for non-streaming (still-frame) tiles. */
const STILL_REFRESH_MS = 4000;

export interface LiveFeedProps {
  runId: string;
  scenario: Scenario;
  /** Tile is on screen and allowed to hold a streaming connection. */
  streaming: boolean;
  /** Scripted replay: draw the synthetic vignette instead of fetching. */
  synthetic: boolean;
  className?: string;
}

export default function LiveFeed({
  runId,
  scenario,
  streaming,
  synthetic,
  className,
}: LiveFeedProps) {
  const [failed, setFailed] = useState(false);
  const [stillTick, setStillTick] = useState(0);

  const hasFrame = scenario.live_frame_path !== null;
  const running = scenario.status === "running";

  // Poll the still frame slowly while not streaming.
  useEffect(() => {
    if (synthetic || !running || !hasFrame || streaming || failed) return;
    const timer = setInterval(() => setStillTick((t) => t + 1), STILL_REFRESH_MS);
    return () => clearInterval(timer);
  }, [synthetic, running, hasFrame, streaming, failed]);

  // A feed that failed once may come back with the next progress tick.
  useEffect(() => {
    setFailed(false);
  }, [scenario.live_frame_path, streaming]);

  if (synthetic && running) {
    return (
      <div className={className}>
        {hasFrame ? (
          <SyntheticFeed
            seed={scenario.seed}
            progress={scenario.progress ?? 0}
            className="h-full w-full object-cover"
          />
        ) : (
          <FeedPlaceholder text="no frames from worker" />
        )}
      </div>
    );
  }

  if (!running || !hasFrame || failed) {
    return (
      <div className={className}>
        <FeedPlaceholder
          text={
            !running
              ? "not simulating"
              : !hasFrame
                ? "no frames from worker"
                : "feed unavailable"
          }
        />
      </div>
    );
  }

  const src = streaming
    ? liveStreamUrl(runId, scenario.id)
    : `${liveFrameUrl(runId, scenario.id)}?t=${stillTick}`;

  return (
    <div className={className}>
      {/* eslint-disable-next-line @next/next/no-img-element -- MJPEG streams
          and cache-busted still frames must bypass the Next image optimizer. */}
      <img
        src={src}
        alt={`live feed — ${scenario.label}`}
        className="h-full w-full object-cover"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function FeedPlaceholder({ text }: { text: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center bg-slate-900/80">
      <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
        {text}
      </span>
    </div>
  );
}
