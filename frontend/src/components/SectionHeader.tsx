import type { ReactNode } from "react";

/** One consistent section-header language across the whole dashboard. */
export default function SectionHeader({
  title,
  sub,
  right,
}: {
  title: string;
  sub?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-relay-border px-4 py-2.5">
      <div className="min-w-0">
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-300">
          {title}
        </h2>
        {sub && <p className="mt-0.5 text-[11px] text-slate-600">{sub}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}
