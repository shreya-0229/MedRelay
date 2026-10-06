import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type {
  DemoRunState,
  DemoScenario,
  DemoStep,
  IncidentState,
  WsAgentEvent,
} from "../types";
import { statusLabel } from "./badges";

interface RunUI {
  runId: string;
  status: DemoRunState;
  incidentId: string | null;
  terminalState: string | null;
  error: string;
  startedAt: number;
}

interface Props {
  feed: WsAgentEvent[];
  incidentUpdates: Record<string, IncidentState>;
  onViewIncident: (id: string) => void;
  onResetDone: () => void;
  onSeedDone: () => void;
}

/** Index of the first event after `from` satisfying the rule, or -1. */
function matchRule(
  events: WsAgentEvent[],
  from: number,
  rule: DemoStep["rule"],
  seenStatuses: Set<string>,
): number {
  const t = rule.type;
  if (t === "incident_known") return from; // caller guarantees incident known
  if (t === "incident_status") {
    return rule.status && seenStatuses.has(rule.status) ? from : -1;
  }
  if (t === "action_all") {
    let max = -1;
    for (const mm of rule.matches ?? []) {
      const needle = mm.toLowerCase();
      let found = -1;
      for (let i = from + 1; i < events.length; i++) {
        if (events[i].action.toLowerCase().includes(needle)) {
          found = i;
          break;
        }
      }
      if (found === -1) return -1;
      max = Math.max(max, found);
    }
    return max;
  }
  // "action"
  const needle = (rule.match ?? "").toLowerCase();
  for (let i = from + 1; i < events.length; i++) {
    if (events[i].action.toLowerCase().includes(needle)) return i;
  }
  return -1;
}

