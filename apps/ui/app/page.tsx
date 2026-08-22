/**
 * Landing page — the front door.
 *
 * Static and server-rendered: the only client code is the scroll chrome
 * (progress hairline, reveal-on-scroll), so the page carries no dependency on
 * the API being up. The dashboard lives at `/runs`.
 */

import Link from "next/link";

import Reveal from "@/components/landing/Reveal";
import ScrollProgress from "@/components/landing/ScrollProgress";
import { MOCK_RUN_ID } from "@/lib/mockRun";

const STAGES: { id: string; title: string; detail: string }[] = [
  {
    id: "RESOLVE_MODEL",
    title: "Identify the robot",
    detail:
      "Pull its model from MuJoCo Menagerie, or have an agent synthesize one.",
  },
  {
    id: "BUILD_HARNESS",
    title: "Bind the code, unmodified",
    detail: "An agent wires your entrypoint into the simulator. No shims in your repo.",
  },
  {
    id: "DESIGN_SCENARIOS",
    title: "Decide what to randomize",
    detail: "A QA-lead agent picks payload, friction, sensor noise and latency.",
  },
  {
    id: "RUN_SUITE",
    title: "Run N worlds in parallel",
    detail: "Deterministic seeds, so every red result is reproducible on demand.",
  },
  {
    id: "CLUSTER_FAILURES",
    title: "Group by root cause",
    detail: "Forty failing seeds are usually three bugs wearing a disguise.",
  },
  {
    id: "INVESTIGATE",
    title: "One debugger per cluster",
    detail: "Reproduce, explain, attach evidence. Findings land on the blackboard.",
  },
  {
    id: "FIX",
    title: "One fixer per confirmed cause",
    detail: "Each patch self-verifies against the seeds that proved the bug.",
  },
  {
    id: "VERIFY",
    title: "Re-run the full suite",
    detail: "All patches merged. Red seeds must come back green — no exceptions.",
  },
  {
    id: "PR_OPENED",
    title: "Open the pull request",
    detail: "Incident report, diff, and before/after video, ready for review.",
  },
];

const PILLARS: { label: string; title: string; body: string }[] = [
  {
    label: "The oracle",
    title: "Simulation is the verdict",
    body: "simkit is deterministic, imports nothing from the agent layer and runs standalone. An agent that says \"fixed\" has fixed nothing until the seeds come back green.",
  },
  {
    label: "The team",
    title: "Seven roles, one blackboard",
    body: "Devin sessions cannot talk to each other, so the orchestrator mediates: every finding is written down, then relayed into the prompts that need it.",
  },
  {
    label: "The proof",
    title: "Video, diff, report",
    body: "Every run ends in artifacts a human can judge in a minute: what broke, why, the patch, and the same scenario before and after.",
  },
];

const QUICKSTART = `./scripts/setup.sh     # venv, editable installs, npm install
cp .env.example .env   # DEVIN_API_KEY, GITHUB_TOKEN
make menagerie         # robot model library
make dev               # api :8000 · dashboard :3000`;

