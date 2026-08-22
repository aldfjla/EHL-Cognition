"use client";

/**
 * The written incident report.
 *
 * Rendered from the same object used verbatim as the pull request body, so
 * what a judge reads on screen and what a developer reads on GitHub are the
 * same text. If those ever diverge, the dashboard has started lying.
 */

import type { Report } from "@/lib/types";

export interface ReportViewProps {
  /** The report, or null until REPORT completes. */
  report: Report | null;
}

export default function ReportView({ report }: ReportViewProps) {
  // TODO(build): render summary + incidents as markdown, verdict badge
  // coloured by outcome, and a prominent link to report.pull_request_url.
  return (
    <div className="stub">
      <div className="stub-label">Incident report</div>
      {report === null ? (
        <p className="mt-2 text-sm text-slate-500">Not written yet.</p>
      ) : (
        <article className="mt-2">
          <h2 className="text-sm font-semibold text-slate-200">{report.title}</h2>
          <p className="mt-1 text-xs text-slate-400">{report.summary}</p>
          <p className="mt-2 font-mono text-[10px] uppercase tracking-widest text-slate-500">
            {report.verdict} · {report.incidents.length} incidents
          </p>
        </article>
      )}
    </div>
  );
}
