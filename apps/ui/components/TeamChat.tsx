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

import { ROLE_LABELS, type Agent, type Message } from "@/lib/types";

export interface TeamChatProps {
  messages: Message[];
  /** Roster, for resolving agent ids to titles. */
  agents: Agent[];
}

export default function TeamChat({ messages, agents }: TeamChatProps) {
  // TODO(build): autoscroll to newest unless the user has scrolled up; icon
  // and colour per message.kind; render refs[] as clickable chips that select
  // the scenario or finding elsewhere on the page.
  void agents;
  return (
    <div className="stub max-h-[420px] overflow-y-auto">
      <div className="stub-label">Team chat · {messages.length}</div>
      {messages.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No relays yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {messages.map((m) => (
            <li key={m.id} className="text-xs">
              <span className="font-mono text-slate-400">
                {m.from_role === "orchestrator"
                  ? "orchestrator"
                  : ROLE_LABELS[m.from_role]}{" "}
                → {m.to_role}
              </span>
              <span className="ml-2 text-slate-500">[{m.kind}]</span>
              <p className="mt-0.5 text-slate-300">{m.body}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
