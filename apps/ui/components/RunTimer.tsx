"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";

const MAX_ELAPSED_MS = 60 * 60 * 1000;

export interface RunTimerProps {
  createdAt: string;
  finishedAt: string | null;
}

export interface FormattedRunElapsed {
  text: string;
  capped: boolean;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatRunElapsed(milliseconds: number): FormattedRunElapsed {
  if (milliseconds > MAX_ELAPSED_MS) {
    return { text: "1:00:00+", capped: true };
  }

  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  return {
    text:
      hours > 0
        ? `${hours}:${pad(minutes)}:${pad(seconds)}`
        : `${pad(minutes)}:${pad(seconds)}`,
    capped: false,
  };
}

export function elapsedRunMs(
  createdAt: string,
  finishedAt: string | null,
  now: number,
): number | null {
  const started = Date.parse(createdAt);
  const ended = finishedAt === null ? now : Date.parse(finishedAt);
  if (Number.isNaN(started) || Number.isNaN(ended)) return null;
  return Math.max(0, ended - started);
}

export default function RunTimer({
  createdAt,
  finishedAt,
}: RunTimerProps) {
  const [now, setNow] = useState<number>(() => Date.now());
  const live = finishedAt === null;

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);

  const elapsed = elapsedRunMs(createdAt, finishedAt, now);
  if (elapsed === null) return null;

  const display = formatRunElapsed(elapsed);
  return (
    <span
      className={clsx(
        "font-mono text-xs",
        display.capped ? "text-status-blocked" : "text-slate-300",
      )}
      title="Elapsed run time"
    >
      {display.text}
    </span>
  );
}
