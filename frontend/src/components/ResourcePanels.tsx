import type { Fleet, IncidentState } from "../types";
import SectionHeader from "./SectionHeader";
import { SkeletonRows } from "./Skeleton";

interface Props {
  fleet: Fleet | null;
  incidents: IncidentState[];
}

function ambStatusClass(status: string): string {
  const s = status.toLowerCase();
  const base =
    "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium";
  if (s === "available")
    return `${base} border-emerald-500/50 bg-emerald-500/10 text-emerald-700`;
  if (s === "en_route")
    return `${base} border-sky-500/50 bg-sky-500/10 text-sky-700`;
  if (s === "out_of_service")
    return `${base} border-red-500/50 bg-red-500/10 text-red-700`;
  return `${base} border-amber-500/50 bg-amber-500/10 text-amber-700`;
}

/**
 * Fleet table — every cell from GET /api/fleet, kept fresh by WS
 * fleet_update frames. ETA joins the selected incident's real ETA
 * when that unit is en route to it.
 */
export function AmbulancePanel({ fleet, incidents }: Props) {
  const etaByAmb = new Map<string, number>();
  for (const inc of incidents) {
    const a = inc.selected_ambulance;
    if (a && inc.ambulance_status === "en_route") etaByAmb.set(a.id, a.eta_min);
  }
  const rows = fleet?.ambulances ?? [];

  return (
    <section className="rounded-lg border border-relay-border bg-relay-panel">
      <SectionHeader
        title="Ambulances"
        sub={`${rows.length} units · live fleet state`}
      />
      {fleet === null ? (
        <div className="p-3">
          <SkeletonRows rows={3} />
        </div>
      ) : (
      <div className="max-h-56 overflow-y-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-relay-panel">
            <tr className="border-b border-relay-border text-[10px] uppercase tracking-wider text-slate-500">
              <th className="px-3 py-1.5 font-medium">ID</th>
              <th className="px-2 py-1.5 font-medium">Status</th>
              <th className="px-2 py-1.5 font-medium">ETA</th>
              <th className="px-2 py-1.5 font-medium">Capabilities</th>
              <th className="px-2 py-1.5 font-medium">Location</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr
                key={a.id}
                className="border-b border-relay-border/50 last:border-0"
              >
                <td className="px-3 py-1.5 font-mono font-semibold text-slate-800">
                  {a.id}
                </td>
                <td className="px-2 py-1.5">
                  <span className={ambStatusClass(a.status)}>{a.status}</span>
                </td>
                <td className="px-2 py-1.5 font-mono text-slate-700">
                  {etaByAmb.has(a.id) ? (
                    `${etaByAmb.get(a.id)!.toFixed(1)} min`
                  ) : (
                    <span className="text-slate-700">—</span>
                  )}
                </td>
                <td className="px-2 py-1.5 text-slate-700">{a.capability}</td>
                <td className="px-2 py-1.5 font-mono text-[10px] tabular-nums text-slate-500">
                  {a.lat.toFixed(3)}, {a.lon.toFixed(3)}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center text-slate-500">
                  No fleet data.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}
    </section>
  );
}

function hasTrauma(specialties: string[]): boolean {
  return specialties.some((s) => s.includes("trauma"));
}

/**
 * Hospital panel — real capacity from the fleet snapshot.
 * ICU has no backend feed in the prototype, so it renders "n/a"
 * instead of invented numbers.
 */
export function HospitalPanel({ fleet }: { fleet: Fleet | null }) {
  const rows = fleet?.hospitals ?? [];

  return (
    <section className="rounded-lg border border-relay-border bg-relay-panel">
      <SectionHeader
        title="Hospitals"
        sub={`${rows.length} facilities · live bed counts`}
      />
      {fleet === null ? (
        <div className="p-3">
          <SkeletonRows rows={2} />
        </div>
      ) : (
      <div className="max-h-56 space-y-2 overflow-y-auto p-3">
        {rows.map((h) => {
          const ratio = h.total_beds > 0 ? h.free_beds / h.total_beds : 0;
          const status =
            h.free_beds <= 0 ? (
              <span className="inline-flex items-center rounded border border-red-500/50 bg-red-500/10 px-1.5 py-0.5 text-[11px] font-medium text-red-700">
                FULL
              </span>
            ) : ratio < 0.15 ? (
              <span className="inline-flex items-center rounded border border-amber-500/50 bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
                LOW
              </span>
            ) : (
              <span className="inline-flex items-center rounded border border-emerald-500/50 bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">
                AVAILABLE
              </span>
            );
          return (
            <div
              key={h.id}
              className="rounded-md border border-relay-border bg-relay-panel2 p-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-slate-800">
                  {h.name}
                </span>
                {status}
              </div>
              <div className="mt-1.5 grid grid-cols-3 gap-2 text-[11px]">
                <div>
                  <div className="text-slate-500">Emergency cap.</div>
                  <div className="font-mono text-slate-800">
                    {h.free_beds}/{h.total_beds}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">ICU</div>
                  <div
                    className="text-slate-700"
                    title="No ICU feed in the prototype"
                  >
                    n/a
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Trauma</div>
                  <div
                    className={
                      hasTrauma(h.specialties)
                        ? "font-medium text-emerald-700"
                        : "text-slate-700"
                    }
                  >
                    {hasTrauma(h.specialties) ? "yes" : "no"}
                  </div>
                </div>
              </div>
              <div className="mt-1 truncate text-[10px] text-slate-700">
                {h.specialties.join(" · ")}
              </div>
            </div>
          );
        })}
        {rows.length === 0 && (
          <p className="py-4 text-center text-xs text-slate-500">
            No hospital data.
          </p>
        )}
      </div>
      )}
    </section>
  );
}
