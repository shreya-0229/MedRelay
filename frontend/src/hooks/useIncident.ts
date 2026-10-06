import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { IncidentState } from "../types";

export interface UseIncidentResult {
  incident: IncidentState | null;
  loading: boolean;
  error: string | null;
  /** Replace the cached incident (after drill/decision/resolve responses). */
  setIncident: (inc: IncidentState) => void;
  /** Re-fetch from the backend. */
  refresh: () => void;
}

/**
 * Loads one full incident and keeps it fresh from live WebSocket updates.
 * `liveUpdate` wins when it matches the selected id.
 */
export function useIncident(
  incidentId: string | null,
  liveUpdate: IncidentState | null,
): UseIncidentResult {
  const [incident, setIncident] = useState<IncidentState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);

  const refresh = useCallback(() => {
    if (!incidentId) return;
    const seq = ++seqRef.current;
    setLoading(true);
    api
      .getIncident(incidentId)
      .then((inc) => {
        if (seqRef.current === seq) {
          setIncident(inc);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (seqRef.current === seq) {
          setError(e instanceof Error ? e.message : "Failed to load incident");
        }
      })
      .finally(() => {
        if (seqRef.current === seq) setLoading(false);
      });
  }, [incidentId]);

  useEffect(() => {
    setIncident(null);
    setError(null);
    if (!incidentId) return;
    refresh();
  }, [incidentId, refresh]);

  /* Live updates from the socket always win over the last fetch. */
  useEffect(() => {
    if (liveUpdate && liveUpdate.incident_id === incidentId) {
      setIncident(liveUpdate);
    }
  }, [liveUpdate, incidentId]);

  return { incident, loading, error, setIncident, refresh };
}
