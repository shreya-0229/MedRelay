import { useState } from "react";
import { api } from "../api";
import type { IncidentState, ReviewDecision } from "../types";
import { pct } from "./badges";

interface Props {
  incident: IncidentState | null;
  onChanged: (inc: IncidentState) => void;
}

function hasTimelineEvent(incident: IncidentState, event: string): boolean {
  return (incident.timeline ?? []).some((t) => t.event === event);
}

/** Structured WHY for the operator, derived from real backend state. */
function whyReasons(incident: IncidentState): string[] {
  const reasons: string[] = [];
  const triageConf = incident.agent_outputs?.["TriageAgent"]?.confidence;
  if (
    (triageConf != null && triageConf < 0.6) ||
    /confidence/i.test(incident.review?.reason ?? "")
  ) {
    reasons.push(
      `Low triage confidence${triageConf != null ? ` (${pct(triageConf)})` : ""} — below the autonomous-action threshold`,
    );
  }
  if (hasTimelineEvent(incident, "conflict_detected")) {
    reasons.push("Agent conflict detected — agents disagree on the plan");
  }
  if (incident.verification_results && !incident.verification_results.passed) {
    reasons.push("Verification failed — the dispatch plan did not validate");
  }
  if (hasTimelineEvent(incident, "failure_detected")) {
    reasons.push("Resource failure — a reserved ambulance or hospital failed");
  }
  if (reasons.length === 0) {
    reasons.push(incident.review?.reason || "Operator decision required");
  }
  return reasons;
}

function ReviewButton({
  label,
  disabled,
  busy,
  onClick,
  className,
}: {
  label: string;
  disabled: boolean;
  busy: boolean;
  onClick: () => void;
  className: string;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-md border px-3 py-2 text-xs font-semibold transition-colors disabled:opacity-50 ${className}`}
    >
      {busy ? "Working…" : label}
    </button>
  );
}

/**
 * Human Operator Panel — rendered ONLY when the selected incident needs a
 * human (human_review_required or escalated). WHY comes from the review
 * dossier, conflict/verification/failure state — never hardcoded.
 * The four buttons call the real review-decision API.
 */
export default function OperatorPanel({ incident, onChanged }: Props) {
  const [reviewNote, setReviewNote] = useState("");
  const [deciding, setDeciding] = useState<ReviewDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!incident) return null;
  const needsHuman =
    incident.current_status === "human_review_required" ||
    incident.escalation_status === "escalated";
  if (!needsHuman) return null;

  const canDecide =
    incident.current_status === "human_review_required" &&
    incident.review?.status === "pending";
  const reasons = whyReasons(incident);

  const handleDecision = async (decision: ReviewDecision) => {
    if (deciding) return;
    setDeciding(decision);
    setError(null);
    try {
      const updated = await api.decideReview(
        incident.incident_id,
        decision,
        reviewNote,
      );
      setReviewNote("");
      onChanged(updated);
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : `Review decision '${decision}' failed`,
      );
    } finally {
      setDeciding(null);
    }
  };

  return (
    <section className="rounded-lg border border-amber-500/60 bg-amber-500/5">
      <div className="border-b border-amber-500/30 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="relay-pulse inline-block h-2.5 w-2.5 rounded-full bg-amber-400" />
          <h2 className="text-xs font-bold uppercase tracking-wider text-amber-700">
            Human operator required
          </h2>
          <span className="ml-auto rounded border border-amber-500/40 px-1.5 py-0.5 font-mono text-[11px] text-amber-700">
            {incident.incident_id}
          </span>
        </div>
      </div>
      <div className="space-y-2.5 p-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-600">
            Why human review is required
          </div>
          <ul className="mt-1.5 space-y-1">
            {reasons.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-slate-800">
                <span className="mt-0.5 text-amber-700">▸</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="grid grid-cols-2 gap-2 text-[11px]">
          <div className="rounded border border-relay-border bg-relay-panel2 p-2">
            <div className="text-slate-500">Confidence</div>
            <div className="font-mono text-sm text-slate-900">
              {pct(incident.review?.confidence ?? incident.confidence)}
            </div>
          </div>
          <div className="rounded border border-relay-border bg-relay-panel2 p-2">
            <div className="text-slate-500">Affected decision</div>
            <div className="truncate font-mono text-[11px] text-slate-900">
              {incident.review?.affected_decision || "—"}
            </div>
          </div>
        </div>
        {incident.review?.status === "info_requested" && (
          <p className="text-xs text-amber-700">
            Awaiting caller callback — more information requested.
          </p>
        )}
        {error && (
          <p className="rounded border border-red-500/50 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-700">
            {error}
          </p>
        )}
        {canDecide ? (
          <>
            <input
              type="text"
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
              placeholder="Operator note (recorded in the audit trail)…"
              className="w-full rounded-md border border-relay-border bg-relay-bg px-3 py-1.5 text-sm text-slate-800 placeholder:text-slate-700 focus:border-amber-500/60 focus:outline-none"
            />
            <div className="grid grid-cols-2 gap-2">
              <ReviewButton
                label="APPROVE"
                disabled={deciding !== null}
                busy={deciding === "approve"}
                onClick={() => handleDecision("approve")}
                className="border-emerald-500/50 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/20"
              />
              <ReviewButton
                label="REJECT"
                disabled={deciding !== null}
                busy={deciding === "reject"}
                onClick={() => handleDecision("reject")}
                className="border-red-500/50 bg-red-500/10 text-red-700 hover:bg-red-500/20"
              />
              <ReviewButton
                label="REPLAN"
                disabled={deciding !== null}
                busy={deciding === "replan"}
                onClick={() => handleDecision("replan")}
                className="border-amber-500/50 bg-amber-500/10 text-amber-700 hover:bg-amber-500/20"
              />
              <ReviewButton
                label="REQUEST INFORMATION"
                disabled={deciding !== null}
                busy={deciding === "request_info"}
                onClick={() => handleDecision("request_info")}
                className="border-sky-500/50 bg-sky-500/10 text-sky-700 hover:bg-sky-500/20"
              />
            </div>
          </>
        ) : (
          <p className="text-[11px] text-slate-500">
            {incident.escalation_status === "escalated"
              ? "Escalated — a human dispatcher has been notified. Decisions are handled outside the console."
              : "Review already decided — see the audit trail."}
          </p>
        )}
      </div>
    </section>
  );
}
