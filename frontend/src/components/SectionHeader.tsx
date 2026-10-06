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
      <div className="flex min-w-0 items-start gap-2.5">
        <span className="relay-accent-bar mt-0.5 h-5 w-1 shrink-0 rounded-full" aria-hidden />
        <div className="min-w-0">
          <h2 className="text-xs font-extrabold uppercase tracking-widest text-slate-800">
            {title}
          </h2>
          {sub && <p className="mt-0.5 text-[11px] text-slate-700">{sub}</p>}
        </div>
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}
