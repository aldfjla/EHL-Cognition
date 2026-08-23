"use client";

/**
 * One agent's machine, embedded.
 *
 * A pane is only allowed to hold a live connection when the panel says so (see
 * `decidePanes` in ./agentOps) — an off-screen, paused or over-budget pane
 * renders a still placeholder that says *why* it is still. The iframe is
 * mounted only while live, so pausing or scrolling away actually drops the
 * connection instead of merely hiding it.
 *
 * When an agent has no `desktop_url` at all we show its activity ticker and
 * session link. A frame that renders an error page is worse than no frame.
 */

import clsx from "clsx";
import { useEffect, useRef } from "react";

import type { PaneBlock } from "./agentOps";
import { ROLE_LABELS, type Agent } from "@/lib/types";

export interface AgentDesktopProps {
  agent: Agent;
  /** May this pane hold an open live connection right now? */
  live: boolean;
  /** Why it may not, when `live` is false. */
  reason: PaneBlock | null;
  /** `large` is focus mode; `tile` is the wall and inline expansion. */
  size?: "tile" | "large";
  /** Reports on-screen-ness so the panel can spend its live budget on it. */
  onVisibilityChange?: (agentId: string, visible: boolean) => void;
  onFocus?: (agentId: string) => void;
}

const BLOCK_COPY: Record<PaneBlock, string> = {
  paused: "Feeds paused",
  budget: "Over the live budget",
  not_visible: "Off screen",
  no_desktop: "No desktop for this session",
};

const BLOCK_DETAIL: Record<PaneBlock, string> = {
  paused: "Resume feeds to reconnect this view.",
  budget: "Too many panes are already live. Focus this agent to give it a slot.",
  not_visible: "Scroll it into view to connect.",
  no_desktop: "This session never exposed an embeddable view of its machine.",
};

/** Report visibility to the panel; an unmounted pane counts as not visible. */
function useReportVisibility(
  ref: React.RefObject<HTMLDivElement | null>,
  agentId: string,
  onVisibilityChange?: (agentId: string, visible: boolean) => void,
): void {
  useEffect(() => {
    const node = ref.current;
    if (!node || !onVisibilityChange) return;

    if (typeof IntersectionObserver === "undefined") {
      // No observer (old browser, test env): assume visible rather than dark.
      onVisibilityChange(agentId, true);
      return () => onVisibilityChange(agentId, false);
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          onVisibilityChange(agentId, entry.isIntersecting);
        }
      },
      { threshold: 0.15 },
    );
    observer.observe(node);

    return () => {
      observer.disconnect();
      onVisibilityChange(agentId, false);
    };
  }, [ref, agentId, onVisibilityChange]);
}

export default function AgentDesktop({
  agent,
  live,
  reason,
  size = "tile",
  onVisibilityChange,
  onFocus,
}: AgentDesktopProps) {
  const ref = useRef<HTMLDivElement>(null);
  useReportVisibility(ref, agent.id, onVisibilityChange);

  // Pin the feed to the newest line so it reads like a terminal, not a log file.
  const feedRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = feedRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [agent.activity_log?.length, agent.last_activity]);

  const frameHeight = size === "large" ? "h-[420px]" : "h-40";

  return (
    <div
      ref={ref}
      data-agent-id={agent.id}
      data-live={live}
      className="flex min-w-0 flex-col overflow-hidden rounded-lg border border-surface-border bg-surface"
    >
      <div className="flex items-center gap-2 border-b border-surface-border px-2 py-1.5">
        <span
          aria-hidden
          className={clsx(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            live ? "bg-status-passed animate-pulse" : "bg-status-pending",
          )}
        />
        <span className="stub-label truncate">{ROLE_LABELS[agent.role]}</span>
        <span className="truncate text-xs text-slate-300" title={agent.title}>
          {agent.title || agent.id}
        </span>
        <span
          className={clsx(
            "ml-auto shrink-0 font-mono text-[10px] uppercase",
            live ? "text-status-passed" : "text-slate-500",
          )}
        >
          {live ? "live" : reason ? BLOCK_COPY[reason] : "idle"}
        </span>
        {onFocus && size === "tile" && (
          <button
            type="button"
            onClick={() => onFocus(agent.id)}
            className="shrink-0 font-mono text-[10px] text-sky-400 hover:underline"
          >
            focus
          </button>
        )}
      </div>

      {live && agent.desktop_url ? (
        <iframe
          src={agent.desktop_url}
          title={`Desktop of ${agent.title || agent.id}`}
          className={clsx("w-full border-0 bg-black", frameHeight)}
          sandbox="allow-scripts allow-same-origin"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div
          className={clsx(
            "flex min-w-0 flex-col justify-center gap-1.5 px-3 py-2",
            frameHeight,
          )}
        >
          {reason === "no_desktop" ? (
            <>
              <p className="stub-label">
                Live activity
                {agent.status === "working" && (
                  <span className="ml-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400 align-middle" />
                )}
              </p>
              {/* The whole transcript, newest last. A single overwritten line
                  reads as a frozen screen while the agent is really working. */}
              <div
                ref={feedRef}
                className="flex max-h-[8.5rem] flex-col gap-1 overflow-y-auto pr-1"
              >
                {(agent.activity_log?.length
                  ? agent.activity_log
                  : agent.last_activity
                    ? [{ text: agent.last_activity, ts: agent.updated_at }]
                    : []
                ).map((line, index) => (
                  <p
                    key={`${line.ts}-${index}`}
                    className="font-mono text-[11px] leading-snug text-slate-300"
                  >
                    <span className="mr-1.5 text-slate-600">
                      {new Date(line.ts).toLocaleTimeString([], {
                        hour12: false,
                      })}
                    </span>
                    {line.text}
                  </p>
                ))}
                {!agent.activity_log?.length && !agent.last_activity && (
                  <p className="font-mono text-[11px] text-slate-500">
                    Waiting for the first transcript line…
                  </p>
                )}
              </div>
              {agent.step && (
                <p className="font-mono text-[10px] text-slate-500">
                  step · {agent.step}
                </p>
              )}
              <p className="mt-1 text-[11px] text-slate-500">
                {BLOCK_DETAIL.no_desktop}
              </p>
              {agent.session_url ? (
                <a
                  href={agent.session_url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono text-[10px] text-sky-400 hover:underline"
                >
                  open Devin session ↗
                </a>
              ) : (
                <span className="font-mono text-[10px] text-slate-600">
                  session pending
                </span>
              )}
            </>
          ) : (
            <>
              <p className="font-mono text-[11px] uppercase tracking-widest text-slate-400">
                {reason ? BLOCK_COPY[reason] : "not connected"}
              </p>
              <p className="text-[11px] text-slate-500">
                {reason ? BLOCK_DETAIL[reason] : "This pane holds no connection."}
              </p>
              {agent.last_activity && (
                <p
                  className="truncate border-l border-surface-border pl-2 font-mono text-[10px] text-slate-500"
                  title={agent.last_activity}
                >
                  {agent.last_activity}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
