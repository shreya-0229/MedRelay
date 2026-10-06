import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type {
  Fleet,
  Health,
  IncidentState,
  IncidentSummary,
  Stats,
} from "./types";
import { useWebSocket } from "./hooks/useWebSocket";
import { useIncident } from "./hooks/useIncident";
import { useIncidentDetails } from "./hooks/useIncidentDetails";
import StatsBar from "./components/StatsBar";
import PipelineStrip from "./components/PipelineStrip";
import OverviewTable from "./components/OverviewTable";
import AgentStatusPanel, {
  deriveAgentStates,
} from "./components/AgentStatusPanel";
import AgentGraph from "./components/AgentGraph";
import MapPanel from "./components/MapPanel";
import IncidentDetail from "./components/IncidentDetail";
import OperatorPanel from "./components/OperatorPanel";
import DemoView from "./components/DemoView";
import { AmbulancePanel, HospitalPanel } from "./components/ResourcePanels";
import NewIncidentModal from "./components/NewIncidentModal";

function toSummary(inc: IncidentState): IncidentSummary {
  return {
    incident_id: inc.incident_id,
    incident_type: inc.incident_type,
    severity: inc.severity,
    current_status: inc.current_status,
    escalation_status: inc.escalation_status,
    created_at: inc.created_at,
    ambulance_id: inc.selected_ambulance?.id ?? null,
    hospital_name: inc.selected_hospital?.name ?? null,
  };
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return (
    <span className="font-mono text-sm tabular-nums text-slate-300">
      {now.toLocaleTimeString("en-GB")}
    </span>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [view, setView] = useState<"command" | "demo">("command");
  const [bootError, setBootError] = useState<string | null>(null);
  const autoSelected = useRef(false);

  const {
    status,
    feed,
    incidentUpdates,
    incidentList,
    fleet: wsFleet,
    pollFallback,
  } = useWebSocket();

  /* Full state of the selected incident (REST + live WS merge). */
  const selectedLive = selectedId ? (incidentUpdates[selectedId] ?? null) : null;
  const {
    incident: selectedIncident,
    setIncident: setSelectedIncident,
  } = useIncident(selectedId, selectedLive);

  /* Full states behind the overview-table rows. */
  const details = useIncidentDetails(incidents, incidentUpdates);
  const mapIncidents = useMemo(() => Object.values(details), [details]);

  const agentStates = useMemo(
    () => deriveAgentStates(selectedIncident, feed),
    [selectedIncident, feed],
  );

  /* Initial data + polling for stats/fleet (WS pushes deltas). */
  const loadInitial = useCallback(() => {
    setBootError(null);
    api
      .health()
      .then(setHealth)
      .catch((e: unknown) =>
        setBootError(
          e instanceof Error ? e.message : "Backend unreachable",
        ),
      );
    api
      .getFleet()
      .then(setFleet)
      .catch(() => {});
    api
      .getIncidents()
      .then((r) => setIncidents(r.incidents ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadInitial();
    const loadStats = () =>
      api
        .getStats()
        .then(setStats)
        .catch(() => {});
    void loadStats();
    const timer = window.setInterval(loadStats, 5000);
    return () => window.clearInterval(timer);
  }, [loadInitial]);

  /* Live list pushed over the socket (or the polling fallback). */
  useEffect(() => {
    if (incidentList) setIncidents(incidentList);
  }, [incidentList]);

  /* Fleet pushed over the socket. */
  useEffect(() => {
    if (wsFleet) setFleet(wsFleet);
  }, [wsFleet]);

  /* Merge per-incident updates into the list summaries. */
  useEffect(() => {
    const updates = Object.values(incidentUpdates);
    if (updates.length === 0) return;
    setIncidents((prev) => {
      const next = new Map(prev.map((i) => [i.incident_id, i]));
      for (const u of updates) next.set(u.incident_id, toSummary(u));
      return [...next.values()].sort((a, b) =>
        a.created_at < b.created_at ? 1 : -1,
      );
    });
  }, [incidentUpdates]);

  /* Auto-select the newest incident on first load. */
  useEffect(() => {
    if (!autoSelected.current && selectedId === null && incidents.length > 0) {
      autoSelected.current = true;
      setSelectedId(incidents[0].incident_id);
    }
  }, [incidents, selectedId]);

  const handleCreated = (incident: IncidentState) => {
    setIncidents((prev) => [
      toSummary(incident),
      ...prev.filter((p) => p.incident_id !== incident.incident_id),
    ]);
    autoSelected.current = true;
    setSelectedId(incident.incident_id);
    setSelectedIncident(incident);
    setModalOpen(false);
  };

  const handleIncidentChanged = (incident: IncidentState) => {
    setIncidents((prev) =>
      prev.map((p) =>
        p.incident_id === incident.incident_id ? toSummary(incident) : p,
      ),
    );
    if (incident.incident_id === selectedId) setSelectedIncident(incident);
  };

  const wsDot =
    status === "connected"
      ? "bg-emerald-400"
      : status === "connecting"
        ? "bg-amber-400 relay-pulse"
        : "bg-red-400";
  const wsText =
    status === "connected"
      ? "LIVE"
      : status === "connecting"
        ? "CONNECTING"
        : pollFallback
          ? "OFFLINE · POLLING"
          : "OFFLINE";
  const agentsOnline = health ? `${health.agents.length}/6` : "…";

  return (
    <div className="min-h-screen bg-relay-bg font-sans text-slate-100">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-relay-border bg-relay-panel px-4 py-3">
        <div className="mr-auto flex items-center gap-2.5">
          <span className="inline-block h-3 w-3 rounded-full bg-red-500" />
          <div>
            <h1 className="text-lg font-bold leading-tight tracking-tight">
              MedRelay
            </h1>
            <p className="text-[11px] leading-tight text-slate-500">
              Autonomous Emergency Response Network
            </p>
          </div>
        </div>
        <Clock />
        <span className="inline-flex items-center gap-1.5 rounded-full border border-relay-border bg-relay-bg px-2.5 py-1 text-[11px] font-semibold tracking-wide text-slate-300">
          <span className={`inline-block h-2 w-2 rounded-full ${wsDot}`} />
          {wsText}
        </span>
        <span className="rounded-full border border-relay-border bg-relay-bg px-2.5 py-1 font-mono text-[11px] text-slate-400">
          Agents online: {agentsOnline}
        </span>
        <span className="rounded-full border border-relay-border bg-relay-bg px-2.5 py-1 font-mono text-[11px] text-slate-400">
          Active incidents: {stats?.active ?? "…"}
        </span>
        <span className="rounded-full border border-relay-border bg-relay-bg px-2.5 py-1 font-mono text-[11px] text-slate-400">
          LLM: {health?.llm_provider ?? "…"}
        </span>
        <span className="rounded-full border border-relay-border bg-relay-bg px-2.5 py-1 font-mono text-[11px] text-slate-500">
          v{health?.version ?? "…"}
        </span>
        <div className="flex overflow-hidden rounded-md border border-relay-border">
          <button
            type="button"
            onClick={() => setView("command")}
            className={`px-3 py-2 text-xs font-bold transition-colors ${
              view === "command"
                ? "bg-blue-600 text-white"
                : "bg-relay-bg text-slate-400 hover:text-slate-200"
            }`}
          >
            Command
          </button>
          <button
            type="button"
            onClick={() => setView("demo")}
            className={`px-3 py-2 text-xs font-bold transition-colors ${
              view === "demo"
                ? "bg-amber-600 text-white"
                : "bg-relay-bg text-slate-400 hover:text-slate-200"
            }`}
          >
            Demo Scenarios
          </button>
        </div>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-500"
        >
          + New incident
        </button>
      </header>

      {view === "demo" ? (
        <DemoView
          feed={feed}
          incidentUpdates={incidentUpdates}
          onViewIncident={(id) => {
            setSelectedId(id);
            setView("command");
          }}
          onResetDone={() => {
            setSelectedId(null);
            autoSelected.current = false;
          }}
        />
      ) : (
      <main className="mx-auto max-w-[1800px] space-y-4 px-4 py-4">
        {bootError && (
          <div
            className="flex flex-wrap items-center gap-3 rounded-lg border border-red-500/60 bg-red-500/10 px-4 py-3"
            role="alert"
          >
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
            <p className="text-sm text-red-200">
              <span className="font-bold">Backend unreachable.</span> Make sure
              the server is running, then retry.{" "}
              <span className="font-mono text-xs text-red-200/70">
                {bootError}
              </span>
            </p>
            <button
              type="button"
              onClick={loadInitial}
              className="ml-auto rounded-md border border-red-500/50 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-200 transition-colors hover:bg-red-500/20"
            >
              Retry connection
            </button>
          </div>
        )}
        {status === "disconnected" && (
          <div
            className="flex items-center gap-2.5 rounded-lg border border-amber-500/50 bg-amber-500/10 px-4 py-2.5"
            role="status"
          >
            <span className="relay-pulse inline-block h-2 w-2 rounded-full bg-amber-400" />
            <p className="text-xs text-amber-200">
              Live feed disconnected — reconnecting automatically
              {pollFallback ? " · polling fallback active" : ""}.
            </p>
          </div>
        )}

        <PipelineStrip incident={selectedIncident} feed={feed} />

        <StatsBar stats={stats} />

        <OverviewTable
          summaries={incidents}
          details={details}
          selectedId={selectedId}
          onSelect={setSelectedId}
          loading={incidents.length === 0 && incidentList === null}
        />

        <div className="grid gap-4 xl:grid-cols-12">
          <div className="xl:col-span-3">
            <AgentStatusPanel incident={selectedIncident} feed={feed} />
          </div>
          <div className="xl:col-span-5">
            <AgentGraph
              states={agentStates}
              feed={feed}
              incidentId={selectedId}
            />
          </div>
          <div className="xl:col-span-4">
            <MapPanel
              incidents={mapIncidents}
              fleet={fleet}
              selectedId={selectedId}
            />
          </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-12">
          <div className="xl:col-span-7">
            <IncidentDetail
              incidentId={selectedId}
              liveUpdate={selectedLive}
              onIncidentChanged={handleIncidentChanged}
            />
          </div>
          <div className="space-y-4 xl:col-span-5">
            <OperatorPanel
              incident={selectedIncident}
              onChanged={handleIncidentChanged}
            />
            <AmbulancePanel fleet={fleet} incidents={mapIncidents} />
            <HospitalPanel fleet={fleet} />
          </div>
        </div>
      </main>
      )}

      <NewIncidentModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  );
}
