import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AuditEvent, IncidentState, TimelineEntry } from "../types";
import { PIPELINE_AGENTS, severityName, typeLabel } from "../types";
import {
  escalationBadge,
  formatClock,
  formatDateTime,
  humanizeEvent,
  isTerminalStatus,
  pct,
  severityBadgeClass,
  statusBadgeClass,
  statusLabel,
} from "./badges";
import AlertBanner from "./AlertBanner";
import EmptyState from "./EmptyState";
import { Skeleton, SkeletonRows } from "./Skeleton";

interface Props {
  incidentId: string | null;
  liveUpdate: IncidentState | null;
  onIncidentChanged: (inc: IncidentState) => void;
}

type AgentRunState = "done" | "failed" | "running" | "pending";

function runState(incident: IncidentState, key: string): AgentRunState {
  const out = incident.agent_outputs?.[key];
  if (out) return out.success ? "done" : "failed";
  return isTerminalStatus(incident.current_status) ? "pending" : "running";
}
  const entries = incident.timeline ?? [];
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const t = entries[i];
    if (/escalat/i.test(t.event ?? "") || /escalat/i.test(t.detail ?? "")) {
      return t.detail || t.event;
    }
  }
  return "A human dispatcher has been notified.";
}

function stateStyles(state: AgentRunState): {
  ring: string;
  num: string;
  label: string;
  labelClass: string;
} {
  switch (state) {
    case "done":
      return {
        ring: "border-emerald-500/50",
        num: "bg-emerald-500/20 text-emerald-300",
        label: "Done",
        labelClass: "text-emerald-400",
      };
    case "failed":
      return {
        ring: "border-red-500/60",
        num: "bg-red-500/20 text-red-300",
        label: "Failed",
        labelClass: "text-red-400",
      };
    case "running":
      return {
        ring: "border-sky-500/60",
        num: "bg-sky-500/20 text-sky-300 relay-pulse",
        label: "Running",
        labelClass: "text-sky-300",
      };
    default:
      return {
        ring: "border-relay-border",
        num: "bg-slate-500/20 text-slate-400",
        label: "Pending",
        labelClass: "text-slate-500",
      };
  }
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
      {children}
    </h3>
  );
}

/** Timeline entries with a given event name, newest last. */
function timelineEvents(incident: IncidentState, event: string) {
  return (incident.timeline ?? []).filter((t) => t.event === event);
}

