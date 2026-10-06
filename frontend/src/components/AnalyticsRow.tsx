import { useEffect, useState } from "react";
import { api } from "../api";
import type { Analytics } from "../types";
import { pct } from "./badges";
import SectionHeader from "./SectionHeader";

const SEV_COLORS: Record<string, string> = {
  CRITICAL: "#dc2626",
  HIGH: "#f97316",
  MODERATE: "#eab308",
  LOW: "#10b981",
  MINIMAL: "#94a3b8",
  UNKNOWN: "#cbd5e1",
};

const SEV_ORDER = ["CRITICAL", "HIGH", "MODERATE", "LOW", "MINIMAL", "UNKNOWN"];

/** SVG donut of the severity mix — pure presentational SVG, every slice
 * from the real /api/demo/analytics counts. */
function SeverityDonut({ dist }: { dist: Record<string, number> }) {
  const total = Object.values(dist).reduce((a, b) => a + b, 0);
  if (total === 0) {
    return (
      <div className="flex h-36 items-center justify-center text-xs text-slate-500">
        No incidents yet
      </div>
    );
  }
  const R = 52;
  const C = 2 * Math.PI * R;
  let acc = 0;
  const segs = SEV_ORDER.filter((k) => (dist[k] ?? 0) > 0).map((k) => {
    const frac = dist[k] / total;
    const seg = { key: k, color: SEV_COLORS[k], offset: acc * C, len: frac * C };
    acc += frac;
    return seg;
  });
  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 140 140" className="h-36 w-36 shrink-0" role="img" aria-label="Severity distribution">
        <circle cx="70" cy="70" r={R} fill="none" stroke="#eef2f7" strokeWidth="18" />
        {segs.map((s) => (
          <circle
            key={s.key}
            cx="70"
            cy="70"
            r={R}
            fill="none"
            stroke={s.color}
            strokeWidth="18"
            strokeDasharray={`${Math.max(s.len - 2, 1)} ${C - Math.max(s.len - 2, 1)}`}
            strokeDashoffset={-s.offset}
            strokeLinecap="butt"
            transform="rotate(-90 70 70)"
          />
        ))}
        <text x="70" y="66" textAnchor="middle" fontSize="22" fontWeight="bold" fill="#1e293b">
          {total}
        </text>
        <text x="70" y="86" textAnchor="middle" fontSize="10" fill="#64748b">
          incidents
        </text>
      </svg>
      <ul className="space-y-1.5">
        {segs.map((s) => (
          <li key={s.key} className="flex items-center gap-2 text-xs">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: s.color }} />
            <span className="font-semibold text-slate-700">{s.key}</span>
            <span className="font-mono tabular-nums text-slate-500">
              {dist[s.key]}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Tile({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub: string;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-relay-border bg-relay-panel relay-card px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-600">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${accent}`}>
        {value}
      </div>
      <div className="mt-0.5 text-xs text-slate-500">{sub}</div>
    </div>
  );
}

/**
 * Analytics row — every number computed live from the DB by
 * GET /api/demo/analytics. Refreshes every 10s.
 */
export default function AnalyticsRow() {
  const [data, setData] = useState<Analytics | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .getAnalytics()
        .then((d) => {
          if (alive) setData(d);
        })
        .catch(() => {});
    void load();
    const t = window.setInterval(load, 10000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (!data) return null;

  return (
    <section aria-label="Response analytics">
      <SectionHeader
        title="Response analytics"
        sub="computed live from the database"
      />
      <div className="mt-2 grid gap-3 lg:grid-cols-5">
        <div className="rounded-2xl border border-relay-border bg-relay-panel relay-card px-4 py-3 lg:col-span-2">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-600">
            Severity mix
          </div>
          <SeverityDonut dist={data.severity_distribution} />
        </div>
        <div className="grid grid-cols-2 gap-3 lg:col-span-3">
          <Tile
            label="Verification pass rate"
            value={
              data.verification.pass_rate == null
                ? "—"
                : `${pct(data.verification.pass_rate)}`
            }
            sub={`${data.verification.passed}/${data.verification.total} plans passed the gate`}
            accent="text-emerald-600"
          />
          <Tile
            label="Messages sent"
            value={String(data.comms_sent)}
            sub="Family + ER notifications (EN/HI/MR)"
            accent="text-sky-700"
          />
          <Tile
            label="Audit events"
            value={String(data.audit_events)}
            sub="Append-only trail, replayable"
            accent="text-slate-800"
          />
          <Tile
            label="Review queue"
            value={String(data.review_queue)}
            sub="Paused for a human operator"
            accent={data.review_queue > 0 ? "text-amber-600" : "text-slate-800"}
          />
        </div>
      </div>
    </section>
  );
}
