import { useMemo } from "react";
import type { IncidentState, TimelineEntry, WsAgentEvent } from "../types";
import { formatClock, isTerminalStatus } from "./badges";

export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StoryStage {
  key: string;
  label: string;
  /** Extra line under the label (e.g. parallel sub-agent states). */
  sub: string | null;
  status: StageStatus;
  /** Amber ring: needs attention (e.g. human review) without being failed. */
  attention: boolean;
  /** Real timestamp of the latest transition, if any. */
  ts: string | null;
}

interface Props {
  incident: IncidentState | null;
  /** Live WS agent events (newest first). */
  feed: WsAgentEvent[];
}

const START_RE = /start|search|beg[ai]n/i;

function findTimeline(
  incident: IncidentState | null,
  name: string,
): TimelineEntry | null {
  return (
    (incident?.timeline ?? []).find((t) => t.event === name) ?? null
  );
}

function agentStage(
  incident: IncidentState | null,
  feed: WsAgentEvent[],
  key: string,
): { status: StageStatus; attention: boolean; ts: string | null } {
  const out = incident?.agent_outputs?.[key];
  if (out) {
    return {
      status: out.success ? "done" : "failed",
      attention: out.requires_human,
      ts: out.timestamp ?? null,
    };
  }
  if (incident) {
    const started = feed.find(
      (e) =>
        e.incident_id === incident.incident_id &&
        e.agent === key &&
        START_RE.test(e.action),
    );
    if (started) return { status: "active", attention: false, ts: started.ts };
  }
  return { status: "pending", attention: false, ts: null };
}

const PARALLEL_KEYS = ["TriageAgent", "DispatchAgent", "HospitalLiaisonAgent"];
const PARALLEL_LABELS: Record<string, string> = {
  TriageAgent: "Triage",
  DispatchAgent: "Dispatch",
  HospitalLiaisonAgent: "Hospital",
};

/** The canonical response story, each stage lit from real backend state. */
export function deriveStory(
  incident: IncidentState | null,
  feed: WsAgentEvent[],
): StoryStage[] {
  const noIncident: StoryStage[] = [
    { key: "report", label: "Report", sub: null, status: "pending", attention: false, ts: null },
    { key: "intake", label: "Intake", sub: null, status: "pending", attention: false, ts: null },
    { key: "parallel", label: "Parallel agents", sub: "Triage · Dispatch · Hospital", status: "pending", attention: false, ts: null },
    { key: "verification", label: "Verification", sub: null, status: "pending", attention: false, ts: null },
    { key: "action", label: "Action", sub: null, status: "pending", attention: false, ts: null },
    { key: "communication", label: "Communication", sub: null, status: "pending", attention: false, ts: null },
    { key: "audit", label: "Audit", sub: null, status: "pending", attention: false, ts: null },
  ];
  if (!incident) return noIncident;

  const intake = agentStage(incident, feed, "IntakeAgent");
  const parallel = PARALLEL_KEYS.map((k) => ({
    key: k,
    ...agentStage(incident, feed, k),
  }));
  const parallelStatus: StageStatus = parallel.some((p) => p.status === "failed")
    ? "failed"
    : parallel.every((p) => p.status === "done")
      ? "done"
      : parallel.some((p) => p.status === "active")
        ? "active"
        : "pending";
  const parallelTs =
    parallel.map((p) => p.ts).filter(Boolean).sort().pop() ?? null;
  const parallelSub = parallel
    .map(
      (p) =>
        `${PARALLEL_LABELS[p.key]} ${p.status === "done" ? "✓" : p.status === "failed" ? "✗" : p.status === "active" ? "…" : "·"}`,
    )
    .join("  ");

  const vr = incident.verification_results;
  const verAgent = agentStage(incident, feed, "VerificationAgent");
  const verification: StoryStage = vr
    ? {
        key: "verification",
        label: "Verification",
        sub: `${vr.checks.filter((c) => c.passed).length}/${vr.checks.length} checks passed`,
        status: vr.passed ? "done" : "failed",
        attention: false,
        ts: verAgent.ts,
      }
    : {
        key: "verification",
        label: "Verification",
        sub: null,
        status: verAgent.status,
        attention: verAgent.attention,
        ts: verAgent.ts,
      };

  const ambOk = incident.ambulance_status === "en_route";
  const hospOk = incident.hospital_status === "reserved";
  const action: StoryStage = {
    key: "action",
    label: "Action",
    sub:
      incident.selected_ambulance || incident.selected_hospital
        ? `${incident.selected_ambulance?.id ?? "—"} → ${incident.selected_hospital?.name ?? "—"}`
        : null,
    status:
      ambOk && hospOk
        ? "done"
        : incident.ambulance_status === "unavailable" ||
            incident.hospital_status === "unavailable"
          ? "failed"
          : verification.status === "done"
            ? "active"
            : "pending",
    attention: false,
    ts: null,
  };

  const comms = incident.communications ?? [];
  const communication: StoryStage = {
    key: "communication",
    label: "Communication",
    sub: comms.length > 0 ? `${comms.length} messages sent` : null,
    status:
      comms.length > 0
        ? "done"
        : action.status === "done"
          ? "active"
          : "pending",
    attention: false,
    ts: comms.length > 0 ? comms[comms.length - 1].ts : null,
  };

  const terminal = isTerminalStatus(incident.current_status);
  const audit: StoryStage = {
    key: "audit",
    label: "Audit",
    sub: `${(incident.timeline ?? []).length} events logged`,
    status: terminal ? "done" : "active",
    attention: false,
    ts: null,
  };

  return [
    {
      key: "report",
      label: "Report",
      sub: incident.incident_type.replace(/_/g, " "),
      status: "done",
      attention: false,
      ts: incident.created_at,
    },
    { key: "intake", label: "Intake", sub: null, ...intake },
    {
      key: "parallel",
      label: "Parallel agents",
      sub: parallelSub,
      status: parallelStatus,
      attention: parallel.some((p) => p.attention),
      ts: parallelTs,
    },
    verification,
    action,
    communication,
    audit,
  ];
}

