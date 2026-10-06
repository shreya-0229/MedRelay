import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { IncidentState, IncidentSummary } from "../types";

/**
 * Full incident states for the overview table. Summaries alone lack
 * ETA / confidence / location, so each row's full state is fetched once
 * and cached; live WebSocket updates (`incidentUpdates`) refresh the cache.
 * Bounded to the newest 25 incidents — this is a command-center demo,
 * not an archive browser.
 */
const MAX_ROWS = 25;

export function useIncidentDetails(
  summaries: IncidentSummary[],
  incidentUpdates: Record<string, IncidentState>,
): Record<string, IncidentState> {
  const [details, setDetails] = useState<Record<string, IncidentState>>({});
  const cacheRef = useRef<Record<string, IncidentState>>({});
  const inFlightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const ids = summaries.slice(0, MAX_ROWS).map((s) => s.incident_id);
    for (const id of ids) {
      if (cacheRef.current[id] || inFlightRef.current.has(id)) continue;
      inFlightRef.current.add(id);
      api
        .getIncident(id)
        .then((inc) => {
          cacheRef.current[id] = inc;
          setDetails({ ...cacheRef.current });
        })
        .catch(() => {
          /* row renders from the summary alone */
        })
        .finally(() => {
          inFlightRef.current.delete(id);
        });
    }
  }, [summaries]);

  useEffect(() => {
    const updates = Object.values(incidentUpdates);
    if (updates.length === 0) return;
    let changed = false;
    for (const u of updates) {
      if (cacheRef.current[u.incident_id] !== u) {
        cacheRef.current[u.incident_id] = u;
        changed = true;
      }
    }
    if (changed) setDetails({ ...cacheRef.current });
  }, [incidentUpdates]);

  return details;
}
