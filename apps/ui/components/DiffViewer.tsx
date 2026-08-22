"use client";

/**
 * The unified diff of everything the agents changed.
 *
 * The proof that the output is code, not a description of code. Rendered from
 * the report's `diff` field — the same text that goes into the pull request.
 */

export interface DiffViewerProps {
  /** Unified diff, or null before VERIFY completes. */
  diff: string | null;
}

export default function DiffViewer({ diff }: DiffViewerProps) {
  // TODO(build): parse into hunks, colour +/- lines, collapse unchanged
  // context, and show the file path as a sticky header per file.
  return (
    <div className="stub">
      <div className="stub-label">Diff</div>
      {diff === null ? (
        <p className="mt-2 text-sm text-slate-500">No patches yet.</p>
      ) : (
        <pre className="mt-2 overflow-x-auto font-mono text-xs text-slate-300">
          {diff}
        </pre>
      )}
    </div>
  );
}