/** The recovery story, shown when a failure is present on the incident. */
export function deriveRecoveryStory(
  incident: IncidentState | null,
): StoryStage[] | null {
  if (!incident) return null;
  const tl = incident.timeline ?? [];
  const failEv = tl.find(
    (t) => t.event === "failure_detected" || t.event === "conflict_detected",
  );
  if (!failEv) return null;

  const has = (name: string) => tl.some((t) => t.event === name);
  const validated = findTimeline(incident, "failure_validated");
  const replaced = findTimeline(incident, "resource_replaced");
  const replIdx = tl.findIndex((t) => t.event === "resource_replaced");
  const reVerified =
    replIdx >= 0 && tl.slice(replIdx + 1).some((t) => /verif/i.test(t.event));
  const recovered =
    has("recovery_complete") ||
    (isTerminalStatus(incident.current_status) &&
      incident.escalation_status !== "escalated");
  const replanning = incident.escalation_status === "replanning" || has("replanning");

  const stage = (
    key: string,
    label: string,
    status: StageStatus,
    ts: string | null,
    sub: string | null = null,
  ): StoryStage => ({ key, label, sub, status, attention: false, ts });

  return [
    stage("failure", "Failure", "done", failEv.ts, failEv.detail?.slice(0, 48) ?? null),
    stage(
      "rverify1",
      "Verify",
      validated ? "done" : "active",
      validated?.ts ?? null,
      "failure confirmed",
    ),
    stage(
      "replan",
      "Replan",
      replaced || has("replan_succeeded") ? "done" : replanning ? "active" : "pending",
      findTimeline(incident, "replanning")?.ts ?? null,
    ),
    stage(
      "newres",
      "New resource",
      replaced ? "done" : "pending",
      replaced?.ts ?? null,
      replaced?.detail?.slice(0, 48) ?? null,
    ),
    stage(
      "rverify2",
      "Verify",
      reVerified ? "done" : replaced ? "active" : "pending",
      null,
      "replacement checked",
    ),
    stage(
      "continue",
      "Continue",
      recovered ? "done" : "active",
      findTimeline(incident, "recovery_complete")?.ts ?? null,
      recovered ? incident.current_status.replace(/_/g, " ") : "response resumed",
    ),
  ];
}

