import type { IncidentSummary } from "../types";
import { typeLabel } from "../types";
import {
  escalationBadge,
  formatDateTime,
  severityBadgeClass,
  severityLabel,
  statusBadgeClass,
} from "./badges";

interface Props {
  incidents: IncidentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function IncidentList({
  incidents,
  selectedId,
  onSelect,
}: Props) {
  return (
    <section className="flex h-[420px] min-h-0 flex-col rounded-lg border border-relay-border bg-relay-panel xl:h-auto">
      <header className="border-b border-relay-border px-3 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
          Incidents
        </h2>
        <p className="text-[11px] text-slate-500">
          {incidents.length} total · newest first
        </p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {incidents.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-500">
            No incidents yet.
            <br />
            Create one to watch the agents work.
          </div>
        ) : (
          <ul>
            {incidents.map((inc) => {
              const esc = escalationBadge(inc.escalation_status);
              const selected = inc.incident_id === selectedId;
              return (
                <li key={inc.incident_id}>
                  <button
                    type="button"
                    onClick={() => onSelect(inc.incident_id)}
                    className={`block w-full border-b border-relay-border/60 px-3 py-2.5 text-left transition-colors hover:bg-relay-panel2 ${
                      selected ? "bg-relay-panel2" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-100">
                        {typeLabel(inc.incident_type)}
                      </span>
                      <span className={severityBadgeClass(inc.severity)}>
                        {severityLabel(inc.severity)}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <span className={statusBadgeClass(inc.current_status)}>
                        {inc.current_status}
                      </span>
                      {esc && <span className={esc.className}>{esc.text}</span>}
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-slate-500">
                      {inc.incident_id.slice(0, 8)} ·{" "}
                      {formatDateTime(inc.created_at)}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
