"use client";

/**
 * The written incident report.
 *
 * Rendered from the same object used verbatim as the pull request body, so
 * what a judge reads on screen and what a developer reads on GitHub are the
 * same text. If those ever diverge, the dashboard has started lying.
 */

import clsx from "clsx";

import type { Report, SuiteStats, Verdict } from "@/lib/types";

export interface ReportViewProps {
  /** The report, or null until REPORT completes. */
  report: Report | null;
}

const VERDICT_TONE: Record<Verdict, string> = {
  clean: "border-status-passed/60 text-status-passed",
  fixed: "border-status-passed/60 text-status-passed",
  unresolved: "border-status-failed/60 text-status-failed",
};

function rate(stats: SuiteStats | null): string {
  if (stats === null) return "—";
  return `${stats.passed}/${stats.total} · ${Math.round(stats.pass_rate * 100)}%`;
}

/**
 * Minimal inline markdown: bold, code and links are all the report body uses,
 * and a markdown dependency for three constructs is not worth the bundle.
 */
function inlineMarkdown(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    const key = `${keyPrefix}:${i}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={key} className="font-semibold text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={key} className="rounded bg-slate-800 px-1 font-mono text-[11px]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={key}>{part}</span>;
  });
}

/** Paragraphs and `- ` bullets, which is the shape the reporter emits. */
function Markdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="space-y-2">
      {blocks.map((block, bi) => {
        const lines = block.split("\n");
        const isList = lines.every((line) => /^\s*[-*]\s+/.test(line));
        if (isList) {
          return (
            <ul key={bi} className="list-disc space-y-1 pl-5">
              {lines.map((line, li) => (
                <li key={li} className="text-xs leading-5 text-slate-300">
                  {inlineMarkdown(line.replace(/^\s*[-*]\s+/, ""), `${bi}:${li}`)}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={bi} className="text-xs leading-5 text-slate-300">
            {inlineMarkdown(block, `${bi}`)}
          </p>
        );
      })}
    </div>
  );
}

export default function ReportView({ report }: ReportViewProps) {
  if (report === null) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
        <div className="stub-label">Incident report</div>
        <p className="mt-2 text-sm text-slate-500">Not written yet.</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <div className="stub-label">Incident report</div>
        <span
          className={clsx(
            "rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest",
            VERDICT_TONE[report.verdict],
          )}
        >
          {report.verdict}
        </span>
        {report.pull_request_url !== null && (
          <a
            href={report.pull_request_url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto rounded border border-sky-600 px-2 py-0.5 font-mono text-[11px] text-sky-300 hover:bg-sky-950"
          >
            view pull request →
          </a>
        )}
      </div>

      <h2 className="mt-3 text-base font-semibold text-slate-100">{report.title}</h2>

      <div className="mt-2 flex flex-wrap gap-4 font-mono text-[11px] text-slate-400">
        <span>
          before <span className="text-status-failed">{rate(report.before)}</span>
        </span>
        <span>
          after <span className="text-status-passed">{rate(report.after)}</span>
        </span>
        <span>
          {report.incidents.length} incident
          {report.incidents.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mt-3">
        <Markdown text={report.summary} />
      </div>

      {report.incidents.length > 0 && (
        <ol className="mt-4 space-y-3">
          {report.incidents.map((incident) => (
            <li
              key={incident.cluster_id}
              className="rounded border border-surface-border p-3"
            >
              <div className="flex flex-wrap items-baseline gap-2">
                <h3 className="text-sm font-semibold text-slate-200">
                  {incident.title}
                </h3>
                <span
                  className={clsx(
                    "font-mono text-[10px] uppercase tracking-widest",
                    incident.status === "fixed"
                      ? "text-status-passed"
                      : "text-status-failed",
                  )}
                >
                  {incident.status}
                </span>
                <span className="ml-auto font-mono text-[10px] text-slate-500">
                  {incident.affected_scenarios} scenarios
                </span>
              </div>

              <dl className="mt-2 space-y-1.5">
                <div>
                  <dt className="stub-label">root cause</dt>
                  <dd className="text-xs leading-5 text-slate-300">
                    {inlineMarkdown(incident.root_cause, `${incident.cluster_id}:rc`)}
                  </dd>
                </div>
                <div>
                  <dt className="stub-label">resolution</dt>
                  <dd className="text-xs leading-5 text-slate-300">
                    {inlineMarkdown(incident.resolution, `${incident.cluster_id}:res`)}
                  </dd>
                </div>
              </dl>

              {incident.files_changed.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {incident.files_changed.map((file) => (
                    <code
                      key={file}
                      className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
                    >
                      {file}
                    </code>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
