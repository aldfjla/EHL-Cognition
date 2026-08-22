"use client";

/**
 * Repositories — connect a GitHub repo so a push wakes the system.
 *
 * The mental model this page sells: Robot CI is *dormant*. Connecting a repo
 * arms it; the first push to the watched branch wakes it and starts a run.
 * Each card shows that state honestly — dormant / running — plus the latest
 * run and the webhook the customer still has to install on GitHub's side.
 *
 * `?connect=1` opens the connect dialog straight away (the ⌘K action).
 */

import clsx from "clsx";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import * as api from "@/lib/api";
import type { ConnectedRepo, ConnectRepoResponse } from "@/lib/types";

const FULL_NAME = /^[\w.-]+\/[\w.-]+$/;

function since(iso: string | null): string {
  if (iso === null) return "never";
  const ms = Date.now() - Date.parse(iso);
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function RepoCard({
  repo,
  onDisconnect,
}: {
  repo: ConnectedRepo;
  onDisconnect: (repo: ConnectedRepo) => void;
}) {
  const running = repo.status === "running";
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="flex items-center gap-3">
        <span
          className={clsx(
            "h-2 w-2 rounded-full",
            running ? "animate-pulse bg-status-running" : "bg-slate-600",
          )}
        />
        <a
          href={`https://github.com/${repo.full_name}`}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-sm font-semibold text-slate-100 hover:text-sky-300"
        >
          {repo.full_name}
        </a>
        <span
          className={clsx(
            "rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest",
            running
              ? "border-status-running/60 text-status-running"
              : "border-surface-border text-slate-500",
          )}
        >
          {running ? "running" : "dormant"}
        </span>
        <button
          type="button"
          onClick={() => onDisconnect(repo)}
          className="ml-auto font-mono text-[10px] text-slate-600 hover:text-status-failed"
        >
          disconnect
        </button>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs text-slate-400 sm:grid-cols-4">
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            branch
          </dt>
          <dd>{repo.branch}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            scenarios / push
          </dt>
          <dd>{repo.suite_size}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            last push
          </dt>
          <dd>{since(repo.last_push_at)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            latest run
          </dt>
          <dd>
            {repo.latest_run === null ? (
              "—"
            ) : (
              <Link
                href={`/runs/${repo.latest_run.id}`}
                className="text-sky-400 hover:underline"
              >
                {repo.latest_run.stage}
              </Link>
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function ConnectDialog({
  onClose,
  onConnected,
}: {
  onClose: () => void;
  onConnected: (result: ConnectRepoResponse) => void;
}) {
  const [fullName, setFullName] = useState("");
  const [branch, setBranch] = useState("main");
  const [suiteSize, setSuiteSize] = useState(50);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    if (!FULL_NAME.test(fullName.trim())) {
      setError("Repository must look like owner/name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.connectRepo({
        full_name: fullName.trim(),
        branch: branch.trim() || "main",
        suite_size: suiteSize,
      });
      onConnected(result);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-24"
      onClick={onClose}
    >
      <form
        className="w-full max-w-md rounded-lg border border-surface-border bg-surface-raised p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="stub-label mb-4">Connect a repository</div>

        <label className="block text-xs text-slate-400">
          GitHub repository
          <input
            autoFocus
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            placeholder="owner/robot-firmware"
            className="mt-1 w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-sky-600"
          />
        </label>

        <div className="mt-3 grid grid-cols-2 gap-3">
          <label className="block text-xs text-slate-400">
            Watched branch
            <input
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              className="mt-1 w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-sky-600"
            />
          </label>
          <label className="block text-xs text-slate-400">
            Scenarios per push
            <input
              type="number"
              min={1}
              max={200}
              value={suiteSize}
              onChange={(event) => setSuiteSize(Number(event.target.value))}
              className="mt-1 w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-sky-600"
            />
          </label>
        </div>

        {error !== null && (
          <p className="mt-3 text-xs text-status-failed">{error}</p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-surface-border px-3 py-1.5 font-mono text-xs text-slate-400 hover:text-slate-200"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-sky-600 px-3 py-1.5 font-mono text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {busy ? "Connecting…" : "Connect"}
          </button>
        </div>
      </form>
    </div>
  );
}

function WebhookNotice({
  result,
  onDismiss,
}: {
  result: ConnectRepoResponse;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded-lg border border-status-passed/40 bg-emerald-950/20 p-4">
      <div className="flex items-baseline justify-between">
        <div className="font-mono text-sm text-status-passed">
          {result.repo.full_name} connected — now dormant, waiting for a push.
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="font-mono text-[10px] text-slate-500 hover:text-slate-300"
        >
          dismiss
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        Last step, on GitHub: <span className="font-mono">Settings → Webhooks →
        Add webhook</span>, content type <span className="font-mono">application/json</span>,
        event <span className="font-mono">push</span>, payload URL:
      </p>
      <code className="mt-2 block rounded bg-surface px-3 py-2 font-mono text-xs text-sky-300">
        {result.webhook.url}
      </code>
      {!result.webhook.secret_configured && (
        <p className="mt-2 text-xs text-status-error">
          No WEBHOOK_SECRET is configured on the server — set one in .env and
          use the same value as the webhook secret on GitHub.
        </p>
      )}
    </div>
  );
}

function RepositoriesPage() {
  const params = useSearchParams();
  const [repos, setRepos] = useState<ConnectedRepo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(params.get("connect") === "1");
  const [justConnected, setJustConnected] = useState<ConnectRepoResponse | null>(
    null,
  );

  const load = useCallback(async (): Promise<void> => {
    try {
      setRepos(await api.listRepos());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const disconnect = async (repo: ConnectedRepo): Promise<void> => {
    if (!window.confirm(`Disconnect ${repo.full_name}? Future pushes will be ignored.`))
      return;
    await api.disconnectRepo(repo.id);
    void load();
  };

  return (
    <main className="mx-auto max-w-4xl p-6">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <h1 className="font-mono text-lg font-semibold">Repositories</h1>
          <p className="mt-1 text-sm text-slate-400">
            Robot CI stays dormant until a push lands on a watched branch —
            then it wakes, simulates, fixes, and opens a PR.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          className="rounded bg-sky-600 px-3 py-1.5 font-mono text-xs font-semibold text-white hover:bg-sky-500"
        >
          + Connect repository
        </button>
      </header>

      {justConnected !== null && (
        <div className="mb-4">
          <WebhookNotice
            result={justConnected}
            onDismiss={() => setJustConnected(null)}
          />
        </div>
      )}

      {error !== null && (
        <div className="mb-4 rounded border border-status-error/60 bg-rose-950/30 px-3 py-2 text-xs text-status-error">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : repos.length === 0 ? (
        <div className="rounded-lg border border-dashed border-surface-border p-10 text-center">
          <p className="font-mono text-sm text-slate-400">
            No repositories connected.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Connect one and Robot CI will watch it while you sleep.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {repos.map((repo) => (
            <RepoCard key={repo.id} repo={repo} onDisconnect={(r) => void disconnect(r)} />
          ))}
        </div>
      )}

      {dialogOpen && (
        <ConnectDialog
          onClose={() => setDialogOpen(false)}
          onConnected={(result) => {
            setDialogOpen(false);
            setJustConnected(result);
            void load();
          }}
        />
      )}
    </main>
  );
}

export default function RepositoriesRoute() {
  // useSearchParams demands a Suspense boundary at build time.
  return (
    <Suspense fallback={null}>
      <RepositoriesPage />
    </Suspense>
  );
}
