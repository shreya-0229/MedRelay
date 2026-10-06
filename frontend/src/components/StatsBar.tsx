import type { Stats } from "../types";
import { pct } from "./badges";
import { SkeletonCards } from "./Skeleton";

interface Props {
  stats: Stats | null;
}

function Card({
  label,
  value,
  valueClass,
  sub,
  icon,
  iconBg,
  accent,
}: {
  label: string;
  value: string;
  valueClass: string;
  sub: string;
  icon: string;
  iconBg: string;
  accent: string;
}) {
  return (
    <div className="relay-card relay-card-hover relative overflow-hidden rounded-2xl border border-relay-border bg-relay-panel px-4 py-3.5">
      <span
        className={`absolute inset-x-0 top-0 h-1 ${accent}`}
        aria-hidden
      />
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] font-bold uppercase tracking-widest text-slate-500">
          {label}
        </div>
        <span
          className={`flex h-8 w-8 items-center justify-center rounded-xl text-base ${iconBg}`}
          aria-hidden
        >
          {icon}
        </span>
      </div>
      <div className={`mt-1 text-[32px] font-extrabold leading-none tabular-nums tracking-tight ${valueClass}`}>
        {value}
      </div>
      <div className="mt-1.5 text-xs font-medium text-slate-500">{sub}</div>
    </div>
  );
}

export default function StatsBar({ stats }: Props) {
  if (!stats) return <SkeletonCards cards={4} />;
  const escalated = stats.escalated ?? 0;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Card
        label="Total incidents"
        value={String(stats.total ?? 0)}
        valueClass="text-slate-900"
        sub="All time"
        icon="📋"
        iconBg="bg-blue-500/10"
        accent="bg-gradient-to-r from-blue-500 to-sky-400"
      />
      <Card
        label="Active"
        value={String(stats.active ?? 0)}
        valueClass="text-sky-700"
        sub="In pipeline now"
        icon="⚡"
        iconBg="bg-sky-500/10"
        accent="bg-gradient-to-r from-sky-500 to-cyan-400"
      />
      <Card
        label="Escalated"
        value={String(escalated)}
        valueClass={escalated > 0 ? "text-red-600" : "text-slate-600"}
        sub="Need human dispatcher"
        icon="🚨"
        iconBg="bg-red-500/10"
        accent="bg-gradient-to-r from-red-500 to-orange-400"
      />
      <Card
        label="Avg confidence"
        value={pct(stats.avg_confidence)}
        valueClass="text-emerald-600"
        sub="Across agent outputs"
        icon="🎯"
        iconBg="bg-emerald-500/10"
        accent="bg-gradient-to-r from-emerald-500 to-teal-400"
      />
    </div>
  );
}
