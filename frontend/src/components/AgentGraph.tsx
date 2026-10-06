import { useEffect, useRef, useState } from "react";
import type { WsAgentEvent } from "../types";
import { PIPELINE_AGENTS } from "../types";
import type { AgentLiveState } from "./AgentStatusPanel";
import SectionHeader from "./SectionHeader";

interface Props {
  /** Live per-agent states (colors nodes honestly). */
  states: AgentLiveState[];
  /** Live WS agent events (newest first) — the ONLY pulse trigger. */
  feed: WsAgentEvent[];
  incidentId: string | null;
}

interface NodePos {
  key: string;
  label: string;
  x: number;
  y: number;
}

const ORCH = { x: 320, y: 46 };
const AGENT_POS: NodePos[] = [
  { key: "IntakeAgent", label: "Intake", x: 100, y: 172 },
  { key: "TriageAgent", label: "Triage", x: 320, y: 172 },
  { key: "DispatchAgent", label: "Dispatch", x: 540, y: 172 },
  { key: "HospitalLiaisonAgent", label: "Hospital", x: 100, y: 296 },
  { key: "VerificationAgent", label: "Verification", x: 320, y: 296 },
  { key: "CommunicationAgent", label: "Comms", x: 540, y: 296 },
];

const STATUS_FILL: Record<string, string> = {
  RUNNING: "#38bdf8",
  COMPLETED: "#34d399",
  FAILED: "#f87171",
  WAITING: "#64748b",
  "HUMAN REVIEW": "#fbbf24",
  IDLE: "#334155",
};

interface Pulse {
  node: string; // agent key or "ORCHESTRATOR"
  n: number;
}

/**
 * Orchestrator ↔ agent communication graph. Nodes are colored by the real
 * per-agent status; a node + its edge pulse EXACTLY once per real backend
 * event (keyed animation, settles after ~1.2s). No idle looping.
 */
export default function AgentGraph({ states, feed, incidentId }: Props) {
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const seenRef = useRef<Set<string>>(new Set());
  const timerRef = useRef<number | null>(null);

  /* Fire one pulse per real event for the selected incident. */
  useEffect(() => {
    if (!incidentId) return;
    const newest = feed.find((e) => e.incident_id === incidentId);
    if (!newest) return;
    const id = `${newest.ts}|${newest.agent}|${newest.action}`;
    if (seenRef.current.has(id)) return;
    seenRef.current.add(id);
    if (seenRef.current.size > 200) {
      seenRef.current = new Set([...seenRef.current].slice(-100));
    }
    const node = PIPELINE_AGENTS.some((a) => a.key === newest.agent)
      ? newest.agent
      : "ORCHESTRATOR";
    setPulse({ node, n: Date.now() });
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setPulse(null), 1400);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [feed, incidentId]);

  const statusByKey = Object.fromEntries(states.map((s) => [s.key, s.status]));
  const latestActionByKey: Record<string, string> = {};
  if (incidentId) {
    for (const e of feed) {
      if (e.incident_id !== incidentId) continue;
      if (!(e.agent in latestActionByKey)) latestActionByKey[e.agent] = e.action;
    }
  }

  return (
    <section className="flex h-full flex-col rounded-lg border border-relay-border bg-relay-panel">
      <SectionHeader
        title="Agent communication"
        sub="highlights fire on real backend events only"
      />
      <div className="flex-1 p-2">
        <svg
          viewBox="0 0 640 344"
          className="h-full max-h-[380px] min-h-[260px] w-full"
          role="img"
          aria-label="Orchestrator and agent communication graph"
        >
          {/* edges */}
          {AGENT_POS.map((p) => {
            const active = pulse?.node === p.key;
            const mx = (ORCH.x + p.x) / 2;
            const my = (ORCH.y + p.y) / 2;
            const label = latestActionByKey[p.key];
            return (
              <g key={`edge-${p.key}`}>
                <line
                  x1={ORCH.x}
                  y1={ORCH.y + 22}
                  x2={p.x}
                  y2={p.y - 22}
                  stroke={active ? "#38bdf8" : "#1e3a5f"}
                  strokeWidth={active ? 2.5 : 1.5}
                />
                {label && (
                  <text
                    x={mx}
                    y={my - 4}
                    textAnchor="middle"
                    fontSize="9.5"
                    fill={active ? "#7dd3fc" : "#64748b"}
                  >
                    {label.length > 26 ? `${label.slice(0, 26)}…` : label}
                  </text>
                )}
              </g>
            );
          })}

          {/* orchestrator node */}
          <g>
            {pulse?.node === "ORCHESTRATOR" && (
              <circle
                key={`ping-orch-${pulse.n}`}
                cx={ORCH.x}
                cy={ORCH.y}
                r={20}
                fill="none"
                stroke="#38bdf8"
                strokeWidth={2}
                className="graph-ping"
              />
            )}
            <circle
              cx={ORCH.x}
              cy={ORCH.y}
              r={20}
              fill="#12233d"
              stroke={pulse?.node === "ORCHESTRATOR" ? "#38bdf8" : "#1e3a5f"}
              strokeWidth={2}
            />
            <text
              x={ORCH.x}
              y={ORCH.y + 4}
              textAnchor="middle"
              fontSize="10"
              fontWeight="bold"
              fill="#e2e8f0"
            >
              ORCH
            </text>
            <text
              x={ORCH.x}
              y={ORCH.y + 36}
              textAnchor="middle"
              fontSize="10"
              fill="#94a3b8"
            >
              Orchestrator
            </text>
          </g>

          {/* agent nodes */}
          {AGENT_POS.map((p) => {
            const status = statusByKey[p.key] ?? "IDLE";
            const fill = STATUS_FILL[status] ?? "#334155";
            const active = pulse?.node === p.key;
            return (
              <g key={`node-${p.key}`}>
                {active && (
                  <circle
                    key={`ping-${p.key}-${pulse!.n}`}
                    cx={p.x}
                    cy={p.y}
                    r={18}
                    fill="none"
                    stroke={fill}
                    strokeWidth={2}
                    className="graph-ping"
                  />
                )}
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={18}
                  fill="#0f1d33"
                  stroke={fill}
                  strokeWidth={active ? 3 : 2}
                />
                <circle cx={p.x} cy={p.y} r={6} fill={fill} />
                <text
                  x={p.x}
                  y={p.y + 34}
                  textAnchor="middle"
                  fontSize="10"
                  fontWeight={600}
                  fill="#cbd5e1"
                >
                  {p.label}
                </text>
                <text
                  x={p.x}
                  y={p.y + 46}
                  textAnchor="middle"
                  fontSize="9"
                  fill="#7d8fa8"
                >
                  {status}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}
