import type { WsAgentEvent } from "../types";
import {
  AGENT_DOT,
  agentShortName,
  formatClock,
  pct,
} from "./badges";

interface Props {
  feed: WsAgentEvent[];
}

export default function LiveFeed({ feed }: Props) {
  return (
    <section className="flex h-[420px] min-h-0 flex-col rounded-lg border border-relay-border bg-relay-panel xl:h-full">
      <header className="flex items-center justify-between border-b border-relay-border px-3 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
          Agent activity
        </h2>
        <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
          <span className="relay-pulse inline-block h-2 w-2 rounded-full bg-emerald-400" />
          live
        </span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {feed.length === 0 ? (
          <div className="py-8 text-center text-sm text-slate-500">
            Waiting for agent events…
            <br />
            They stream here as incidents run.
          </div>
        ) : (
          <ul className="space-y-2.5">
            {feed.map((ev, i) => (
              <li
                key={`${ev.ts}-${ev.agent}-${ev.action}-${i}`}
                className="relay-fade-in"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                      AGENT_DOT[ev.agent] ?? "bg-slate-400"
                    }`}
                  />
                  <span className="text-xs font-semibold text-slate-200">
                    {agentShortName(ev.agent)}
                  </span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-500">
                    {formatClock(ev.ts)}
                  </span>
                </div>
                <div className="mt-0.5 pl-4 text-xs text-slate-300">
                  {ev.action}
                </div>
                {ev.rationale && (
                  <div className="mt-0.5 line-clamp-2 pl-4 text-[11px] leading-snug text-slate-500">
                    {ev.rationale}
                  </div>
                )}
                <div className="mt-0.5 pl-4 text-[11px] text-slate-500">
                  confidence{" "}
                  <span className="font-medium text-slate-300">
                    {pct(ev.confidence)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
