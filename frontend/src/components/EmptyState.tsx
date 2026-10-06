import type { ReactNode } from "react";

/** On-brand empty states: helpful, never blank. */
export default function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <span
        className="flex h-10 w-10 items-center justify-center rounded-full border border-relay-border bg-relay-panel2"
        aria-hidden
      >
        <span className="h-2.5 w-2.5 rounded-full bg-slate-700" />
      </span>
      <p className="mt-3 text-sm font-semibold text-slate-700">{title}</p>
      {hint && <p className="mt-1 max-w-xs text-xs text-slate-500">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
