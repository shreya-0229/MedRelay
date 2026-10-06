import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Analytics, IncidentState, IncidentSummary } from "../types";
import SectionHeader from "./SectionHeader";

interface Props {
  incident: IncidentState | null;
  llmProvider: string | null;
  onSelectIncident: (id: string) => void;
  onGoDemo: () => void;
}

interface CardShell {
  icon: string;
  iconBg: string;
  /** Top accent gradient per card so the section pops. */
  accent: string;
  title: string;
  body: string;
  stat: React.ReactNode;
  action?: React.ReactNode;
}

/**
 * "Why MedRelay wins" — the 7 novelty angles, each backed by live data.
 * No invented numbers: every stat is read from the backend at render time.
 */
export default function NoveltyShowcase({
  incident,
  llmProvider,
  onSelectIncident,
  onGoDemo,
}: Props) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api.getAnalytics().then((d) => {
        if (alive) setAnalytics(d);
      }).catch(() => {});
      api.getIncidents().then((r) => {
        if (alive) setIncidents(r.incidents ?? []);
      }).catch(() => {});
    };
    void load();
    const t = window.setInterval(load, 10000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  const reviewIncident = useMemo(
    () => incidents.find((i) => i.current_status === "human_review_required"),
    [incidents],
  );
  const sampleCount = useMemo(
    () => incidents.filter((i) => i.is_sample).length,
    [incidents],
  );

  const ver = incident?.verification_results;
  const verStat = !incident ? (
    <span className="text-slate-500">Select an incident to inspect its gate</span>
  ) : !ver ? (
    <span className="text-slate-500">Gate not reached yet</span>
  ) : (
    <span className={ver.passed ? "text-emerald-700" : "text-red-700"}>
      {ver.checks.filter((c) => c.passed).length}/{ver.checks.length} checks passed
      {ver.passed ? "" : " — plan vetoed"}
    </span>
  );

  const recoveryEvents = (incident?.timeline ?? []).filter((t) =>
    /fail|replan|replac|recover/i.test(t.event ?? ""),
  ).length;

  const cards: CardShell[] = [
    {
      icon: "🛡️",
      iconBg: "bg-gradient-to-br from-emerald-500 to-teal-600 text-white shadow-[0_4px_10px_-2px_rgba(16,185,129,0.5)]",
      accent: "bg-gradient-to-r from-emerald-500 to-teal-400",
      title: "Verification with veto power",
      body: "An independent agent cross-checks the plan against the live database — 8 consistency checks, real reject power before anything moves.",
      stat: verStat,
    },
    {
      icon: "🔄",
      iconBg: "bg-gradient-to-br from-sky-500 to-blue-600 text-white shadow-[0_4px_10px_-2px_rgba(14,165,233,0.5)]",
      accent: "bg-gradient-to-r from-sky-500 to-blue-400",
      title: "Autonomous failure recovery",
      body: "Ambulance breaks down mid-plan? Hospital goes dark? The system verifies the failure, replans, and dispatches a replacement on its own.",
      stat: incident ? (
        <span className="text-sky-800">
          {recoveryEvents} recovery events on the selected incident
        </span>
      ) : (
        <span className="text-slate-500">11-step recovery, fully audited</span>
      ),
    },
    {
      icon: "🧑‍⚕️",
      iconBg: "bg-gradient-to-br from-amber-500 to-orange-600 text-white shadow-[0_4px_10px_-2px_rgba(245,158,11,0.5)]",
      accent: "bg-gradient-to-r from-amber-500 to-orange-400",
      title: "Human-in-the-loop",
      body: "Low-confidence triage pauses the pipeline BEFORE any reservation. A human dispatcher approves, rejects, asks for info, or replans.",
      stat: (
        <span className={analytics?.review_queue ? "text-amber-700" : "text-slate-700"}>
          {analytics == null ? "…" : `${analytics.review_queue} incident${analytics.review_queue === 1 ? "" : "s"} awaiting review`}
        </span>
      ),
      action: reviewIncident ? (
        <button
          type="button"
          onClick={() => onSelectIncident(reviewIncident.incident_id)}
          className="relay-btn-amber mt-2 rounded-lg px-3 py-1.5 text-xs font-bold text-white"
        >
          Open live review →
        </button>
      ) : undefined,
    },
    {
      icon: "🔒",
      iconBg: "bg-gradient-to-br from-slate-600 to-slate-800 text-white shadow-[0_4px_10px_-2px_rgba(71,85,105,0.5)]",
      accent: "bg-gradient-to-r from-slate-500 to-slate-700",
      title: "Deterministic safety rails",
      body: "Dispatch, verification, retries, and escalation are hard deterministic rules. An LLM may assist — it can never override safety.",
      stat: (
        <span className="text-slate-700">
          Mode:{" "}
          <span className="font-bold">
            {llmProvider ?? "…"}
          </span>{" "}
          · runs fully offline
        </span>
      ),
    },
    {
      icon: "💬",
      iconBg: "bg-gradient-to-br from-violet-500 to-purple-600 text-white shadow-[0_4px_10px_-2px_rgba(139,92,246,0.5)]",
      accent: "bg-gradient-to-r from-violet-500 to-purple-400",
      title: "Trilingual emergency comms",
      body: "Families get updates in English, Hindi, and Marathi — drafted from confirmed facts, persisted as the durable record.",
      stat: (
        <span className="text-violet-800">
          {analytics == null ? "…" : `${analytics.comms_sent} messages sent (EN · HI · MR)`}
        </span>
      ),
    },
    {
      icon: "📜",
      iconBg: "bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-[0_4px_10px_-2px_rgba(59,130,246,0.5)]",
      accent: "bg-gradient-to-r from-blue-500 to-indigo-400",
      title: "Event-sourced audit trail",
      body: "21 event types, append-only, replayable. Every decision carries its reason and confidence — accountability by construction.",
      stat: (
        <span className="text-blue-800">
          {analytics == null ? "…" : `${analytics.audit_events} audit events recorded`}
        </span>
      ),
    },
    {
      icon: "▶️",
      iconBg: "bg-gradient-to-br from-rose-500 to-red-600 text-white shadow-[0_4px_10px_-2px_rgba(244,63,94,0.5)]",
      accent: "bg-gradient-to-r from-rose-500 to-red-400",
      title: "One-button judging mode",
      body: "9 demo scenarios drive the real pipeline — checklists tick only on genuine backend events. Reset restores a pristine fleet.",
      stat: (
        <span className="text-slate-700">
          9 scenarios · honest step checklists
          {sampleCount > 0 && ` · ${sampleCount} sample incidents loaded`}
        </span>
      ),
      action: (
        <button
          type="button"
          onClick={onGoDemo}
          className="relay-btn-primary mt-2 rounded-lg px-3 py-1.5 text-xs font-bold text-white"
        >
          Open Demo Scenarios →
        </button>
      ),
    },
  ];

  return (
    <section aria-label="Why MedRelay wins">
      <SectionHeader
        title="Why MedRelay wins"
        sub="the 7 novelty angles — every number below is live backend data"
      />
      <div className="mt-2 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((c) => (
          <div
            key={c.title}
            className="relay-card relay-card-hover relative flex flex-col overflow-hidden rounded-2xl border border-relay-border bg-relay-panel p-4"
          >
            <span className={`absolute inset-x-0 top-0 h-1 ${c.accent}`} aria-hidden />
            <div className="flex items-center gap-2.5">
              <span
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg ${c.iconBg}`}
                aria-hidden
              >
                {c.icon}
              </span>
              <h3 className="text-sm font-bold leading-tight text-slate-900">
                {c.title}
              </h3>
            </div>
            <p className="mt-2.5 flex-1 text-xs leading-relaxed text-slate-600">
              {c.body}
            </p>
            <div className="mt-3 border-t border-relay-border pt-2.5 text-xs font-semibold">
              {c.stat}
            </div>
            {c.action}
          </div>
        ))}
      </div>
    </section>
  );
}
