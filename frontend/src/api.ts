/* Typed fetch helpers for the MedRelay backend API (same origin). */
import type {
  AuditLog,
  CreateIncidentPayload,
  DemoRunStatus,
  DemoScenario,
  FailureInjectionPayload,
  Fleet,
  Health,
  IncidentState,
  IncidentSummary,
  ReviewDecision,
  ReviewDossier,
  Stats,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${path}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  getStats: () => request<Stats>("/api/stats"),
  getIncidents: () =>
    request<{ incidents: IncidentSummary[] }>("/api/incidents"),
  getIncident: (id: string) =>
    request<IncidentState>(`/api/incidents/${encodeURIComponent(id)}`),
  getAudit: (id: string) =>
    request<AuditLog>(`/api/incidents/${encodeURIComponent(id)}/audit`),
  getFleet: () => request<Fleet>("/api/fleet"),
  createIncident: (payload: CreateIncidentPayload) =>
    request<IncidentState>("/api/incidents", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  resolveIncident: (id: string) =>
    request<IncidentState>(`/api/incidents/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
    }),
  injectFailure: (id: string, payload: FailureInjectionPayload) =>
    request<IncidentState>(
      `/api/incidents/${encodeURIComponent(id)}/failures`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  getReview: (id: string) =>
    request<ReviewDossier>(`/api/incidents/${encodeURIComponent(id)}/review`),
  decideReview: (id: string, decision: ReviewDecision, note?: string) =>
    request<IncidentState>(
      `/api/incidents/${encodeURIComponent(id)}/review/decision`,
      { method: "POST", body: JSON.stringify({ decision, note: note ?? "" }) },
    ),
  /* Demo mode (hackathon judging). */
  listDemoScenarios: () =>
    request<{ scenarios: DemoScenario[] }>("/api/demo/scenarios"),
  runDemoScenario: (id: string) =>
    request<{ scenario_run_id: string; incident_id: string | null }>(
      `/api/demo/scenarios/${encodeURIComponent(id)}/run`,
      { method: "POST" },
    ),
  getDemoRun: (runId: string) =>
    request<DemoRunStatus>(
      `/api/demo/scenarios/runs/${encodeURIComponent(runId)}`,
    ),
  resetDemo: () =>
    request<{ status: string; incidents_deleted: number }>("/api/demo/reset", {
      method: "POST",
    }),
};
