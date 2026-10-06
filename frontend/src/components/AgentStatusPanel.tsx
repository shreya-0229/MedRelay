import { useMemo } from "react";
import type { AgentStatus, IncidentState, WsAgentEvent } from "../types";
import { PIPELINE_AGENTS } from "../types";
import { agentShortName, formatClock } from "./badges";
import SectionHeader from "./SectionHeader";
import EmptyState from "./EmptyState";

interface Props {
  /** Full state of the selected incident (null when nothing selected). */
  incident: IncidentState | null;
  /** Live WS agent events (newest first). */
  feed: WsAgentEvent[];
}

/** Predecessors in the pipeline graph — used to derive WAITING honestly. */
const PREDECESSORS: Record<string, string[]> = {
  IntakeAgent: [],
  TriageAgent: ["IntakeAgent"],
  DispatchAgent: ["TriageAgent"],
  HospitalLiaisonAgent: ["TriageAgent"],
  VerificationAgent: ["DispatchAgent", "HospitalLiaisonAgent"],
  CommunicationAgent: ["VerificationAgent"],
};

export interface AgentLiveState {
  key: string;
  label: string;
  status: AgentStatus;
  /** Real timestamp of the latest transition (output or WS event). */
  ts: string | null;
  confidence: number | null;
  note: string | null;
}

const START_RE = /start|search|beg[ai]n/i;

function statusStyle(status: AgentStatus): {
  dot: string;
  pill: string;
  label: string;
} {
  switch (status) {
    case "RUNNING":
      return {
        dot: "bg-sky-400 relay-pulse",
        pill: "border-sky-500/50 bg-sky-500/10 text-sky-700",
        label: "RUNNING",
      };
    case "COMPLETED":
      return {
        dot: "bg-emerald-400",
        pill: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700",
        label: "COMPLETED",
      };
    case "FAILED":
      return {
        dot: "bg-red-500",
        pill: "border-red-500/60 bg-red-500/15 text-red-800 font-bold",
        label: "FAILED",
      };
    case "WAITING":
      return {
        dot: "bg-slate-500",
        pill: "border-slate-500/40 bg-slate-500/10 text-slate-600",
        label: "WAITING",
      };
    case "HUMAN REVIEW":
      return {
        dot: "bg-amber-400 relay-pulse",
        pill: "border-amber-500/60 bg-amber-500/15 text-amber-800 font-bold",
        label: "HUMAN REVIEW",
      };
    default:
      return {
        dot: "bg-slate-700",
        pill: "border-relay-border bg-relay-bg text-slate-500",
        label: "IDLE",
      };
  }
}

/**
 * Derives each agent's live state from REAL backend data only:
 * - agent_outputs[agent] (with its canonical timestamp) → COMPLETED / FAILED / HUMAN REVIEW
 * - WS agent_event frames ("…started")               → RUNNING with the event's timestamp
 * - a predecessor already produced output              → WAITING
 * - otherwise                                          → IDLE
 * No hardcoded statuses, no synthetic activity.
 */
export function deriveAgentStates(
  incident: IncidentState | null,
  feed: WsAgentEvent[],
): AgentLiveState[] {
  const outputs = incident?.agent_outputs ?? {};
  const incidentId = incident?.incident_id ?? null;

  return PIPELINE_AGENTS.map(({ key, label }) => {
    const out = outputs[key];
    if (out) {
      const status: AgentStatus = out.requires_human
        ? "HUMAN REVIEW"
        : out.success
          ? "COMPLETED"
          : "FAILED";
      return {
        key,
        label,
        status,
        ts: out.timestamp ?? null,
        confidence: out.confidence ?? null,
        note: out.requires_human ? "flagged for human review" : null,
      };
    }
    if (incidentId) {
      const started = feed.find(
        (e) =>
          e.incident_id === incidentId &&
          e.agent === key &&
          START_RE.test(e.action),
      );
      if (started) {
        return {
          key,
          label,
          status: "RUNNING",
          ts: started.ts,
          confidence: null,
          note: started.action,
        };
      }
      const predDone = PREDECESSORS[key].some((p) => outputs[p]);
      if (predDone) {
        return { key, label, status: "WAITING", ts: null, confidence: null, note: null };
      }
    }
    return { key, label, status: "IDLE", ts: null, confidence: null, note: null };
  });
}

export default function AgentStatusPanel({ incident, feed }: Props) {
  const states = useMemo(
    () => deriveAgentStates(incident, feed),
    [incident, feed],
  );

  return (
    <section className="flex h-full flex-col rounded-lg border border-relay-border bg-relay-panel">
      <SectionHeader
        title="Agent activity"
        sub={incident ? incident.incident_id : "no incident selected"}
      />
      {states.every((a) => a.status === "IDLE") && !incident ? (
        <div className="flex-1">
          <EmptyState
            title="Agents standing by"
            hint="Select or create an incident — each agent lights up here the moment it starts real work."
          />
        </div>
      ) : (
      <ul className="flex-1 space-y-1.5 overflow-y-auto p-3">
        {states.map((a) => {
          const s = statusStyle(a.status);
          /* One-shot flash when real backend data moves the agent's
             status — keyed on status+timestamp, settles, never loops. */
          const sig = `${a.status}|${a.ts ?? ""}`;
          return (
            <li
              key={a.key}
              className="flex items-center gap-2.5 rounded-md border border-relay-border bg-relay-panel2 px-3 py-2"
            >
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${s.dot}`}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-semibold text-slate-800">
                  {a.label}
                  <span className="ml-1.5 font-mono text-[10px] font-normal text-slate-700">
                    {agentShortName(a.key)}
                  </span>
                </div>
                <div className="font-mono text-[10px] tabular-nums text-slate-500">
                  {a.ts ? formatClock(a.ts) : "—"}
                  {a.note && a.status !== "RUNNING" ? ` · ${a.note}` : ""}
                </div>
              </div>
              <span
                key={sig}
                className={`stage-flash shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold tracking-wide ${s.pill}`}
              >
                {s.label}
              </span>
            </li>
          );
        })}
      </ul>
      )}
      <p className="border-t border-relay-border px-4 py-2 text-[10px] text-slate-700">
        Statuses derive from agent outputs and live WebSocket events.
      </p>
    </section>
  );
}
