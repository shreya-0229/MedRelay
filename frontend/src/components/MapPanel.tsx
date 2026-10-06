import { Suspense, lazy, useMemo, useState } from "react";
import type { Fleet, IncidentState } from "../types";
import SectionHeader from "./SectionHeader";
import EmptyState from "./EmptyState";

const LeafletMap = lazy(() => import("./LeafletMap"));

interface Props {
  incidents: IncidentState[];
  fleet: Fleet | null;
  selectedId: string | null;
}

interface Pt {
  lat: number;
  lon: number;
  kind: "incident" | "ambulance" | "hospital";
  label: string;
  color: string;
}

/**
 * Clean coordinate-grid fallback: used when map tiles cannot load
 * (offline / blocked tile server). Never blocks the dashboard.
 */
function CoordinateGrid({ incidents, fleet, selectedId }: Props) {
  const { pts, route } = useMemo(() => {
    const pts: Pt[] = [];
    for (const i of incidents)
      pts.push({
        lat: i.location.lat,
        lon: i.location.lon,
        kind: "incident",
        label: i.incident_id,
        color: "#f87171",
      });
    for (const a of fleet?.ambulances ?? [])
      pts.push({
        lat: a.lat,
        lon: a.lon,
        kind: "ambulance",
        label: a.id,
        color: "#38bdf8",
      });
    for (const h of fleet?.hospitals ?? [])
      pts.push({
        lat: h.lat,
        lon: h.lon,
        kind: "hospital",
        label: h.name,
        color: "#2dd4bf",
      });
    const route: Array<[number, number]> = [];
    const sel = incidents.find((i) => i.incident_id === selectedId);
    if (sel && fleet) {
      const amb = fleet.ambulances.find(
        (a) => a.id === sel.selected_ambulance?.id,
      );
      const hosp = fleet.hospitals.find(
        (h) => h.id === sel.selected_hospital?.id,
      );
      if (amb) route.push([amb.lat, amb.lon]);
      route.push([sel.location.lat, sel.location.lon]);
      if (hosp) route.push([hosp.lat, hosp.lon]);
    }
    return { pts, route };
  }, [incidents, fleet, selectedId]);

  const W = 600;
  const H = 380;
  const PAD = 36;
  const lats = pts.map((p) => p.lat);
  const lons = pts.map((p) => p.lon);
  const minLat = Math.min(...lats, 18.45) - 0.02;
  const maxLat = Math.max(...lats, 18.6) + 0.02;
  const minLon = Math.min(...lons, 73.78) - 0.02;
  const maxLon = Math.max(...lons, 73.95) + 0.02;
  const X = (lon: number) =>
    PAD + ((lon - minLon) / (maxLon - minLon)) * (W - 2 * PAD);
  const Y = (lat: number) =>
    PAD + ((maxLat - lat) / (maxLat - minLat)) * (H - 2 * PAD);

  const gridLines = [];
  for (let g = 0; g <= 6; g += 1) {
    const x = PAD + ((W - 2 * PAD) * g) / 6;
    const y = PAD + ((H - 2 * PAD) * g) / 6;
    gridLines.push(
      <line key={`v${g}`} x1={x} y1={PAD} x2={x} y2={H - PAD} stroke="#e2e8f0" />,
      <line key={`h${g}`} x1={PAD} y1={y} x2={W - PAD} y2={y} stroke="#e2e8f0" />,
    );
  }

  return (
    <div className="absolute inset-0">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full">
        <rect x={0} y={0} width={W} height={H} fill="#f8fafc" />
        {gridLines}
        <text x={PAD} y={H - 12} fontSize="9" fill="#94a3b8" fontFamily="monospace">
          {minLon.toFixed(2)}°E
        </text>
        <text
          x={W - PAD}
          y={H - 12}
          fontSize="9"
          fill="#94a3b8"
          fontFamily="monospace"
          textAnchor="end"
        >
          {maxLon.toFixed(2)}°E
        </text>
        {route.length >= 2 && (
          <polyline
            points={route.map(([la, lo]) => `${X(lo)},${Y(la)}`).join(" ")}
            fill="none"
            stroke="#0284c7"
            strokeWidth={1.5}
            strokeDasharray="5 4"
          />
        )}
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={X(p.lon)} cy={Y(p.lat)} r={5} fill={p.color} opacity={0.85} />
            <text x={X(p.lon) + 8} y={Y(p.lat) + 3} fontSize="9" fill="#64748b">
              {p.label.length > 18 ? `${p.label.slice(0, 18)}…` : p.label}
            </text>
          </g>
        ))}
      </svg>
      <div className="absolute left-2 top-2 rounded bg-relay-panel/90 px-2 py-1 text-[10px] text-slate-600">
        Offline grid — tiles unavailable
      </div>
    </div>
  );
}

function MapSkeleton() {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-relay-bg">
      <p className="text-xs text-slate-500">Loading map…</p>
    </div>
  );
}

/**
 * Live map panel. Leaflet is lazy-loaded (never blocks initial render);
 * if tiles fail, a clean coordinate grid takes over automatically.
 */
export default function MapPanel(props: Props) {
  const [tilesFailed, setTilesFailed] = useState(false);

  return (
    <section className="flex h-full min-h-[320px] flex-col rounded-lg border border-relay-border bg-relay-panel">
      <SectionHeader
        title="Live map"
        sub={
          tilesFailed
            ? "coordinate grid (tiles unavailable)"
            : "Pune sector · Esri street map"
        }
      />
      <div className="relative flex-1">
        {props.incidents.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <EmptyState
              title="No incidents to plot"
              hint="Create an incident and its location, ambulances, and hospitals will appear here."
            />
          </div>
        ) : (
        <Suspense fallback={<MapSkeleton />}>
          {tilesFailed ? (
            <CoordinateGrid {...props} />
          ) : (
            <LeafletMap {...props} onTilesFailed={() => setTilesFailed(true)} />
          )}
        </Suspense>
        )}
        <div className="absolute bottom-2 left-2 z-[500] flex gap-3 rounded bg-relay-panel/90 px-2.5 py-1.5 text-[10px] text-slate-700">
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-red-400" /> incident
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-sky-400" /> ambulance
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-teal-400" /> hospital
          </span>
        </div>
      </div>
    </section>
  );
}
