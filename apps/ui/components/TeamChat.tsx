"use client";

/**
 * The agent-to-agent message feed.
 *
 * Every entry is one orchestrator-mediated relay: a finding lifted out of one
 * session and spoken into another. Rendering it as a chat is honest — the
 * relay really is the conversation — and it is the clearest evidence that this
 * is a team rather than seven independent scripts.
 *
 * See docs/AGENT_ROLES.md for why the orchestrator sits in the middle.
 */

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

import {
  ROLE_LABELS,
  type Agent,
  type Message,
  type MessageKind,
  type Recipient,
  type Ref,
  type Speaker,
} from "@/lib/types";

export interface TeamChatProps {
  messages: Message[];
  /** Roster, for resolving agent ids to titles. */
  agents: Agent[];
  /** Selecting a ref chip focuses that object elsewhere on the page. */
  onSelectRef?: (ref: Ref) => void;
}

/** Speech act → glyph and colour. The icon carries the act, not the text. */
const KIND_STYLE: Record<MessageKind, { icon: string; tone: string }> = {
  hypothesis: { icon: "?", tone: "text-status-blocked border-status-blocked/50" },
  finding: { icon: "!", tone: "text-status-running border-status-running/50" },
  question: { icon: "?", tone: "text-slate-300 border-surface-border" },
  answer: { icon: "→", tone: "text-slate-300 border-surface-border" },
  verdict: { icon: "✓", tone: "text-status-passed border-status-passed/50" },
  handoff: { icon: "⇢", tone: "text-status-error border-status-error/50" },
};

function speakerLabel(role: Speaker | Recipient): string {
  if (role === "orchestrator") return "Orchestrator";
  if (role === "broadcast") return "everyone";
  return ROLE_LABELS[role];
}

function timeLabel(ts: string): string {
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return "";
  return new Date(parsed).toISOString().slice(11, 19);
}

export default function TeamChat({ messages, agents, onSelectRef }: TeamChatProps) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);

  const ordered = messages.slice().sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));

  // Autoscroll to newest, unless the reader has scrolled up to read history.
  useEffect(() => {
    const node = scroller.current;
    if (node === null || !pinned) return;
    node.scrollTop = node.scrollHeight;
  }, [ordered.length, pinned]);

  const onScroll = (): void => {
    const node = scroller.current;
    if (node === null) return;
    const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
    setPinned(atBottom);
  };

  const titleFor = (agentId: string | null): string | null => {
    if (agentId === null) return null;
    return agents.find((agent) => agent.id === agentId)?.title ?? agentId;
  };

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="flex items-baseline gap-2">
        <div className="stub-label">Team chat · {messages.length} relays</div>
        {!pinned && (
          <button
            type="button"
            onClick={() => setPinned(true)}
            className="ml-auto font-mono text-[10px] text-sky-400 hover:underline"
          >
            jump to newest
          </button>
        )}
      </div>

      {messages.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No relays yet.</p>
      ) : (
        <div
          ref={scroller}
          onScroll={onScroll}
          className="mt-3 max-h-[380px] space-y-3 overflow-y-auto pr-1"
        >
          {ordered.map((message) => {
            const style = KIND_STYLE[message.kind];
            const fromTitle = titleFor(message.from_agent_id);
            return (
              <article key={message.id} className="animate-rise">
                <div className="flex items-baseline gap-2">
                  <span
                    aria-hidden
                    className={clsx(
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded border font-mono text-[10px]",
                      style.tone,
                    )}
                  >
                    {style.icon}
                  </span>
                  <span className="truncate font-mono text-[11px] text-slate-300">
                    {speakerLabel(message.from_role)} → {speakerLabel(message.to_role)}
                  </span>
                  <span className={clsx("font-mono text-[10px]", style.tone)}>
                    {message.kind}
                  </span>
                  <span className="ml-auto font-mono text-[10px] text-slate-600">
                    {timeLabel(message.ts)}
                  </span>
                </div>

                {fromTitle && (
                  <div className="ml-6 truncate font-mono text-[10px] text-slate-600">
                    {fromTitle}
                  </div>
                )}

                <p className="ml-6 mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-300">
                  {message.body}
                </p>

                {message.refs.length > 0 && (
                  <div className="ml-6 mt-1.5 flex flex-wrap gap-1">
                    {message.refs.map((ref) => (
                      <button
                        key={`${ref.type}:${ref.id}`}
                        type="button"
                        onClick={() => onSelectRef?.(ref)}
                        className="rounded border border-surface-border px-1.5 py-0.5 font-mono text-[10px] text-slate-400 hover:border-sky-500 hover:text-sky-300"
                      >
                        {ref.type}:{ref.label ?? ref.id}
                      </button>
                    ))}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