export default function IncidentDetail({
  incidentId,
  liveUpdate,
  onIncidentChanged,
}: Props) {
  const [incident, setIncident] = useState<IncidentState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [audit, setAudit] = useState<AuditEvent[] | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [drilling, setDrilling] = useState<string | null>(null);

  useEffect(() => {
    setIncident(null);
    setError(null);
    setAuditOpen(false);
    setAudit(null);
    if (!incidentId) return;
    setLoading(true);
    api
      .getIncident(incidentId)
      .then(setIncident)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load incident"),
      )
      .finally(() => setLoading(false));
  }, [incidentId]);

  useEffect(() => {
    if (liveUpdate && liveUpdate.incident_id === incidentId) {
      setIncident(liveUpdate);
    }
  }, [liveUpdate, incidentId]);

  const toggleAudit = () => {
    const next = !auditOpen;
    setAuditOpen(next);
    if (next && audit === null && incidentId) {
      setAuditLoading(true);
      api
        .getAudit(incidentId)
        .then((r) => setAudit(r.events ?? []))
        .catch(() => setAudit([]))
        .finally(() => setAuditLoading(false));
    }
  };

  const handleResolve = async () => {
    if (!incident || resolving) return;
    setResolving(true);
    setError(null);
    try {
      const updated = await api.resolveIncident(incident.incident_id);
      setIncident(updated);
      onIncidentChanged(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Resolve failed");
    } finally {
      setResolving(false);
    }
  };

  const handleDrill = async (
    kind: "ambulance" | "hospital",
    resourceId: string,
  ) => {
    if (!incident || drilling) return;
    setDrilling(kind);
    setError(null);
    try {
      const updated = await api.injectFailure(incident.incident_id, {
        resource_type: kind,
        resource_id: resourceId,
        reason: kind === "ambulance" ? "breakdown" : "power outage",
      });
      setIncident(updated);
      onIncidentChanged(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failure drill failed");
    } finally {
      setDrilling(null);
    }
  };

  if (!incidentId) {
    return (
      <section className="flex items-center justify-center rounded-lg border border-relay-border bg-relay-panel p-8">
        <p className="text-center text-sm text-slate-500">
          Select an incident to inspect the agent pipeline.
        </p>
      </section>
    );
  }

  if (loading || !incident) {
    return (
      <section className="rounded-lg border border-relay-border bg-relay-panel p-8">
        <p className="text-sm text-slate-400">
          {error ?? "Loading incident…"}
        </p>
      </section>
    );
  }

  const esc = escalationBadge(incident.escalation_status);
  const escalated = incident.escalation_status === "escalated";
  const replanning = incident.escalation_status === "replanning";
  const resolved = isTerminalStatus(incident.current_status);
  const verification = incident.verification_results;
  const inReview =
    incident.current_status === "human_review_required" &&
    (incident.review?.status === "pending" ||
      incident.review?.status === "info_requested");
  const canDrill =
    !resolved &&
    !inReview &&
    ((incident.selected_ambulance !== null &&
      incident.ambulance_status === "en_route") ||
      (incident.selected_hospital !== null &&
        incident.hospital_status === "reserved"));

  return (
    <section className="min-h-0 rounded-lg border border-relay-border bg-relay-panel">
      <div className="space-y-4 overflow-y-auto p-4 xl:max-h-[calc(100vh-240px)]">
        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-bold text-slate-100">
                {typeLabel(incident.incident_type)}
              </h2>
              <span className={severityBadgeClass(incident.severity)}>
                {severityLabel(incident.severity)}
              </span>
              <span className={statusBadgeClass(incident.current_status)}>
                {statusLabel(incident.current_status)}
              </span>
              {esc && <span className={esc.className}>{esc.text}</span>}
            </div>
            <div className="mt-1 font-mono text-[11px] text-slate-500">
              {incident.incident_id} · {formatDateTime(incident.created_at)}
            </div>
          </div>
          {!resolved && (
            <button
              type="button"
              onClick={handleResolve}
              disabled={resolving}
              className="rounded-md border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-50"
            >
              {resolving ? "Resolving…" : "Resolve incident"}
            </button>
          )}
        </div>

        {error && (
          <div className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        {/* Escalation banner */}
        {escalated && (
          <div className="rounded-md border border-red-500/60 bg-red-500/15 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="relay-pulse inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
              <span className="text-sm font-bold tracking-wide text-red-300">
                ESCALATED — human dispatcher notified
              </span>
            </div>
            <p className="mt-1 text-sm text-red-200/80">
              {escalationReason(incident)}
            </p>
          </div>
        )}
        {replanning && (
          <div className="rounded-md border border-amber-500/60 bg-amber-500/10 px-4 py-3">
            <span className="text-sm font-bold tracking-wide text-amber-300">
              REPLANNING IN PROGRESS
            </span>
            <p className="mt-1 text-sm text-amber-200/80">
              The agents are revising the dispatch plan after a failed
              assignment.
            </p>
          </div>
        )}

        {/* Recovery events — rendered strictly from the backend timeline */}
        {timelineEvents(incident, "failure_detected").map((t, i) => (
          <div
            key={`fail-${i}`}
            className="rounded-md border border-red-500/60 bg-red-500/15 px-4 py-3"
          >
            <div className="flex items-center gap-2">
              <span className="relay-pulse inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
              <span className="text-sm font-bold tracking-wide text-red-300">
                FAILURE DETECTED
              </span>
              <span className="ml-auto font-mono text-[10px] text-red-200/60">
                {formatClock(t.ts)}
              </span>
            </div>
            <p className="mt-1 text-sm text-red-200/80">{t.detail}</p>
          </div>
        ))}
        {timelineEvents(incident, "conflict_detected").map((t, i) => (
          <div
            key={`conf-${i}`}
            className="rounded-md border border-red-500/60 bg-red-500/15 px-4 py-3"
          >
            <div className="flex items-center gap-2">
              <span className="relay-pulse inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
              <span className="text-sm font-bold tracking-wide text-red-300">
                AGENT CONFLICT DETECTED
              </span>
              <span className="ml-auto font-mono text-[10px] text-red-200/60">
                {formatClock(t.ts)}
              </span>
            </div>
            <p className="mt-1 text-sm text-red-200/80">{t.detail}</p>
          </div>
        ))}
        {timelineEvents(incident, "resource_replaced").map((t, i) => (
          <div
            key={`repl-${i}`}
            className="rounded-md border border-emerald-500/50 bg-emerald-500/10 px-4 py-3"
          >
            <div className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />
              <span className="text-sm font-bold tracking-wide text-emerald-300">
                NEW RESOURCE SELECTED
              </span>
              <span className="ml-auto font-mono text-[10px] text-emerald-200/60">
                {formatClock(t.ts)}
              </span>
            </div>
            <p className="mt-1 text-sm text-emerald-200/80">{t.detail}</p>
          </div>
        ))}

        {/* Failure drills (demo controls — hit the real recovery API) */}
        {canDrill && (
          <div className="rounded-md border border-dashed border-slate-600 bg-relay-panel2 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Failure drills
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {incident.selected_ambulance &&
                incident.ambulance_status === "en_route" && (
                  <button
                    type="button"
                    disabled={drilling !== null}
                    onClick={() =>
                      handleDrill("ambulance", incident.selected_ambulance!.id)
                    }
                    className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20 disabled:opacity-50"
                  >
                    {drilling === "ambulance"
                      ? "Injecting…"
                      : `▸ Simulate ${incident.selected_ambulance.id} breakdown`}
                  </button>
                )}
              {incident.selected_hospital &&
                incident.hospital_status === "reserved" && (
                  <button
                    type="button"
                    disabled={drilling !== null}
                    onClick={() =>
                      handleDrill("hospital", incident.selected_hospital!.id)
                    }
                    className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20 disabled:opacity-50"
                  >
                    {drilling === "hospital"
                      ? "Injecting…"
                      : `▸ Simulate ${incident.selected_hospital.name} outage`}
                  </button>
                )}
            </div>
            <p className="mt-1.5 text-[11px] text-slate-600">
              Drills run the real recovery pipeline: failure validation,
              replanning, re-dispatch, re-verification, family notification.
            </p>
          </div>
        )}

        {/* Agent pipeline */}
        <div>
          <SectionTitle>Agent pipeline</SectionTitle>
          <div className="mt-2 flex flex-wrap items-stretch gap-y-3">
            {PIPELINE_AGENTS.map((a, i) => {
              const out = incident.agent_outputs?.[a.key];
              const st = runState(incident, a.key);
              const s = stateStyles(st);
              return (
                <div key={a.key} className="flex items-stretch">
                  <div
                    className={`w-40 shrink-0 rounded-md border bg-relay-panel2 p-2.5 ${s.ring}`}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${s.num}`}
                      >
                        {i + 1}
                      </span>
                      <span className="truncate text-xs font-semibold text-slate-200">
                        {a.label}
                      </span>
                    </div>
                    <div
                      className={`mt-1.5 text-[11px] font-medium ${s.labelClass}`}
                    >
                      {s.label}
                    </div>
                    {out && (
                      <>
                        <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-relay-bg">
                          <div
                            className={`h-full rounded transition-all ${
                              st === "failed" ? "bg-red-400" : "bg-sky-400"
                            }`}
                            style={{
                              width: `${
                                out.confidence > 1
                                  ? out.confidence
                                  : out.confidence * 100
                              }%`,
                            }}
                          />
                        </div>
                        <div className="mt-1 text-[10px] text-slate-500">
                          confidence {pct(out.confidence)}
                        </div>
                        {out.rationale && (
                          <p className="mt-1 line-clamp-3 text-[11px] leading-snug text-slate-400">
                            {out.rationale}
                          </p>
                        )}
                      </>
                    )}
                  </div>
                  {i < PIPELINE_AGENTS.length - 1 && (
                    <div className="flex items-center px-1 text-slate-600">
                      <span aria-hidden>→</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Verification */}
        <div>
          <SectionTitle>Verification</SectionTitle>
          <div
            className={`mt-2 rounded-md border p-3 ${
              !verification
                ? "border-relay-border bg-relay-panel2"
                : verification.passed
                  ? "border-emerald-500/40 bg-emerald-500/5"
                  : "border-red-500/50 bg-red-500/5"
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold border ${
                  !verification
                    ? "border-relay-border text-slate-400"
                    : verification.passed
                      ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-400"
                      : "border-red-500/50 bg-red-500/15 text-red-400"
                }`}
              >
                {!verification
                  ? "AWAITING"
                  : verification.passed
                    ? "PASSED"
                    : "FAILED"}
              </span>
              <span className="text-xs text-slate-500">
                Independent cross-check of the dispatch plan
              </span>
            </div>
            {verification && (
              <>
                <ul className="mt-2 space-y-1.5">
                  {verification.checks.map((c, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs">
                      <span
                        className={
                          c.passed
                            ? "font-bold text-emerald-400"
                            : "font-bold text-red-400"
                        }
                      >
                        {c.passed ? "✓" : "✗"}
                      </span>
                      <span>
                        <span className="font-medium text-slate-200">
                          {c.name}
                        </span>
                        {c.detail && (
                          <span className="text-slate-500"> — {c.detail}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
                {verification.issues.length > 0 && (
                  <ul className="mt-2 space-y-1 border-t border-red-500/30 pt-2">
                    {verification.issues.map((issue, i) => (
                      <li key={i} className="text-xs text-red-300">
                        • {issue}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>

        {/* Resources */}
        <div>
          <SectionTitle>Resources</SectionTitle>
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            <div className="rounded-md border border-relay-border bg-relay-panel2 p-3">
              <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
                Ambulance
              </div>
              {incident.selected_ambulance ? (
                <>
                  <div className="mt-1 font-mono text-sm font-semibold text-slate-100">
                    {incident.selected_ambulance.id}
                  </div>
                  <div className="text-xs text-slate-400">
                    {incident.selected_ambulance.capability} · ETA{" "}
                    {incident.selected_ambulance.eta_min} min
                  </div>
                </>
              ) : (
                <div className="mt-1 text-sm text-slate-500">Not assigned</div>
              )}
              <div className="mt-1.5">
                <span
                  className={statusBadgeClass(
                    incident.ambulance_status === "en_route"
                      ? "completed"
                      : incident.ambulance_status === "unavailable"
                        ? "failed"
                        : "active",
                  )}
                >
                  {incident.ambulance_status}
                </span>
              </div>
            </div>
            <div className="rounded-md border border-relay-border bg-relay-panel2 p-3">
              <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
                Hospital
              </div>
              {incident.selected_hospital ? (
                <>
                  <div className="mt-1 text-sm font-semibold text-slate-100">
                    {incident.selected_hospital.name}
                  </div>
                  <div className="text-xs text-slate-400">
                    {incident.selected_hospital.distance_km.toFixed(1)} km away
                  </div>
                </>
              ) : (
                <div className="mt-1 text-sm text-slate-500">Not assigned</div>
              )}
              <div className="mt-1.5">
                <span
                  className={statusBadgeClass(
                    incident.hospital_status === "reserved"
                      ? "completed"
                      : incident.hospital_status === "unavailable"
                        ? "failed"
                        : "active",
                  )}
                >
                  {incident.hospital_status}
                </span>
              </div>
            </div>
          </div>
          <div className="mt-3 rounded-md border border-relay-border bg-relay-panel2 p-3 text-xs text-slate-400">
            <div className="font-medium text-slate-300">
              {incident.location.address}
            </div>
            <div className="mt-1 font-mono text-[11px] text-slate-500">
              {incident.location.lat.toFixed(4)}, {incident.location.lon.toFixed(4)}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
              <span>Patients: {incident.patient_count}</span>
              <span>Breathing: {incident.breathing_status}</span>
              <span>Bleeding: {incident.bleeding_status}</span>
            </div>
            {incident.symptoms.length > 0 && (
              <div className="mt-1">Symptoms: {incident.symptoms.join(", ")}</div>
            )}
            {incident.triage_result && (
              <div className="mt-2 border-t border-relay-border pt-2">
                <span className="font-medium text-slate-300">Triage: </span>
                severity S{incident.triage_result.severity} ·{" "}
                {incident.triage_result.pathway} · confidence{" "}
                {pct(incident.triage_result.confidence)}
                <p className="mt-0.5 text-slate-500">
                  {incident.triage_result.rationale}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Communications */}
        <div>
          <SectionTitle>Family communications</SectionTitle>
          {incident.communications.length === 0 ? (
            <p className="mt-2 text-xs text-slate-500">No messages sent yet.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {incident.communications.map((m, i) => (
                <li
                  key={i}
                  className="rounded-md border border-relay-border bg-relay-panel2 p-3"
                >
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-[11px] font-medium text-sky-300">
                      {m.channel}
                    </span>
                    <span className="ml-auto font-mono text-[10px] text-slate-500">
                      {formatClock(m.ts)}
                    </span>
                  </div>
                  <p className="mt-1.5 text-sm leading-snug text-slate-200">
                    {m.text}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Timeline */}
        <div>
          <SectionTitle>Timeline</SectionTitle>
          <ol className="mt-2 space-y-2">
            {(incident.timeline ?? []).map((t, i) => (
              <li key={i} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-slate-500" />
                  {i < incident.timeline.length - 1 && (
                    <span className="w-px flex-1 bg-relay-border" />
                  )}
                </div>
                <div className="pb-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-xs font-medium text-slate-200">
                      {humanizeEvent(t.event)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {formatClock(t.ts)}
                    </span>
                  </div>
                  {t.detail && (
                    <p className="text-[11px] text-slate-500">{t.detail}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>

        {/* Audit */}
        <div>
          <button
            type="button"
            onClick={toggleAudit}
            className="flex w-full items-center justify-between rounded-md border border-relay-border bg-relay-panel2 px-3 py-2 text-left transition-colors hover:border-slate-500"
          >
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">
              Audit trail
            </span>
            <span className="text-xs text-slate-500">
              {auditOpen ? "▾ collapse" : "▸ expand"}
            </span>
          </button>
          {auditOpen && (
            <div className="mt-2 max-h-64 overflow-y-auto rounded-md border border-relay-border bg-relay-bg p-3">
              {auditLoading ? (
                <p className="text-xs text-slate-500">Loading audit…</p>
              ) : !audit || audit.length === 0 ? (
                <p className="text-xs text-slate-500">No audit events.</p>
              ) : (
                <ul className="space-y-2">
                  {audit.map((ev) => (
                    <li
                      key={ev.id}
                      className="border-b border-relay-border/50 pb-2 font-mono text-[11px] last:border-0"
                    >
                      <div className="flex flex-wrap gap-x-2 text-slate-400">
                        <span className="text-slate-500">
                          {formatClock(ev.ts)}
                        </span>
                        <span className="font-semibold text-slate-300">
                          {ev.agent}
                        </span>
                        <span>{ev.action}</span>
                        <span className="ml-auto text-slate-500">
                          {pct(ev.confidence)}
                        </span>
                      </div>
                      {ev.rationale && (
                        <p className="mt-0.5 font-sans text-[11px] text-slate-500">
                          {ev.rationale}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