function stageClasses(status: StageStatus, attention: boolean): {
  ring: string;
  dot: string;
  label: string;
} {
  if (status === "done")
    return {
      ring: "border-emerald-500/50 bg-emerald-500/10",
      dot: "bg-emerald-400",
      label: "text-emerald-700",
    };
  if (status === "failed")
    return {
      ring: "border-red-500/60 bg-red-500/10",
      dot: "bg-red-500",
      label: "text-red-700",
    };
  if (status === "active")
    return attention
      ? {
          ring: "border-amber-500/60 bg-amber-500/10",
          dot: "bg-amber-400 relay-pulse",
          label: "text-amber-700",
        }
      : {
          ring: "border-sky-500/60 bg-sky-500/10",
          dot: "bg-sky-400 relay-pulse",
          label: "text-sky-700",
        };
  return {
    ring: "border-relay-border bg-relay-panel2",
    dot: "bg-slate-700",
    label: "text-slate-500",
  };
}

function StageNode({ stage }: { stage: StoryStage }) {
  const s = stageClasses(stage.status, stage.attention);
  /* keying the inner span on status+ts replays a one-shot highlight
     whenever real backend data moves the stage — never a loop. */
  const sig = `${stage.status}|${stage.ts ?? ""}|${stage.attention}`;
  const glow = stage.status === "active" ? "relay-stage-glow" : "";
  return (
    <div className="flex min-w-0 flex-1 flex-col items-center px-1">
      <span
        key={sig}
        className={`stage-flash flex h-11 w-11 items-center justify-center rounded-full border-2 ${s.ring} ${glow}`}
      >
        <span className={`h-2.5 w-2.5 rounded-full ${s.dot}`} aria-hidden />
      </span>
      <span className={`mt-1.5 text-center text-xs font-bold uppercase tracking-wide ${s.label}`}>
        {stage.label}
      </span>
      {stage.sub && (
        <span className="mt-0.5 max-w-[140px] truncate text-center text-[10px] text-slate-500">
          {stage.sub}
        </span>
      )}
      <span className="mt-0.5 font-mono text-[10px] tabular-nums text-slate-700">
        {stage.ts ? formatClock(stage.ts) : "—"}
      </span>
    </div>
  );
}

function Connector({ done }: { done: boolean }) {
  return (
    <div className="flex h-11 shrink-0 items-center px-0.5" aria-hidden>
      <span className={`relay-connector ${done ? "relay-connector-done" : ""}`} />
      <svg width="10" height="12" viewBox="0 0 10 12" className="-ml-0.5 shrink-0" aria-hidden>
        <path
          d="M1 1 L8 6 L1 11"
          fill="none"
          stroke={done ? "#34d399" : "#3b82f6"}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.8"
        />
      </svg>
    </div>
  );
}

function Strip({
  title,
  titleClass,
  stages,
  hint,
}: {
  title: string;
  titleClass: string;
  stages: StoryStage[];
  hint: string;
}) {
  return (
    <div className="rounded-2xl border border-relay-border bg-relay-panel relay-card px-4 py-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className={`text-xs font-bold uppercase tracking-widest ${titleClass}`}>
          {title}
        </h2>
        <p className="text-[11px] text-slate-700">{hint}</p>
      </div>
      <div className="overflow-x-auto">
        <div className="flex min-w-[760px] items-start justify-between">
          {stages.map((st, i) => (
            <div key={st.key} className="flex flex-1 items-start">
              <StageNode stage={st} />
              {i < stages.length - 1 && <Connector done={st.status === "done"} />}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * The 5-second story: REPORT → INTAKE → PARALLEL AGENTS → VERIFICATION →
 * ACTION → COMMUNICATION → AUDIT, every stage lit from real backend state
 * of the selected incident. When a failure is present, the recovery story
 * (FAILURE → VERIFY → REPLAN → NEW RESOURCE → VERIFY → CONTINUE) appears
 * below it, also from real timeline events.
 */
export default function PipelineStrip({ incident, feed }: Props) {
  const stages = useMemo(() => deriveStory(incident, feed), [incident, feed]);
  const recovery = deriveRecoveryStory(incident);

  return (
    <div className="space-y-3">
      <Strip
        title="Response pipeline"
        titleClass="text-slate-700"
        stages={stages}
        hint={
          incident
            ? `${incident.incident_id} · live`
            : "Create an incident to watch the pipeline run"
        }
      />
      {recovery && (
        <Strip
          title="Failure recovery"
          titleClass="text-amber-700"
          stages={recovery}
          hint="driven by recovery events"
        />
      )}
    </div>
  );
}
