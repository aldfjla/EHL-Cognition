"use client";

/**
 * The focused view of one wall tile: the feed large, plus everything the tile
 * itself has no room for — params, criteria results, diagnosis, sim error.
 *
 * The focused tile always counts as visible and streaming (it is the one feed
 * the user explicitly asked for). Escape or the backdrop closes it.
 */

import clsx from "clsx";
import { useEffect } from "react";

import * as api from "@/lib/api";
import type { Scenario } from "@/lib/types";

import LiveFeed from "./LiveFeed";

export interface LiveTileFocusProps {
  runId: string;
  scenario: Scenario;
  synthetic: boolean;
  onClose: () => void;
}

export default function LiveTileFocus({
  runId,
  scenario,
  synthetic,
  onClose,
}: LiveTileFocusProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const finished =
    scenario.status === "passed" ||
    scenario.status === "failed" ||
    scenario.status === "error";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`scenario detail — ${scenario.label}`}
    >
      <div
        className="max-h-full w-full max-w-3xl overflow-y-auto rounded-lg border border-surface-border bg-surface-raised"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-baseline justify-between gap-3 border-b border-surface-border px-4 py-3">
          <div>
            <div className="text-sm text-slate-200">{scenario.label}</div>
            <div className="font-mono text-[11px] text-slate-500">
              #{scenario.index} · seed {scenario.seed}
              {scenario.worker_id ? ` · ${scenario.worker_id}` : ""}
              {" · "}
              <span
                className={clsx(
                  scenario.status === "passed" && "text-status-passed",
                  scenario.status === "failed" && "text-status-failed",
                  scenario.status === "error" && "text-status-error",
                  scenario.status === "running" && "text-status-running",
                )}
              >
                {scenario.status}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="font-mono text-xs text-slate-400 hover:text-slate-200"
          >
            close ✕
          </button>
        </div>

        <div className="aspect-video w-full bg-slate-950">
          {finished && scenario.video_path ? (
            <video
              src={api.artifactUrl(scenario.video_path)}
              className="h-full w-full object-contain"
              controls
              muted
              loop
              autoPlay
              playsInline
            />
          ) : (
            <LiveFeed
              runId={runId}
              scenario={scenario}
              streaming
              synthetic={synthetic}
              className="h-full w-full"
            />
          )}
        </div>

        <div className="grid gap-4 p-4 sm:grid-cols-2">
          <div>
            <div className="stub-label">Params</div>
            <dl className="mt-2 space-y-1">
              {Object.entries(scenario.params).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt className="font-mono text-xs text-slate-500">{key}</dt>
                  <dd className="font-mono text-xs text-slate-300">
                    {String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <div>
            <div className="stub-label">Criteria</div>
            {scenario.criteria.length === 0 ? (
              <p className="mt-2 text-xs text-slate-500">
                No results yet — criteria are scored when the scenario finishes.
              </p>
            ) : (
              <ul className="mt-2 space-y-1">
                {scenario.criteria.map((criterion) => (
                  <li
                    key={criterion.id}
                    className="flex justify-between gap-3 font-mono text-xs"
                  >
                    <span className="text-slate-400">{criterion.id}</span>
                    <span
                      className={
                        criterion.passed
                          ? "text-status-passed"
                          : "text-status-failed"
                      }
                    >
                      {criterion.passed ? "pass" : "fail"}
                      {criterion.value !== null && criterion.value !== undefined
                        ? ` (${String(criterion.value)}${
                            criterion.threshold !== null &&
                            criterion.threshold !== undefined
                              ? ` / ${String(criterion.threshold)}`
                              : ""
                          })`
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {scenario.diagnosis && (
            <div className="sm:col-span-2">
              <div className="stub-label">Diagnosis</div>
              <p className="mt-2 text-sm text-slate-300">{scenario.diagnosis}</p>
            </div>
          )}

          {scenario.error && (
            <div className="sm:col-span-2">
              <div className="stub-label text-status-error">Sim error</div>
              <p className="mt-2 font-mono text-xs text-status-error">
                {scenario.error}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