function ScenarioCard({
  scenario,
  run,
  feed,
  seenStatuses,
  onRun,
  onViewIncident,
}: {
  scenario: DemoScenario;
  run: RunUI | undefined;
  feed: WsAgentEvent[];
  seenStatuses: Set<string>;
  onRun: () => void;
  onViewIncident: (id: string) => void;
}) {
  const running = run?.status === "running";

  const doneCount = useMemo(() => {
    if (!run?.incidentId) return 0;
    // Chronological, this run only: incident match + started after Run.
    const evs = [...feed]
      .reverse()
      .filter(
        (e) =>
          e.incident_id === run.incidentId &&
          new Date(e.ts).getTime() >= run.startedAt - 2000,
      );
    let idx = -1;
    let n = 0;
    for (const step of scenario.steps) {
      const at = matchRule(evs, idx, step.rule, seenStatuses);
      if (at === -1) break;
      idx = at;
      n++;
    }
    return n;
  }, [feed, run, scenario.steps, seenStatuses]);

  const total = scenario.steps.length;
  const complete = run?.status === "done";
  const failed = run?.status === "failed";

  return (
    <div
      className={`flex flex-col rounded-lg border bg-relay-panel p-4 ${
        scenario.flagship
          ? "border-amber-500/60 shadow-[0_0_24px_rgba(245,158,11,0.12)]"
          : "border-relay-border"
      }`}
    >
      <div className="mb-1 flex items-start justify-between gap-2">
        <h3 className="text-sm font-bold text-slate-900">{scenario.name}</h3>
        {scenario.flagship && (
          <span className="shrink-0 rounded border border-amber-500/60 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-amber-700">
            FLAGSHIP
          </span>
        )}
      </div>
      <p className="mb-3 text-xs leading-relaxed text-slate-600">
        {scenario.description}
      </p>

      <div className="mb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className={`rounded-md px-4 py-1.5 text-xs font-bold text-white transition-colors ${
            running
              ? "cursor-wait bg-slate-700"
              : scenario.flagship
                ? "relay-btn-amber"
                : "relay-btn-primary"
          }`}
        >
          {running ? "Running…" : complete ? "Run again" : "Run"}
        </button>
        {run && (
          <span className="font-mono text-[11px] text-slate-500">
            Step {Math.min(doneCount, total)}/{total}
          </span>
        )}
        {complete && run.terminalState && (
          <span className="rounded border border-emerald-500/50 bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-600">
            {statusLabel(run.terminalState)}
          </span>
        )}
        {failed && (
          <span className="rounded border border-red-500/50 bg-red-500/10 px-1.5 py-0.5 text-[11px] font-medium text-red-600">
            Failed
          </span>
        )}
      </div>

      {run && (
        <ol className="mb-3 max-h-56 space-y-1 overflow-y-auto pr-1">
          {scenario.steps.map((s, i) => {
            const done = i < doneCount;
            const current = running && i === doneCount;
            return (
              <li
                key={s.id}
                className={`flex items-start gap-2 text-[11px] leading-snug ${
                  done
                    ? "text-emerald-600"
                    : current
                      ? "text-amber-700"
                      : "text-slate-700"
                }`}
              >
                <span className="mt-px inline-block w-4 shrink-0 text-center font-mono">
                  {done ? "✓" : current ? "▸" : "·"}
                </span>
                <span>
                  <span className="mr-1 font-mono text-slate-700">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  {s.label}
                </span>
              </li>
            );
          })}
        </ol>
      )}

      {failed && run.error && (
        <p className="mb-2 text-[11px] text-red-600">{run.error}</p>
      )}

      <div className="mt-auto">
        {complete && run.incidentId && (
          <button
            type="button"
            onClick={() => onViewIncident(run.incidentId!)}
            className="text-xs font-semibold text-sky-600 hover:text-sky-700"
          >
            View incident {run.incidentId} →
          </button>
        )}
        {run?.status === "done" &&
          run.terminalState === "human_review_required" &&
          run.incidentId && (
            <p className="mt-1 text-[11px] text-amber-700">
              Paused for review — decide in the command view's operator panel.
            </p>
          )}
      </div>
    </div>
  );
}

export default function DemoView({
  feed,
  incidentUpdates,
  onViewIncident,
  onResetDone,
  onSeedDone,
}: Props) {
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [runs, setRuns] = useState<Record<string, RunUI>>({});
  const [statusHistory, setStatusHistory] = useState<Record<string, string[]>>(
    {},
  );
  const [resetting, setResetting] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const runsRef = useRef(runs);
  runsRef.current = runs;

  const loadScenarios = () => {
    setLoadError(null);
    api
      .listDemoScenarios()
      .then((r) => setScenarios(r.scenarios ?? []))
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "Load failed"),
      );
  };

  useEffect(() => {
    loadScenarios();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Accumulate every current_status ever seen per incident (sticky). */
  useEffect(() => {
    setStatusHistory((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const [id, inc] of Object.entries(incidentUpdates)) {
        const arr = next[id] ?? [];
        if (!arr.includes(inc.current_status)) {
          next[id] = [...arr, inc.current_status];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [incidentUpdates]);

  /* Poll running scenarios once a second. */
  useEffect(() => {
    const t = window.setInterval(async () => {
      const active = Object.entries(runsRef.current).filter(
        ([, r]) => r.status === "running",
      );
      if (active.length === 0) return;
      for (const [sid, r] of active) {
        try {
          const s = await api.getDemoRun(r.runId);
          setRuns((prev) => ({
            ...prev,
            [sid]: {
              ...prev[sid],
              status: s.status,
              incidentId: s.incident_id,
              terminalState: s.terminal_state,
              error: s.error,
            },
          }));
        } catch {
          /* keep polling */
        }
      }
    }, 1000);
    return () => window.clearInterval(t);
  }, []);

  const startRun = async (scenarioId: string) => {
    try {
      const res = await api.runDemoScenario(scenarioId);
      setRuns((prev) => ({
        ...prev,
        [scenarioId]: {
          runId: res.scenario_run_id,
          status: "running",
          incidentId: res.incident_id,
          terminalState: null,
          error: "",
          startedAt: Date.now(),
        },
      }));
    } catch (e) {
      window.alert(
        `Could not start scenario: ${e instanceof Error ? e.message : e}`,
      );
    }
  };

  const doReset = async () => {
    if (
      !window.confirm(
        "Reset the demo? All incidents, audit history and fleet state will be cleared.",
      )
    )
      return;
    setResetting(true);
    try {
      await api.resetDemo();
      setRuns({});
      setStatusHistory({});
      setSeedMsg(null);
      onResetDone();
    } catch (e) {
      window.alert(`Reset failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setResetting(false);
    }
  };

  const doSeed = async () => {
    if (
      !window.confirm(
        "Load the sample day? 6 realistic pre-run incidents (badged SAMPLE) will be added — one pauses at human review for a live decision.",
      )
    )
      return;
    setSeeding(true);
    setSeedMsg(null);
    try {
      const r = await api.seedSample();
      setSeedMsg(
        `Loaded ${r.incidents.length} sample incidents — switch to the Command tab to explore them.`,
      );
      onSeedDone();
    } catch (e) {
      window.alert(`Seed failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setSeeding(false);
    }
  };

  const flagship = scenarios.find((s) => s.flagship);
  const rest = scenarios.filter((s) => !s.flagship);

  return (
    <div className="mx-auto max-w-[1200px] px-4 py-6">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="mr-auto">
          <h2 className="text-xl font-bold tracking-tight text-slate-900">
            Demo Scenarios
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            One-button judging drills. Every scenario drives the{" "}
            <span className="font-semibold text-slate-800">
              real backend pipeline
            </span>{" "}
            — checklist steps tick off only as genuine backend events arrive.
          </p>
        </div>
        <button
          type="button"
          onClick={doSeed}
          disabled={seeding}
          className="relay-btn-primary rounded-lg px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
        >
          {seeding ? "Loading…" : "Load sample data"}
        </button>
        <button
          type="button"
          onClick={doReset}
          disabled={resetting}
          className="rounded-md border border-red-500/60 bg-red-500/10 px-4 py-2 text-sm font-bold text-red-600 transition-colors hover:bg-red-500/20 disabled:opacity-50"
        >
          {resetting ? "Resetting…" : "Reset Demo"}
        </button>
      </div>
      {seedMsg && (
        <div
          className="mb-4 rounded-lg border border-blue-500/50 bg-blue-500/10 px-4 py-2.5 text-sm text-blue-800"
          role="status"
        >
          {seedMsg}
        </div>
      )}

      {flagship && (
        <div className="mb-4">
          <ScenarioCard
            scenario={flagship}
            run={runs[flagship.id]}
            feed={feed}
            seenStatuses={
              new Set(statusHistory[runs[flagship.id]?.incidentId ?? ""] ?? [])
            }
            onRun={() => startRun(flagship.id)}
            onViewIncident={onViewIncident}
          />
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {rest.map((s) => (
          <ScenarioCard
            key={s.id}
            scenario={s}
            run={runs[s.id]}
            feed={feed}
            seenStatuses={
              new Set(statusHistory[runs[s.id]?.incidentId ?? ""] ?? [])
            }
            onRun={() => startRun(s.id)}
            onViewIncident={onViewIncident}
          />
        ))}
      </div>

      {scenarios.length === 0 && !loadError && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton h-44 rounded-lg" />
          ))}
        </div>
      )}
      {loadError && (
        <div className="rounded-lg border border-red-500/60 bg-red-500/10 px-4 py-3" role="alert">
          <p className="text-sm text-red-800">
            <span className="font-bold">Could not load demo scenarios.</span>{" "}
            {loadError}
          </p>
          <button
            type="button"
            onClick={loadScenarios}
            className="mt-2 rounded-md border border-red-500/50 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-800 hover:bg-red-500/20"
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
