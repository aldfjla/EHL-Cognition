"use client";

/**
 * The ⌘K palette: fuzzy-ish filtering over navigation and actions.
 *
 * Static commands plus the most recent runs, fetched lazily on open so the
 * palette costs nothing until used. Filtering is a simple case-insensitive
 * subsequence match — good enough for a dozen commands, no dependency.
 */

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import * as api from "@/lib/api";
import { LIVE_MOCK_RUN_ID } from "@/lib/mockLive";
import { MOCK_RUN_ID } from "@/lib/mockRun";
import type { Run } from "@/lib/types";

interface Command {
  id: string;
  label: string;
  hint?: string;
  href: string;
}

const STATIC_COMMANDS: Command[] = [
  { id: "nav-runs", label: "Go to dashboard", hint: "g r", href: "/runs" },
  {
    id: "act-connect",
    label: "Connect a GitHub repository",
    href: "/runs?connect=1",
  },
  {
    id: "demo-replay",
    label: "Demo: scripted run replay",
    href: `/runs/${MOCK_RUN_ID}`,
  },
  {
    id: "demo-live",
    label: "Demo: live simulation wall",
    href: `/runs/${LIVE_MOCK_RUN_ID}`,
  },
  { id: "demo-agents", label: "Demo: agent ops harness", href: "/agents-demo" },
];

function matches(query: string, text: string): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  const t = text.toLowerCase();
  let at = 0;
  for (const ch of q) {
    at = t.indexOf(ch, at);
    if (at === -1) return false;
    at += 1;
  }
  return true;
}

export default function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setCursor(0);
    inputRef.current?.focus();
    api
      .listRuns(8)
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [open]);

  const commands = useMemo((): Command[] => {
    const runCommands = runs.map((run) => ({
      id: `run-${run.id}`,
      label: `Open run ${run.id}`,
      hint: `${run.repo} · ${run.stage}`,
      href: `/runs/${run.id}`,
    }));
    return [...STATIC_COMMANDS, ...runCommands].filter((cmd) =>
      matches(query, `${cmd.label} ${cmd.hint ?? ""}`),
    );
  }, [query, runs]);

  const clamped = Math.min(cursor, Math.max(commands.length - 1, 0));

  const run = (command: Command | undefined): void => {
    if (command === undefined) return;
    onClose();
    router.push(command.href);
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-24"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-lg border border-surface-border bg-surface-raised shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setCursor((c) => Math.min(c + 1, commands.length - 1));
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setCursor((c) => Math.max(c - 1, 0));
            }
            if (event.key === "Enter") run(commands[clamped]);
          }}
          placeholder="Type a command or search…"
          className="w-full border-b border-surface-border bg-transparent px-4 py-3 font-mono text-sm text-slate-100 outline-none placeholder:text-slate-600"
        />
        <ul className="max-h-80 overflow-y-auto p-1">
          {commands.length === 0 && (
            <li className="px-3 py-4 text-center text-sm text-slate-500">
              Nothing matches “{query}”.
            </li>
          )}
          {commands.map((command, index) => (
            <li key={command.id}>
              <button
                type="button"
                onClick={() => run(command)}
                onMouseEnter={() => setCursor(index)}
                className={`flex w-full items-baseline justify-between gap-3 rounded px-3 py-2 text-left font-mono text-xs ${
                  index === clamped
                    ? "bg-surface text-sky-300"
                    : "text-slate-300"
                }`}
              >
                <span>{command.label}</span>
                {command.hint !== undefined && (
                  <span className="truncate text-[10px] text-slate-500">
                    {command.hint}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