export default function LandingPage() {
  return (
    <>
      <ScrollProgress />

      {/* No `overflow-hidden` on this wrapper: it would clip the sticky
          header out of the viewport, and hide any real horizontal overflow. */}
      <div className="relative">
        {/* Faint grid + glow: enough texture to read as a product, not a demo.
            Clipped by their own fixed viewport box so the wide glow never adds
            horizontal scroll. */}
        <div
          aria-hidden
          className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
        >
          <div className="absolute inset-0 opacity-[0.35] [background-image:linear-gradient(to_right,#1f2a35_1px,transparent_1px),linear-gradient(to_bottom,#1f2a35_1px,transparent_1px)] [background-size:64px_64px] [mask-image:radial-gradient(ellipse_at_50%_0%,black,transparent_75%)]" />
          <div className="absolute left-1/2 top-[-18rem] h-[36rem] w-[72rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-[140px]" />
        </div>

        <header className="sticky top-0 z-40 border-b border-surface-border/60 bg-surface/80 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
            <Link href="/" className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-status-passed shadow-[0_0_12px] shadow-emerald-400/70" />
              <span className="text-sm font-semibold tracking-tight">Robot CI</span>
            </Link>
            <nav className="hidden items-center gap-8 font-mono text-[11px] uppercase tracking-widest text-slate-400 sm:flex">
              <a href="#pipeline" className="transition-colors hover:text-slate-100">
                Pipeline
              </a>
              <a href="#principles" className="transition-colors hover:text-slate-100">
                Principles
              </a>
              <a href="#start" className="transition-colors hover:text-slate-100">
                Start
              </a>
            </nav>
            <Link
              href="/runs"
              className="rounded-full border border-surface-border px-4 py-1.5 font-mono text-[11px] uppercase tracking-widest text-slate-200 transition-colors hover:border-sky-500/70 hover:text-sky-300"
            >
              Dashboard
            </Link>
          </div>
        </header>

        <main>
          {/* Hero */}
          <section className="mx-auto flex min-h-[86vh] max-w-6xl flex-col justify-center px-6 py-24">
            <Reveal>
              <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">
                Autonomous CI for robotics
              </p>
            </Reveal>
            <Reveal delay={80}>
              <h1 className="mt-6 max-w-4xl text-balance text-5xl font-semibold leading-[1.05] tracking-tight text-slate-50 sm:text-6xl md:text-7xl">
                Push robot control code.
                <span className="block text-slate-500">
                  Get it back proven.
                </span>
              </h1>
            </Reveal>
            <Reveal delay={160}>
              <p className="mt-8 max-w-xl text-lg leading-relaxed text-slate-400">
                An autonomous engineering team simulates every commit, finds what
                breaks, fixes it, and opens a pull request with video proof — no
                human in the loop.
              </p>
            </Reveal>
            <Reveal delay={240}>
              <div className="mt-10 flex flex-wrap items-center gap-3">
                <Link
                  href="/runs"
                  className="rounded-full bg-slate-100 px-6 py-2.5 text-sm font-medium text-slate-900 transition-colors hover:bg-white"
                >
                  Open mission control
                </Link>
                <Link
                  href={`/runs/${MOCK_RUN_ID}`}
                  className="rounded-full border border-surface-border px-6 py-2.5 text-sm text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
                >
                  Watch a replay
                </Link>
              </div>
            </Reveal>
            <Reveal delay={320}>
              <div className="mt-20 flex items-center gap-3 font-mono text-[11px] uppercase tracking-widest text-slate-600">
                <span className="h-8 w-px animate-pulse bg-gradient-to-b from-slate-600 to-transparent" />
                Scroll
              </div>
            </Reveal>
          </section>

          {/* The gap */}
          <section className="border-y border-surface-border/60 bg-surface-raised/30">
            <div className="mx-auto grid max-w-6xl gap-12 px-6 py-24 md:grid-cols-2">
              <Reveal>
                <h2 className="text-3xl font-semibold tracking-tight text-slate-100 sm:text-4xl">
                  Robotics has a closed loop too.
                  <br />
                  Nobody wired an engineer into it.
                </h2>
              </Reveal>
              <Reveal delay={120} className="space-y-5 text-slate-400">
                <p>
                  Software engineering has CI because the loop is programmable:
                  write, run, read the failure, fix, re-run. Robotics has the same
                  loop — it is called simulation, and it is deterministic, fast and
                  already installed in every serious lab.
                </p>
                <p>
                  Yet control code still gets flashed to a machine, tried once by
                  hand, and shipped. Regressions surface weeks later on physical
                  hardware, where they are expensive and occasionally dangerous.
                </p>
              </Reveal>
            </div>
          </section>

          {/* Pipeline */}
          <section id="pipeline" className="mx-auto max-w-6xl px-6 py-28 scroll-mt-20">
            <Reveal>
              <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">
                One push
              </p>
              <h2 className="mt-4 text-3xl font-semibold tracking-tight text-slate-100 sm:text-4xl">
                Nine stages, zero humans
              </h2>
            </Reveal>
            <div className="mt-14 divide-y divide-surface-border/70 border-y border-surface-border/70">
              {STAGES.map((stage, index) => (
                <Reveal key={stage.id} delay={index * 40}>
                  <div className="group grid gap-2 py-6 md:grid-cols-[3rem_16rem_1fr] md:items-baseline md:gap-8">
                    <span className="font-mono text-xs text-slate-600 transition-colors group-hover:text-sky-400">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
                        {stage.id}
                      </div>
                      <div className="mt-1 text-lg text-slate-100">{stage.title}</div>
                    </div>
                    <p className="text-sm leading-relaxed text-slate-400">
                      {stage.detail}
                    </p>
                  </div>
                </Reveal>
              ))}
            </div>
          </section>

          {/* Commitment */}
          <section className="border-y border-surface-border/60 bg-surface-raised/30">
            <div className="mx-auto max-w-4xl px-6 py-28 text-center">
              <Reveal>
                <p className="text-balance text-3xl font-semibold leading-tight tracking-tight text-slate-100 sm:text-5xl">
                  Agents propose.
                  <span className="text-status-passed"> Simulation disposes.</span>
                </p>
              </Reveal>
              <Reveal delay={120}>
                <p className="mx-auto mt-8 max-w-xl text-slate-400">
                  Nothing an agent claims is accepted without a simulation result
                  behind it.
                </p>
              </Reveal>
            </div>
          </section>

          {/* Principles */}
          <section id="principles" className="mx-auto max-w-6xl px-6 py-28 scroll-mt-20">
            <div className="grid gap-px overflow-hidden rounded-2xl border border-surface-border/70 bg-surface-border/70 md:grid-cols-3">
              {PILLARS.map((pillar, index) => (
                <Reveal key={pillar.label} delay={index * 100} className="h-full">
                  <div className="flex h-full flex-col bg-surface p-8 transition-colors hover:bg-surface-raised/60">
                    <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
                      {pillar.label}
                    </div>
                    <h3 className="mt-4 text-xl font-medium tracking-tight text-slate-100">
                      {pillar.title}
                    </h3>
                    <p className="mt-4 text-sm leading-relaxed text-slate-400">
                      {pillar.body}
                    </p>
                  </div>
                </Reveal>
              ))}
            </div>
          </section>

          {/* Quickstart */}
          <section id="start" className="mx-auto max-w-6xl px-6 pb-28 scroll-mt-20">
            <div className="grid gap-12 md:grid-cols-2 md:items-center">
              <Reveal>
                <h2 className="text-3xl font-semibold tracking-tight text-slate-100 sm:text-4xl">
                  Point it at your repo
                </h2>
                <p className="mt-6 text-slate-400">
                  Robot CI watches from the outside — your code is never modified to
                  accommodate it. Send a push webhook, and optionally drop a{" "}
                  <code className="font-mono text-slate-300">robotci.yaml</code> at
                  the root. Every field is optional except the entrypoint; the rest
                  is inferred.
                </p>
                <div className="mt-8 flex flex-wrap gap-3">
                  <Link
                    href="/runs"
                    className="rounded-full bg-slate-100 px-6 py-2.5 text-sm font-medium text-slate-900 transition-colors hover:bg-white"
                  >
                    Open mission control
                  </Link>
                  <a
                    href="https://github.com/aldfjla/EHL-Cognition"
                    className="rounded-full border border-surface-border px-6 py-2.5 text-sm text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
                  >
                    Read the docs
                  </a>
                </div>
              </Reveal>
              {/* min-w-0: without it the grid item takes the pre's content
                  width and the section overflows on narrow viewports. */}
              <Reveal delay={120} className="min-w-0">
                <pre className="overflow-x-auto rounded-2xl border border-surface-border bg-surface-raised/60 p-6 font-mono text-[13px] leading-relaxed text-slate-300">
                  {QUICKSTART}
                </pre>
              </Reveal>
            </div>
          </section>
        </main>

        <footer className="border-t border-surface-border/60">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-10 font-mono text-[11px] uppercase tracking-widest text-slate-600 sm:flex-row sm:items-center sm:justify-between">
            <span>Robot CI · TUM.ai × EHL hackathon</span>
            <Link href="/runs" className="transition-colors hover:text-slate-300">
              /runs
            </Link>
          </div>
        </footer>
      </div>
    </>
  );
}
