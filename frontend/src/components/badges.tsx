/* Shared badge + formatting helpers for the command-center theme. */

/**
 * Refined severity palette, consistent with the analytics donut:
 * CRITICAL(5) red · HIGH(4) orange · MODERATE(3) amber · LOW(2) green.
 */
export function severityBadgeClass(severity: number | null): string {
  const base =
    "inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-bold border tracking-wide";
  if (severity == null)
    return `${base} border-relay-border text-slate-600 bg-slate-600/5`;
  if (severity >= 5)
    return `${base} border-red-500/50 bg-red-500/15 text-red-600 shadow-[0_0_8px_rgba(239,68,68,0.25)]`;
  if (severity === 4)
    return `${base} border-orange-500/50 bg-orange-500/15 text-orange-600`;
  if (severity === 3)
    return `${base} border-amber-500/50 bg-amber-500/15 text-amber-700`;
  return `${base} border-emerald-500/50 bg-emerald-500/10 text-emerald-700`;
}

export function severityLabel(severity: number | null): string {
  return severity == null ? "S—" : `S${severity}`;
}

const TERMINAL = new Set([
  "completed",
  "resolved",
  "closed",
  "cancelled",
  "hospital_ready",
]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL.has(status.trim().toLowerCase());
}

/** Human-friendly label for raw current_status values. */
export function statusLabel(status: string): string {
  const s = status.trim().toLowerCase().replace(/_/g, " ");
  return s.replace(/\b\w/g, (c) => c.toUpperCase());
}

export function statusBadgeClass(status: string): string {
  const base =
    "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium border";
  const s = status.trim().toLowerCase();
  if (TERMINAL.has(s))
    return `${base} border-emerald-500/50 bg-emerald-500/10 text-emerald-600`;
  if (s === "failed" || s === "error")
    return `${base} border-red-500/50 bg-red-500/15 text-red-600`;
  return `${base} border-sky-500/40 bg-sky-500/10 text-sky-700`;
}

export function escalationBadge(escalationStatus: string): {
  text: string;
  className: string;
} | null {
  const s = (escalationStatus ?? "").trim().toLowerCase();
  if (s === "escalated")
    return {
      text: "ESCALATED",
      className:
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold border border-red-500/60 bg-red-500/15 text-red-600",
    };
  if (s === "replanning")
    return {
      text: "REPLANNING",
      className:
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold border border-amber-500/60 bg-amber-500/15 text-amber-700",
    };
  return null;
}

export function formatClock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDateTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return (
    d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) +
    " " +
    d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
  );
}

export function pct(confidence: number | null | undefined): string {
  if (confidence == null || Number.isNaN(confidence)) return "—";
  const v = confidence > 1 ? confidence : confidence * 100;
  return `${v.toFixed(0)}%`;
}

/** A colored dot per agent for the live feed. */
export const AGENT_DOT: Record<string, string> = {
  IntakeAgent: "bg-sky-400",
  TriageAgent: "bg-amber-400",
  DispatchAgent: "bg-violet-400",
  HospitalLiaisonAgent: "bg-cyan-400",
  VerificationAgent: "bg-emerald-400",
  CommunicationAgent: "bg-slate-700",
};

export function agentShortName(name: string): string {
  return name.endsWith("Agent") ? name.slice(0, -"Agent".length) : name;
}

/** "failure_detected" -> "Failure detected" for timeline/event labels. */
export function humanizeEvent(event: string): string {
  const s = (event ?? "").replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—";
}
