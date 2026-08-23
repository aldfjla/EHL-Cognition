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

import type {
  Agent,
  ConnectedRepo,
  ConnectRepoResponse,
  Finding,
  InternalDbRow,
  InternalDbRows,
  InternalDbTable,
  InternalDbValue,
  Message,
  Report,
  Run,
  RunDetail,
  RunEvent,
  Scenario,
  TriggerRunResponse,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** A hung API must surface as an error state, not an infinite skeleton. */
export const REQUEST_TIMEOUT_MS = 8000;

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
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = (await res.clone().json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = `: ${body.detail}`;
      } catch {
        // Non-JSON error responses still retain their status and status text.
      }
      throw new ApiError(
        `${init?.method ?? "GET"} ${path} failed: ${res.status} ${res.statusText}${detail}`,
        res.status,
      );
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof Error && err.name === "AbortError") {
      throw new ApiError(`${path} timed out after ${REQUEST_TIMEOUT_MS}ms`, 504);
    }
    throw new ApiError(`${path} unreachable: ${(err as Error).message}`, 0);
  } finally {
    clearTimeout(timer);
  }
}

/** Runs, newest first. Backs the index page. */
export async function listRuns(limit = 25): Promise<Run[]> {
  return request<Run[]>(`/runs?limit=${limit}`);
}

/** One run with its scenarios and clusters — the mission control first paint. */
export async function getRun(runId: string): Promise<RunDetail> {
  const detail = await request<RunDetail>(`/runs/${runId}`);
  return {
    ...detail,
    scenarios: detail.scenarios ?? [],
    clusters: detail.clusters ?? [],
  };
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
  if (/^(https?:|blob:|data:)/.test(path)) return path;

  const rel = path.replace(/^\.?\/+/, "");
  const segments = rel.split("/").filter(Boolean).map(encodeURIComponent);

  // mp4s go through the range-capable video route (seeking breaks without it);
  // anything else falls through to the plain artifact path space.
  if (segments.length === 2 && rel.endsWith(".mp4")) {
    return `${API_BASE}/artifacts/video/${segments.join("/")}`;
  }
  return `${API_BASE}/artifacts/${segments.join("/")}`;
}

/** Connected repos — the ones a push will wake the system for. */
export async function listRepos(): Promise<ConnectedRepo[]> {
  return request<ConnectedRepo[]>("/repos");
}

/** Connect a repo. Returns the repo plus webhook setup instructions. */
export async function connectRepo(body: {
  full_name: string;
  branch?: string;
  suite_size?: number;
}): Promise<ConnectRepoResponse> {
  return request<ConnectRepoResponse>("/repos", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Update a connected repo's branch or suite size. */
export async function updateRepo(
  repoId: string,
  body: { branch?: string; suite_size?: number },
): Promise<ConnectedRepo> {
  return request<ConnectedRepo>(`/repos/${repoId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Disconnect a repo. Past runs are kept; future pushes are ignored. */
export async function disconnectRepo(repoId: string): Promise<void> {
  await request<unknown>(`/repos/${repoId}`, { method: "DELETE" });
}

/** Registered SQLModel tables for the local database browser. */
export async function listInternalDbTables(): Promise<InternalDbTable[]> {
  return request<InternalDbTable[]>("/internal/db/tables");
}

/** A page of raw stored values from one internal database table. */
export async function listInternalDbRows(
  table: string,
  limit = 50,
  offset = 0,
): Promise<InternalDbRows> {
  return request<InternalDbRows>(
    `/internal/db/tables/${encodeURIComponent(table)}/rows?limit=${limit}&offset=${offset}`,
  );
}

/** Update existing fields in one internal database row. */
export async function updateInternalDbRow(
  table: string,
  primaryKey: string,
  values: Record<string, InternalDbValue>,
): Promise<InternalDbRow> {
  return request<InternalDbRow>(
    `/internal/db/tables/${encodeURIComponent(table)}/rows/${encodeURIComponent(primaryKey)}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ values }),
    },
  );
}

/** Delete one internal database row. */
export async function deleteInternalDbRow(
  table: string,
  primaryKey: string,
): Promise<void> {
  await request<unknown>(
    `/internal/db/tables/${encodeURIComponent(table)}/rows/${encodeURIComponent(primaryKey)}`,
    { method: "DELETE" },
  );
}

/** Start a run without GitHub. Resolves the connected branch HEAD server-side. */
export async function triggerRun(repo: string): Promise<TriggerRunResponse> {
  return request<TriggerRunResponse>("/webhooks/manual", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo }),
  });
}
