import type { IncidentState, IncidentSummary } from "../types";
import { severityName, typeLabel } from "../types";
import {
  escalationBadge,
  pct,
  severityBadgeClass,
  statusBadgeClass,
  statusLabel,
} from "./badges";
import SectionHeader from "./SectionHeader";
import EmptyState from "./EmptyState";
import { SkeletonRows } from "./Skeleton";

interface Props {
  summaries: IncidentSummary[];
  details: Record<string, IncidentState>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** True while the first incident list is still loading. */
  loading: boolean;
}

function shortAddr(addr: string): string {
  if (!addr) return "—";
  return addr.length > 26 ? `${addr.slice(0, 26)}…` : addr;
}

/**
 * Live Incident Overview — every cell comes from real backend state:
 * summaries from GET /api/incidents (+ WS incident_list), full states
 * from GET /api/incidents/{id} refreshed by WS incident_update frames.
 */
export default function OverviewTable({
  summaries,
  details,
  selectedId,
  onSelect,
  loading,
}: Props) {
  const rows = summaries.slice(0, 25);

  return (
    <section className="rounded-lg border border-relay-border bg-relay-panel">
      <SectionHeader
        title="Live incident overview"
        sub={
          loading
            ? "loading…"
            : `${rows.length} incident${rows.length === 1 ? "" : "s"} · click a row to inspect`
        }
      />
      {loading ? (
        <div className="p-4">
          <SkeletonRows rows={4} />
        </div>
      ) : (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[880px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-relay-border text-[11px] uppercase tracking-wider text-slate-500">
              <th className="px-4 py-2 font-medium">Incident ID</th>
              <th className="px-3 py-2 font-medium">Severity</th>
              <th className="px-3 py-2 font-medium">Location</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Ambulance</th>
              <th className="px-3 py-2 font-medium">Hospital</th>
              <th className="px-3 py-2 font-medium">ETA</th>
              <th className="px-3 py-2 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <EmptyState
                    title="No incidents yet"
                    hint="Create one to watch the six agents coordinate a live emergency response."
                  />
                </td>
              </tr>
            )}
            {rows.map((s) => {
              const d = details[s.incident_id];
              const selected = s.incident_id === selectedId;
              const esc = escalationBadge(s.escalation_status);
              return (
                <tr
                  key={s.incident_id}
                  onClick={() => onSelect(s.incident_id)}
                  className={`cursor-pointer border-b border-relay-border/50 transition-colors last:border-0 hover:bg-relay-panel2 ${
                    selected ? "bg-relay-panel2" : ""
                  }`}
                >
                  <td className="px-4 py-2.5">
                    <div className="font-mono text-xs text-slate-200">
                      {s.incident_id}
                    </div>
                    <div className="text-[11px] text-slate-500">
                      {typeLabel(s.incident_type)}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={severityBadgeClass(s.severity)}>
                      {severityName(s.severity)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-300">
                    {shortAddr(d?.location.address ?? "")}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="flex flex-wrap items-center gap-1">
                      <span className={statusBadgeClass(s.current_status)}>
                        {statusLabel(s.current_status)}
                      </span>
                      {esc && (
                        <span className={esc.className}>{esc.text}</span>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-200">
                    {s.ambulance_id ?? (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-300">
                    {s.hospital_name ? (
                      shortAddr(s.hospital_name)
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-slate-200">
                    {d?.selected_ambulance ? (
                      `${d.selected_ambulance.eta_min.toFixed(1)} min`
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-slate-200">
                    {d ? (
                      pct(d.confidence)
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      )}
    </section>
  );
}
