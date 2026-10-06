import type { ReactNode } from "react";
import { formatClock } from "./badges";

export type AlertVariant = "critical" | "warning" | "success" | "info";

const VARIANTS: Record<
  AlertVariant,
  { box: string; dot: string; title: string; body: string }
> = {
  critical: {
    box: "border-red-500/60 bg-red-500/10",
    dot: "bg-red-500 relay-pulse",
    title: "text-red-300",
    body: "text-red-200/80",
  },
  warning: {
    box: "border-amber-500/60 bg-amber-500/10",
    dot: "bg-amber-400 relay-pulse",
    title: "text-amber-300",
    body: "text-amber-200/80",
  },
  success: {
    box: "border-emerald-500/50 bg-emerald-500/10",
    dot: "bg-emerald-400",
    title: "text-emerald-300",
    body: "text-emerald-200/80",
  },
  info: {
    box: "border-sky-500/50 bg-sky-500/10",
    dot: "bg-sky-400",
    title: "text-sky-300",
    body: "text-sky-200/80",
  },
};

/** One consistent alert language: red = critical, amber = warning, green = ok. */
export default function AlertBanner({
  variant,
  title,
  detail,
  ts,
  children,
}: {
  variant: AlertVariant;
  title: string;
  detail?: string | null;
  ts?: string | null;
  children?: ReactNode;
}) {
  const v = VARIANTS[variant];
  return (
    <div className={`rounded-md border px-4 py-3 ${v.box}`} role="alert">
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${v.dot}`}
          aria-hidden
        />
        <span className={`text-sm font-bold uppercase tracking-wide ${v.title}`}>
          {title}
        </span>
        {ts && (
          <span className="ml-auto font-mono text-[10px] tabular-nums text-slate-500">
            {formatClock(ts)}
          </span>
        )}
      </div>
      {detail && <p className={`mt-1 text-sm ${v.body}`}>{detail}</p>}
      {children}
    </div>
  );
}
