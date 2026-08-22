"use client";

/**
 * Shared chrome: one slim top bar, a command palette, and a shortcut sheet.
 *
 * The bar is deliberately shallow — mission control needs the vertical space —
 * so everything beyond primary navigation lives behind ⌘K or the demos menu.
 *
 * Keyboard surface:
 *   Ctrl/⌘+K   command palette
 *   ?          shortcut sheet
 *   g then r   dashboard
 */

import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import CommandPalette from "./CommandPalette";
import { MOCK_RUN_ID } from "@/lib/mockRun";

const NAV = [
  { href: "/runs", label: "Dashboard" },
] as const;

const DEMOS = [
  { href: `/runs/${MOCK_RUN_ID}`, label: "Scripted run replay" },
] as const;

const SHORTCUTS: Array<{ keys: string; does: string }> = [
  { keys: "Ctrl K", does: "Open the command palette" },
  { keys: "?", does: "Toggle this sheet" },
  { keys: "g r", does: "Go to dashboard" },
  { keys: "1 – 4", does: "Switch tabs on a run page" },
  { keys: "Esc", does: "Close any overlay" },
];

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [demosOpen, setDemosOpen] = useState(false);
  const pendingG = useRef(false);

  const closeAll = useCallback((): void => {
    setPaletteOpen(false);
    setHelpOpen(false);
    setDemosOpen(false);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setHelpOpen(false);
        setPaletteOpen((open) => !open);
        return;
      }
      if (isEditable(event.target) || paletteOpen) return;

      if (event.key === "Escape") {
        closeAll();
        return;
      }
      if (event.key === "?") {
        setHelpOpen((open) => !open);
        return;
      }
      if (event.key === "g") {
        pendingG.current = true;
        window.setTimeout(() => {
          pendingG.current = false;
        }, 800);
        return;
      }
      if (pendingG.current) {
        pendingG.current = false;
        if (event.key === "r") router.push("/runs");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, router, closeAll]);

  // Navigating closes every overlay; a palette left open over a new page reads
  // as a bug even when it is not.
  useEffect(() => closeAll(), [pathname, closeAll]);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-surface-border bg-surface/90 backdrop-blur">
        <div className="flex h-11 items-center gap-1 px-4">
          <Link href="/" className="mr-3 flex items-baseline gap-2">
            <span className="font-mono text-sm font-semibold tracking-tight text-slate-100">
              robot·ci
            </span>
            <span className="hidden text-[10px] uppercase tracking-widest text-slate-500 sm:inline">
              autonomous simulation ci
            </span>
          </Link>

          <nav className="flex items-center gap-1">
            {NAV.map(({ href, label }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={clsx(
                    "rounded px-2.5 py-1 font-mono text-xs",
                    active
                      ? "bg-surface-raised text-sky-300"
                      : "text-slate-400 hover:bg-surface-raised hover:text-slate-200",
                  )}
                >
                  {label}
                </Link>
              );
            })}

            <div className="relative">
              <button
                type="button"
                onClick={() => setDemosOpen((open) => !open)}
                className={clsx(
                  "rounded px-2.5 py-1 font-mono text-xs",
                  demosOpen
                    ? "bg-surface-raised text-sky-300"
                    : "text-slate-400 hover:bg-surface-raised hover:text-slate-200",
                )}
              >
                Demos ▾
              </button>
              {demosOpen && (
                <div className="absolute left-0 top-full z-50 mt-1 w-52 rounded-lg border border-surface-border bg-surface-raised p-1 shadow-xl">
                  {DEMOS.map(({ href, label }) => (
                    <Link
                      key={href}
                      href={href}
                      onClick={() => setDemosOpen(false)}
                      className="block rounded px-2.5 py-1.5 font-mono text-xs text-slate-300 hover:bg-surface hover:text-sky-300"
                    >
                      {label}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="flex items-center gap-2 rounded border border-surface-border px-2.5 py-1 font-mono text-xs text-slate-500 hover:border-slate-600 hover:text-slate-300"
            >
              Search & commands
              <kbd className="rounded bg-surface px-1.5 py-0.5 text-[10px] text-slate-400">
                Ctrl K
              </kbd>
            </button>
            <button
              type="button"
              onClick={() => setHelpOpen((open) => !open)}
              aria-label="Keyboard shortcuts"
              className="rounded border border-surface-border px-2 py-1 font-mono text-xs text-slate-500 hover:border-slate-600 hover:text-slate-300"
            >
              ?
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1">{children}</div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      {helpOpen && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-24"
          onClick={() => setHelpOpen(false)}
        >
          <div
            className="w-full max-w-sm rounded-lg border border-surface-border bg-surface-raised p-4 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="stub-label mb-3">Keyboard shortcuts</div>
            <table className="w-full text-sm">
              <tbody>
                {SHORTCUTS.map(({ keys, does }) => (
                  <tr key={keys}>
                    <td className="py-1 pr-4">
                      <kbd className="rounded bg-surface px-1.5 py-0.5 font-mono text-xs text-sky-300">
                        {keys}
                      </kbd>
                    </td>
                    <td className="py-1 text-slate-300">{does}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
