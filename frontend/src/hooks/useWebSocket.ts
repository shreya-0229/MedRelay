import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Fleet,
  IncidentState,
  IncidentSummary,
  WsAgentEvent,
  WsMessage,
} from "../types";
import { api } from "../api";

export type WsStatus = "connecting" | "connected" | "disconnected";

const MAX_BACKOFF_MS = 5000;
const INITIAL_BACKOFF_MS = 500;
const POLL_INTERVAL_MS = 4000;
const FAILURES_BEFORE_POLL = 3;
const MAX_FEED = 60;

export interface UseWebSocketResult {
  status: WsStatus;
  /** Agent events, newest first, capped at 60. */
  feed: WsAgentEvent[];
  /** Latest full incident state per incident id (from incident_update messages). */
  incidentUpdates: Record<string, IncidentState>;
  /** Full list from incident_list messages (or the polling fallback). Null until received. */
  incidentList: IncidentSummary[] | null;
  fleet: Fleet | null;
  /** True while the HTTP polling fallback is active. */
  pollFallback: boolean;
}

export function useWebSocket(): UseWebSocketResult {
  const [status, setStatus] = useState<WsStatus>("connecting");
  const [feed, setFeed] = useState<WsAgentEvent[]>([]);
  const [incidentUpdates, setIncidentUpdates] = useState<
    Record<string, IncidentState>
  >({});
  const [incidentList, setIncidentList] = useState<IncidentSummary[] | null>(
    null,
  );
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [pollFallback, setPollFallback] = useState(false);

  const failuresRef = useRef(0);
  const backoffRef = useRef(INITIAL_BACKOFF_MS);
  const wsRef = useRef<WebSocket | null>(null);
  const pollTimerRef = useRef<number | null>(null);

  const handleMessage = useCallback((msg: WsMessage) => {
    switch (msg.type) {
      case "agent_event":
        setFeed((prev) => [msg, ...prev].slice(0, MAX_FEED));
        break;
      case "incident_update":
        setIncidentUpdates((prev) => ({
          ...prev,
          [msg.incident.incident_id]: msg.incident,
        }));
        break;
      case "fleet_update":
        setFleet(msg.fleet);
        break;
      case "incident_list":
        setIncidentList(msg.incidents);
        break;
      default:
        break;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: number | null = null;

    const stopPolling = () => {
      if (pollTimerRef.current !== null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      setPollFallback(false);
    };

    const startPolling = () => {
      if (pollTimerRef.current !== null) return;
      setPollFallback(true);
      const poll = async () => {
        try {
          const res = await api.getIncidents();
          if (!cancelled) setIncidentList(res.incidents);
        } catch {
          /* keep the last known list; WS will recover */
        }
      };
      void poll();
      pollTimerRef.current = window.setInterval(poll, POLL_INTERVAL_MS);
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      failuresRef.current += 1;
      setStatus("disconnected");
      if (failuresRef.current >= FAILURES_BEFORE_POLL) startPolling();
      const delay = backoffRef.current;
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS);
      reconnectTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting");
      const url = `${
        window.location.protocol === "https:" ? "wss" : "ws"
      }://${window.location.host}/ws`;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;
      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        failuresRef.current = 0;
        backoffRef.current = INITIAL_BACKOFF_MS;
        setStatus("connected");
        stopPolling();
      };
      ws.onmessage = (ev) => {
        try {
          handleMessage(JSON.parse(ev.data as string) as WsMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => scheduleReconnect();
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* already closed */
        }
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      stopPolling();
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, [handleMessage]);

  return { status, feed, incidentUpdates, incidentList, fleet, pollFallback };
}
