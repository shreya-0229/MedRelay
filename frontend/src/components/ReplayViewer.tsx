import { useEffect, useState } from "react";
import { api } from "../api";
import type { AuditEvent } from "../types";
import { formatClock, humanizeEvent, pct } from "./badges";

/**
 * Incident replay — step through the incident's REAL audit events in
 * chronological order. The event-sourced trail is the durable record;
 * this viewer just walks it.
 */
export default function ReplayViewer({ incidentId }: { incidentId: string }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    setEvents(null);
    setIdx(0);
    api
      .getAudit(incidentId)
      .then((r) => setEvents(r.events ?? []))
      .catch(() => setEvents([]));
  }, [incidentId]);

  if (events === null) {
    return <p className="text-xs text-slate-500">Loading audit trail…</p>;
  }
  if (events.length === 0) {
    return <p className="text-xs text-slate-500">No audit events yet.</p>;
  }

  const ev = events[Math.min(idx, events.length - 1)];
  const progress = ((idx + 1) / events.length) * 100;

  return (
    <div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setIdx((i) => Math.max(0, i - 1))}
          disabled={idx === 0}
          className="rounded-md border border-relay-border bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-relay-panel2 disabled:opacity-40"
        >
          ← Prev
        </button>
        <div className="h-1.5 flex-1 overflow-hidden rounded bg-relay-panel2">
          <div
            className="h-full rounded bg-blue-600 transition-all"
            style={{ width: `${progress}%` }}
          />
        </div>
        <button
          type="button"
          onClick={() => setIdx((i) => Math.min(events.length - 1, i + 1))}
          disabled={idx >= events.length - 1}
          className="rounded-md border border-relay-border bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-relay-panel2 disabled:opacity-40"
        >
          Next →
        </button>
        <span className="font-mono text-[11px] tabular-nums text-slate-500">
          {idx + 1}/{events.length}
        </span>
      </div>

      <div
        key={ev.id}
        className="relay-fade-in mt-3 rounded-lg border border-relay-border bg-white p-4"
      >
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span className="text-sm font-bold text-slate-900">
            {humanizeEvent(ev.action)}
          </span>
          <span className="rounded border border-relay-border bg-relay-panel2 px-1.5 py-0.5 text-[11px] font-medium text-slate-600">
            {ev.agent}
          </span>
          <span className="ml-auto font-mono text-[11px] tabular-nums text-slate-500">
            {formatClock(ev.ts)} · confidence {pct(ev.confidence)}
          </span>
        </div>
        {ev.rationale && (
          <p className="mt-2 text-sm leading-relaxed text-slate-600">
            {ev.rationale}
          </p>
        )}
      </div>

      <ol className="mt-3 flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
        {events.map((e, i) => (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => setIdx(i)}
              title={`${humanizeEvent(e.action)} — ${e.agent}`}
              className={`flex h-7 w-7 items-center justify-center rounded-md border font-mono text-[10px] tabular-nums transition-colors ${
                i === idx
                  ? "border-blue-600 bg-blue-600 text-white"
                  : i < idx
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-700"
                    : "border-relay-border bg-white text-slate-500 hover:border-slate-400"
              }`}
            >
              {i + 1}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
