/* TypeScript interfaces mirroring the MedRelay backend API contract. */

export interface Location {
  lat: number;
  lon: number;
  address: string;
}

export interface TriageResult {
  severity: number;
  pathway: string;
  confidence: number;
  rationale: string;
}

export interface SelectedAmbulance {
  id: string;
  capability: string;
  eta_min: number;
}

export interface SelectedHospital {
  id: string;
  name: string;
  distance_km: number;
}

export interface CommunicationMessage {
  channel: string;
  text: string;
  ts: string;
}

export interface AgentOutput {
  agent_name: string;
  confidence: number;
  rationale: string;
  data: Record<string, unknown> | null;
  success: boolean;
  /* Canonical envelope fields (sent by the backend; mirrored from above). */
  agent: string;
  incident_id: string;
  status: "success" | "failed" | "escalated";
  decision: Record<string, unknown>;
  reasoning_summary: string;
  warnings: string[];
  requires_human: boolean;
  timestamp: string;
}

export interface VerificationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface VerificationResults {
  passed: boolean;
  issues: string[];
  checks: VerificationCheck[];
}

export interface TimelineEntry {
  ts: string;
  event: string;
  detail: string;
}

export type EscalationStatus = "none" | "replanning" | "escalated";

export interface ReviewState {
  status: string; // none | pending | approved | rejected | info_requested | replanned
  reason: string;
  affected_decision: string;
  confidence: number;
  note: string;
  decided_at: string | null;
}

export interface IncidentState {
  incident_id: string;
  created_at: string;
  incident_type: string;
  location: Location;
  patient_count: number;
  symptoms: string[];
  breathing_status: string;
  bleeding_status: string;
  severity: number | null;
  triage_result: TriageResult | null;
  selected_ambulance: SelectedAmbulance | null;
  selected_hospital: SelectedHospital | null;
  hospital_status: "pending" | "reserved" | "unavailable";
  ambulance_status: "pending" | "en_route" | "unavailable";
  family_contact: string;
  communications: CommunicationMessage[];
  agent_outputs: Record<string, AgentOutput>;
  verification_results: VerificationResults | null;
  confidence: number;
  current_status: string;
  escalation_status: EscalationStatus;
  review: ReviewState;
  timeline: TimelineEntry[];
}

/** Compact row from GET /api/incidents, newest first. */
export interface IncidentSummary {
  incident_id: string;
  incident_type: string;
  severity: number | null;
  current_status: string;
  escalation_status: string;
  created_at: string;
  ambulance_id: string | null;
  hospital_name: string | null;
}

export interface AuditEvent {
  id: string;
  ts: string;
  agent: string;
  action: string;
  rationale: string;
  confidence: number;
}

export interface AuditLog {
  incident_id: string;
  events: AuditEvent[];
}

export interface Ambulance {
  id: string;
  lat: number;
  lon: number;
  capability: string;
  status: string;
  assigned_incident: string | null;
}

export interface Hospital {
  id: string;
  name: string;
  lat: number;
  lon: number;
  specialties: string[];
  total_beds: number;
  free_beds: number;
}

export interface Fleet {
  ambulances: Ambulance[];
  hospitals: Hospital[];
}

export interface Stats {
  total: number;
  active: number;
  completed: number;
  escalated: number;
  avg_confidence: number;
}

export interface Health {
  status: string;
  version: string;
  llm_provider: string;
  incidents: number;
  agents: string[];
}

/* ---------- WebSocket messages ---------- */

export interface WsAgentEvent {
  type: "agent_event";
  incident_id: string;
  agent: string;
  action: string;
  rationale: string;
  confidence: number;
  ts: string;
}

export interface WsIncidentUpdate {
  type: "incident_update";
  incident: IncidentState;
}

export interface WsFleetUpdate {
  type: "fleet_update";
  fleet: Fleet;
}

export interface WsIncidentList {
  type: "incident_list";
  incidents: IncidentSummary[];
}

export type WsMessage =
  | WsAgentEvent
  | WsIncidentUpdate
  | WsFleetUpdate
  | WsIncidentList;

/* ---------- Request payloads ---------- */

export interface CreateIncidentPayload {
  incident_type: string;
  lat: number;
  lon: number;
  address: string;
  patient_count: number;
  symptoms: string[];
  breathing_status: string;
  bleeding_status: string;
  family_contact: string;
}

export interface FailureInjectionPayload {
  resource_type: "ambulance" | "hospital" | "conflict";
  resource_id?: string;
  reason?: string;
  conflict?: Record<string, unknown>;
}

export type ReviewDecision = "approve" | "reject" | "request_info" | "replan";

export interface ReviewDossier {
  incident_id: string;
  current_status: string;
  review: ReviewState;
  reason: string;
  affected_decision: string;
  confidence: number;
  agent_outputs: Record<string, AgentOutput>;
}

/* ---------- Display helpers ---------- */

/** Agents in the order the pipeline executes, for the pipeline view. */
export const PIPELINE_AGENTS: Array<{ key: string; label: string }> = [
  { key: "IntakeAgent", label: "Intake" },
  { key: "TriageAgent", label: "Triage" },
  { key: "DispatchAgent", label: "Dispatch" },
  { key: "HospitalLiaisonAgent", label: "Hospital Liaison" },
  { key: "VerificationAgent", label: "Verification" },
  { key: "CommunicationAgent", label: "Communication" },
];

export const INCIDENT_TYPE_LABELS: Record<string, string> = {
  cardiac_arrest: "Cardiac Arrest",
  road_accident: "Road Accident",
  stroke: "Stroke",
  trauma_fall: "Trauma — Fall",
  obstetric: "Obstetric Emergency",
  chest_pain: "Chest Pain",
  unknown_demo: "Unknown (Demo)",
};

export function typeLabel(t: string): string {
  return INCIDENT_TYPE_LABELS[t] ?? t.replace(/_/g, " ");
}

/** Numeric severity (2–5) → canonical label used across the dashboard. */
export const SEVERITY_NAMES: Record<number, string> = {
  5: "CRITICAL",
  4: "HIGH",
  3: "MODERATE",
  2: "LOW",
};

export function severityName(severity: number | null): string {
  if (severity == null) return "—";
  return SEVERITY_NAMES[severity] ?? `S${severity}`;
}

/** Live lifecycle state of one agent for one incident. */
export type AgentStatus =
  | "IDLE"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "WAITING"
  | "HUMAN REVIEW";

/* ---------- Demo mode ---------- */

export interface DemoStepRule {
  type: "action" | "action_all" | "incident_known" | "incident_status";
  match?: string;
  matches?: string[];
  status?: string;
}

export interface DemoStep {
  id: string;
  label: string;
  rule: DemoStepRule;
}

export interface DemoScenario {
  id: string;
  name: string;
  description: string;
  flagship?: boolean;
  steps: DemoStep[];
}

export type DemoRunState = "running" | "done" | "failed" | "cancelled";

export interface DemoRunStatus {
  run_id: string;
  scenario_id: string;
  status: DemoRunState;
  incident_id: string | null;
  terminal_state: string | null;
  error: string;
  started_at: string;
  finished_at: string | null;
}
