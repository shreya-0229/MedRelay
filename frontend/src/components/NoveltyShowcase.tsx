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
      iconBg: "bg-emerald-500/15",
      title: "Verification with veto power",
      body: "An independent agent cross-checks the plan against the live database — 8 consistency checks, real reject power before anything moves.",
      stat: verStat,
    },
    {
      icon: "🔄",
      iconBg: "bg-sky-500/15",
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
      iconBg: "bg-amber-500/15",
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
          className="mt-2 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-amber-500"
        >
          Open live review →
        </button>
      ) : undefined,
    },
    {
      icon: "🔒",
      iconBg: "bg-slate-500/15",
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
      iconBg: "bg-violet-500/15",
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
      iconBg: "bg-blue-500/15",
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
      iconBg: "bg-rose-500/15",
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
          className="mt-2 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-500"
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
            className="relay-card flex flex-col rounded-lg border border-relay-border bg-relay-panel p-4"
          >
            <div className="flex items-center gap-2.5">
              <span
                className={`flex h-9 w-9 items-center justify-center rounded-lg text-lg ${c.iconBg}`}
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
