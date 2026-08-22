"use client";

/**
 * The unified diff of everything the agents changed.
 *
 * The proof that the output is code, not a description of code. Rendered from
 * the report's `diff` field — the same text that goes into the pull request.
 */

import clsx from "clsx";
import { useMemo, useState } from "react";

export interface DiffViewerProps {
  /** Unified diff, or null before VERIFY completes. */
  diff: string | null;
}

interface FileDiff {
  path: string;
  added: number;
  removed: number;
  lines: string[];
}

/** Split a unified diff into per-file blocks, keeping hunk headers. */
function parseDiff(diff: string): FileDiff[] {
  const files: FileDiff[] = [];
  let current: FileDiff | null = null;

  for (const line of diff.split("\n")) {
    if (line.startsWith("diff --git") || (line.startsWith("+++ ") && current === null)) {
      const path =
        line.startsWith("diff --git")
          ? (line.split(" ").pop() ?? "").replace(/^b\//, "")
          : line.slice(4).replace(/^b\//, "");
      current = { path, added: 0, removed: 0, lines: [] };
      files.push(current);
      continue;
    }
    if (current === null) continue;
    // Index/mode/--- /+++ lines carry no signal once the path is known.
    if (/^(index |--- |\+\+\+ |new file|deleted file|old mode|new mode|similarity)/.test(line)) {
      continue;
    }
    if (line.startsWith("+")) current.added += 1;
    else if (line.startsWith("-")) current.removed += 1;
    current.lines.push(line);
  }

  return files.filter((file) => file.lines.length > 0);
}

function lineTone(line: string): string {
  if (line.startsWith("@@")) return "text-sky-300 bg-sky-950/40";
  if (line.startsWith("+")) return "text-status-passed bg-emerald-950/30";
  if (line.startsWith("-")) return "text-status-failed bg-rose-950/30";
  return "text-slate-400";
}

export default function DiffViewer({ diff }: DiffViewerProps) {
  const files = useMemo(() => (diff === null ? [] : parseDiff(diff)), [diff]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  if (diff === null) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
        <div className="stub-label">Diff</div>
        <p className="mt-2 text-sm text-slate-500">No patches yet.</p>
      </div>
    );
  }

  const added = files.reduce((sum, file) => sum + file.added, 0);
  const removed = files.reduce((sum, file) => sum + file.removed, 0);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="stub-label">
        Diff · {files.length || 1} files ·{" "}
        <span className="text-status-passed">+{added}</span>{" "}
        <span className="text-status-failed">-{removed}</span>
      </div>

      {files.length === 0 ? (
        <pre className="mt-2 overflow-x-auto font-mono text-xs text-slate-300">
          {diff}
        </pre>
      ) : (
        <div className="mt-3 space-y-3">
          {files.map((file) => {
            const isCollapsed = collapsed[file.path] ?? false;
            return (
              <div
                key={file.path}
                className="overflow-hidden rounded border border-surface-border"
              >
                <button
                  type="button"
                  onClick={() =>
                    setCollapsed((prev) => ({
                      ...prev,
                      [file.path]: !isCollapsed,
                    }))
                  }
                  className="sticky top-0 flex w-full items-baseline gap-2 bg-slate-900/90 px-2 py-1.5 text-left backdrop-blur"
                >
                  <span className="truncate font-mono text-[11px] text-slate-200">
                    {file.path}
                  </span>
                  <span className="ml-auto font-mono text-[10px] text-status-passed">
                    +{file.added}
                  </span>
                  <span className="font-mono text-[10px] text-status-failed">
                    -{file.removed}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {isCollapsed ? "show" : "hide"}
                  </span>
                </button>

                {!isCollapsed && (
                  <div className="overflow-x-auto">
                    {file.lines.map((line, i) => (
                      <div
                        key={`${file.path}:${i}`}
                        className={clsx(
                          "whitespace-pre px-2 font-mono text-[11px] leading-5",
                          lineTone(line),
                        )}
                      >
                        {line === "" ? " " : line}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
