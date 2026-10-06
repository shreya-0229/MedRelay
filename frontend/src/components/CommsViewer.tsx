import { useEffect, useState } from "react";
import { api } from "../api";
import type { CommsMessage } from "../types";
import { formatClock } from "./badges";

const LANGS = [
  { code: "en", label: "English" },
  { code: "hi", label: "हिंदी" },
  { code: "mr", label: "मराठी" },
];

const CHANNEL_COLORS: Record<string, string> = {
  family: "border-sky-500/50 bg-sky-500/10 text-sky-700",
  hospital: "border-violet-500/50 bg-violet-500/10 text-violet-700",
  escalation: "border-red-500/50 bg-red-500/10 text-red-700",
  bystander: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700",
};

/**
 * Trilingual message viewer — reads the REAL communications table via
 * GET /api/incidents/{id}/communications. Tabs per language; every
 * message shown is a persisted row, never a template guess.
 */
export default function CommsViewer({ incidentId }: { incidentId: string }) {
  const [messages, setMessages] = useState<CommsMessage[] | null>(null);
  const [lang, setLang] = useState("en");

  useEffect(() => {
    setMessages(null);
    api
      .getCommunications(incidentId)
      .then((r) => setMessages(r.messages ?? []))
      .catch(() => setMessages([]));
  }, [incidentId]);

  if (messages === null) {
    return <p className="text-xs text-slate-500">Loading messages…</p>;
  }
  if (messages.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        No messages yet — the CommunicationAgent writes here once the plan is
        verified.
      </p>
    );
  }

  const shown = messages.filter((m) => m.language === lang);
  const counts: Record<string, number> = {};
  for (const m of messages) counts[m.language] = (counts[m.language] ?? 0) + 1;

  return (
    <div>
      <div className="flex gap-1 rounded-md bg-relay-panel2 p-1">
        {LANGS.map((l) => (
          <button
            key={l.code}
            type="button"
            onClick={() => setLang(l.code)}
            className={`flex-1 rounded px-2 py-1.5 text-xs font-semibold transition-colors ${
              lang === l.code
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {l.label}
            <span className="ml-1 font-mono text-[10px] text-slate-700">
              {counts[l.code] ?? 0}
            </span>
          </button>
        ))}
      </div>
      <ul className="mt-2 space-y-2">
        {shown.length === 0 && (
          <li className="text-xs text-slate-500">
            No {LANGS.find((l) => l.code === lang)?.label} messages yet.
          </li>
        )}
        {shown.map((m) => (
          <li
            key={m.id}
            className="rounded-md border border-relay-border bg-white p-3"
          >
            <div className="flex items-center gap-2">
              <span
                className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${
                  CHANNEL_COLORS[m.channel] ?? "border-relay-border text-slate-600"
                }`}
              >
                {m.channel}
              </span>
              <span className="ml-auto font-mono text-[10px] text-slate-500">
                {formatClock(m.ts)}
              </span>
            </div>
            <p className="mt-1.5 text-sm leading-snug text-slate-800">
              {m.text}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
