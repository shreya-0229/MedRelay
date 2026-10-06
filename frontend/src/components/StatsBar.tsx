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
}: {
  label: string;
  value: string;
  valueClass: string;
  sub: string;
}) {
  return (
    <div className="rounded-lg border border-relay-border bg-relay-panel px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${valueClass}`}>
        {value}
      </div>
      <div className="mt-0.5 text-xs text-slate-500">{sub}</div>
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
        valueClass="text-slate-100"
        sub="All time"
      />
      <Card
        label="Active"
        value={String(stats.active ?? 0)}
        valueClass="text-sky-300"
        sub="In pipeline now"
      />
      <Card
        label="Escalated"
        value={String(escalated)}
        valueClass={escalated > 0 ? "text-red-400" : "text-slate-400"}
        sub="Need human dispatcher"
      />
      <Card
        label="Avg confidence"
        value={pct(stats.avg_confidence)}
        valueClass="text-emerald-400"
        sub="Across agent outputs"
      />
    </div>
  );
}
