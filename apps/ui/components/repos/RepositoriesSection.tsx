"use client";

/**
 * Repository controls — connect a GitHub repo so a push wakes the system.
 *
 * The mental model this section sells: Robot CI is *dormant*. Connecting a repo
 * arms it; the first push to the watched branch wakes it and starts a run. Each
 * card shows that state honestly — dormant / running — plus the latest run and
 * the webhook the customer still has to install on GitHub's side.
 *
 * Sits above the run history on the dashboard; `?connect=1` opens the connect
 * dialog straight away (the ⌘K action), which is why the host page wraps this
 * in Suspense.
 */

import clsx from "clsx";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import * as api from "@/lib/api";
import { normalizeRepoInput } from "@/lib/repoInput";
import type {
  ConnectedRepo,
  ConnectRepoResponse,
  MenagerieModelInfo,
} from "@/lib/types";

const BASELINE_MODEL: MenagerieModelInfo = {
  name: "franka_emika_panda",
  dof: 9,
  kind: "arm",
};

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
  models,
  onModelUpdated,
  onDisconnect,
}: {
  repo: ConnectedRepo;
  models: MenagerieModelInfo[];
  onModelUpdated: (updated: ConnectedRepo) => void;
  onDisconnect: (repo: ConnectedRepo) => void;
}) {
  const running = repo.status === "running";
  const router = useRouter();
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const runAnalysis = async (): Promise<void> => {
    setRunBusy(true);
    setRunError(null);
    try {
      const result = await api.triggerRun(repo.full_name, undefined, repo.branch);
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      setRunError((err as Error).message);
      setRunBusy(false);
    }
  };

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
          onClick={() => void runAnalysis()}
          disabled={runBusy}
          className="ml-auto rounded border border-sky-700 px-2 py-1 font-mono text-[10px] text-sky-300 hover:border-sky-500 disabled:cursor-wait disabled:opacity-50"
        >
          {runBusy ? "Starting…" : "Run analysis"}
        </button>
        <button
          type="button"
          onClick={() => onDisconnect(repo)}
          className="font-mono text-[10px] text-slate-600 hover:text-status-failed"
        >
          disconnect
        </button>
      </div>
      {runError !== null && (
        <p className="mt-2 text-[10px] text-status-failed">{runError}</p>
      )}

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
      <RobotModelControl
        repo={repo}
        models={models}
        onUpdated={onModelUpdated}
      />
    </div>
  );
}

function RobotModelControl({
  repo,
  models,
  onUpdated,
}: {
  repo: ConnectedRepo;
  models: MenagerieModelInfo[];
  onUpdated: (updated: ConnectedRepo) => void;
}) {
  const availableModels = models.length > 0 ? models : [BASELINE_MODEL];
  const isKnownModel = availableModels.some(
    (model) => model.name === repo.robot_menagerie,
  );
  const [selection, setSelection] = useState(isKnownModel ? repo.robot_menagerie : "custom");
  const [customName, setCustomName] = useState(isKnownModel ? "" : repo.robot_menagerie);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const known = (models.length > 0 ? models : [BASELINE_MODEL]).some(
      (model) => model.name === repo.robot_menagerie,
    );
    setSelection(known ? repo.robot_menagerie : "custom");
    setCustomName(known ? "" : repo.robot_menagerie);
  }, [models, repo.robot_menagerie]);

  const save = async (name: string): Promise<void> => {
    const value = name.trim();
    if (!value) {
      setError("Enter a model name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateRepo(repo.id, { robot_menagerie: value });
      onUpdated(updated);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-4 border-t border-surface-border pt-3">
      <label className="block text-xs text-slate-400">
        Robot model
        <select
          value={selection}
          disabled={busy}
          onChange={(event) => {
            const value = event.target.value;
            setSelection(value);
            if (value !== "custom") void save(value);
          }}
          className="mt-1 w-full rounded border border-surface-border bg-surface px-2 py-1.5 font-mono text-xs text-slate-200 outline-none focus:border-sky-600"
        >
          {availableModels.map((model) => (
            <option key={model.name} value={model.name}>
              {model.name}
              {model.dof != null ? ` · ${model.dof} dof` : ""}
            </option>
          ))}
          <option value="custom">Custom…</option>
        </select>
      </label>
      {selection === "custom" && (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void save(customName);
          }}
        >
          <input
            value={customName}
            onChange={(event) => setCustomName(event.target.value)}
            placeholder="your_model_name"
            className="min-w-0 flex-1 rounded border border-surface-border bg-surface px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-sky-600"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded border border-sky-700 px-2 py-1 font-mono text-[10px] text-sky-300 hover:border-sky-500 disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </form>
      )}
      {error !== null && <p className="mt-1 text-[10px] text-status-failed">{error}</p>}
      <p className="mt-1 font-mono text-[10px] text-slate-500">
        current: {repo.robot_menagerie}
      </p>
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

  const normalized = normalizeRepoInput(fullName);

  const submit = async (): Promise<void> => {
    if (normalized === null) {
      setError("Paste a GitHub repository URL, or type owner/name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.connectRepo({
        full_name: normalized,
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
          GitHub repository — paste the URL
          <input
            autoFocus
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            placeholder="https://github.com/owner/robot-firmware"
            className="mt-1 w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-sky-600"
          />
        </label>
        <p className="mt-1 font-mono text-[10px] text-slate-500">
          {normalized === null
            ? "URL, git@ remote or owner/name — all accepted."
            : `will watch ${normalized}`}
        </p>

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
        Last step, on GitHub:{" "}
        <span className="font-mono">Settings → Webhooks → Add webhook</span>,
        content type <span className="font-mono">application/json</span>, event{" "}
        <span className="font-mono">push</span>, payload URL:
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

function RepositoriesSection() {
  const params = useSearchParams();
  const [repos, setRepos] = useState<ConnectedRepo[]>([]);
  const [models, setModels] = useState<MenagerieModelInfo[]>([]);
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

  useEffect(() => {
    void api
      .listModels()
      .then(setModels)
      .catch(() => setModels([BASELINE_MODEL]));
  }, []);

  const disconnect = async (repo: ConnectedRepo): Promise<void> => {
    if (
      !window.confirm(
        `Disconnect ${repo.full_name}? Future pushes will be ignored.`,
      )
    )
      return;
    await api.disconnectRepo(repo.id);
    void load();
  };

  return (
    <section className="mb-8">
      <header className="mb-4 flex items-baseline justify-between">
        <div className="stub-label">
          Repositories · {repos.length}
          {loading ? " · loading" : ""}
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
          <button
            type="button"
            onClick={() => setDialogOpen(true)}
            className="mt-4 rounded bg-sky-600 px-3 py-1.5 font-mono text-xs font-semibold text-white hover:bg-sky-500"
          >
            + Connect repository
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {repos.map((repo) => (
            <RepoCard
              key={repo.id}
              repo={repo}
              models={models}
              onModelUpdated={(updated) =>
                setRepos((current) =>
                  current.map((item) => (item.id === updated.id ? updated : item)),
                )
              }
              onDisconnect={(r) => void disconnect(r)}
            />
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
    </section>
  );
}

export default RepositoriesSection;
