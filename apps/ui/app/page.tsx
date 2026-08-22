/**
 * Runs index — every CI run, newest first.
 *
 * Entry point of the dashboard. Each row links to that run's mission control
 * page. Kept plain on purpose: the interesting screen is the run detail, and
 * this one exists to get there.
 */

import Link from "next/link";

export default function RunsIndexPage() {
  // TODO(build): fetch via api.listRuns(); subscribe to WS /ws/runs for
  // run.created and run.stage_changed so a new push appears without a refresh.
  const runs: { id: string; repo: string; stage: string }[] = [];

  return (
    <main className="mx-auto max-w-5xl p-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Robot CI</h1>
        <p className="mt-1 text-sm text-slate-400">
          Autonomous CI for robot control code. Every push is simulated,
          debugged and fixed without a human in the loop.
        </p>
      </header>

      <section className="stub">
        <div className="stub-label">Runs list</div>
        {runs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">
            No runs yet. Trigger one with{" "}
            <code className="font-mono text-slate-300">make seed</code> or push
            to the watched repo.
          </p>
        ) : (
          <ul className="mt-2 space-y-1">
            {runs.map((run) => (
              <li key={run.id}>
                <Link
                  href={`/runs/${run.id}`}
                  className="font-mono text-sm text-sky-400 hover:underline"
                >
                  {run.id} · {run.repo} · {run.stage}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
