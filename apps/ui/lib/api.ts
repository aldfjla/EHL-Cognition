/**
 * REST client for the FastAPI orchestrator.
 *
 * Responsibility: every HTTP call the dashboard makes, in one file, returning
 * the types from `./types`. Components never call `fetch` directly — a
 * component that builds its own URL is a component that breaks when a route
 * moves.
 *
 * The WebSocket lives in `./useEventStream`; this file is request/response only.
 */

import type { Agent, Finding, Message, Report, Run, RunEvent, Scenario } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** Thrown for any non-2xx response, carrying the status for the caller. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // TODO(build): fetch(`${API_BASE}${path}`), throw ApiError on !res.ok,
  // return res.json() as T. Add a short timeout via AbortController — a hung
  // API should surface as an error state, not an infinite skeleton.
  throw new Error("not implemented");
}

/** Runs, newest first. Backs the index page. */
export async function listRuns(limit = 25): Promise<Run[]> {
  return request<Run[]>(`/runs?limit=${limit}`);
}

/** One run with its scenarios and clusters — the mission control first paint. */
export async function getRun(runId: string): Promise<Run> {
  return request<Run>(`/runs/${runId}`);
}

/** The team roster for a run, in dispatch order. */
export async function getAgents(runId: string): Promise<Agent[]> {
  return request<Agent[]>(`/runs/${runId}/agents`);
}

/** The whole team's relayed traffic. */
export async function getMessages(runId: string): Promise<Message[]> {
  return request<Message[]>(`/runs/${runId}/messages`);
}

/** Messages to or from one agent. */
export async function getAgentMessages(agentId: string): Promise<Message[]> {
  return request<Message[]>(`/agents/${agentId}/messages`);
}

/** The blackboard contents — what the team currently believes. */
export async function getFindings(runId: string): Promise<Finding[]> {
  return request<Finding[]>(`/runs/${runId}/findings`);
}

/** The scenario matrix. */
export async function getScenarios(runId: string): Promise<Scenario[]> {
  return request<Scenario[]>(`/runs/${runId}/scenarios`);
}

/** The incident report, once REPORT has completed. */
export async function getReport(runId: string): Promise<Report> {
  return request<Report>(`/runs/${runId}/report`);
}

/** Replay buffered events after `since`. The WebSocket reconnect path. */
export async function getEvents(runId: string, since = 0): Promise<RunEvent[]> {
  return request<RunEvent[]>(`/runs/${runId}/events?since=${since}`);
}

/** Absolute URL for an artifact path returned on a scenario or incident. */
export function artifactUrl(path: string): string {
  // TODO(build): normalise — the API returns paths relative to ARTIFACTS_DIR,
  // and `<video src>` needs an absolute URL against API_BASE.
  return `${API_BASE}/artifacts/${path}`;
}

/** Start a run without GitHub. The demo trigger. */
export async function triggerRun(repo: string, sha: string): Promise<{ run_id: string }> {
  return request<{ run_id: string }>("/webhooks/manual", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo, sha }),
  });
}
