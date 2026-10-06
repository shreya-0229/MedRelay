import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Fleet, IncidentState } from "../types";

interface Props {
  incidents: IncidentState[];
  fleet: Fleet | null;
  selectedId: string | null;
  onTilesFailed: () => void;
}

const ESRI_DARK =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";

const AMB_COLOR: Record<string, string> = {
  available: "#34d399",
  en_route: "#38bdf8",
  out_of_service: "#f87171",
  reserved: "#fbbf24",
};

/**
 * Leaflet map, loaded lazily so it never blocks the dashboard.
 * Esri dark canvas tiles (free, no key); straight-line route segments
 * for the selected incident (honest: no road routing in the prototype).
 */
export default function LeafletMap({
  incidents,
  fleet,
  selectedId,
  onTilesFailed,
}: Props) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const failedRef = useRef(false);

  /* Create the map exactly once. */
  useEffect(() => {
    if (!divRef.current || mapRef.current) return;
    const map = L.map(divRef.current, {
      center: [18.5204, 73.8567],
      zoom: 12,
      zoomControl: true,
      attributionControl: true,
    });
    const tiles = L.tileLayer(ESRI_DARK, {
      attribution: "Tiles &copy; Esri",
      maxZoom: 16,
    });
    let errors = 0;
    let loaded = false;
    tiles.on("tileerror", () => {
      errors += 1;
      if (errors > 8 && !loaded && !failedRef.current) {
        failedRef.current = true;
        onTilesFailed();
      }
    });
    tiles.on("load", () => {
      loaded = true;
    });
    tiles.addTo(map);
    /* If nothing loads within 15s (offline/blocked tiles), fall back. */
    const timer = window.setTimeout(() => {
      if (!loaded && !failedRef.current) {
        failedRef.current = true;
        onTilesFailed();
      }
    }, 15000);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      window.clearTimeout(timer);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Refresh markers + route whenever the data changes. */
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.clearLayers();

    for (const h of fleet?.hospitals ?? []) {
      L.circleMarker([h.lat, h.lon], {
        radius: 7,
        color: "#2dd4bf",
        weight: 2,
        fillColor: "#2dd4bf",
        fillOpacity: 0.25,
      })
        .bindPopup(
          `<b>${h.name}</b><br/>Beds free: ${h.free_beds}/${h.total_beds}<br/>${h.specialties.join(", ")}`,
        )
        .addTo(layer);
    }
    for (const a of fleet?.ambulances ?? []) {
      const c = AMB_COLOR[a.status] ?? "#94a3b8";
      L.circleMarker([a.lat, a.lon], {
        radius: 6,
        color: c,
        weight: 2,
        fillColor: c,
        fillOpacity: 0.35,
      })
        .bindPopup(
          `<b>${a.id}</b> · ${a.capability}<br/>Status: ${a.status}`,
        )
        .addTo(layer);
    }
    for (const inc of incidents) {
      L.circleMarker([inc.location.lat, inc.location.lon], {
        radius: 8,
        color: "#f87171",
        weight: 2.5,
        fillColor: "#f87171",
        fillOpacity: 0.3,
      })
        .bindPopup(
          `<b>${inc.incident_id}</b><br/>${inc.incident_type}<br/>Status: ${inc.current_status}`,
        )
        .addTo(layer);
    }

    /* Route for the selected incident: ambulance → incident → hospital. */
    const sel = incidents.find((i) => i.incident_id === selectedId);
    if (sel && fleet) {
      const amb = fleet.ambulances.find(
        (a) => a.id === sel.selected_ambulance?.id,
      );
      const hosp = fleet.hospitals.find(
        (h) => h.id === sel.selected_hospital?.id,
      );
      const pts: Array<[number, number]> = [];
      if (amb) pts.push([amb.lat, amb.lon]);
      pts.push([sel.location.lat, sel.location.lon]);
      if (hosp) pts.push([hosp.lat, hosp.lon]);
      if (pts.length >= 2) {
        L.polyline(pts, {
          color: "#7dd3fc",
          weight: 2,
          dashArray: "6 6",
          opacity: 0.9,
        }).addTo(layer);
      }
    }
  }, [incidents, fleet, selectedId]);

  return <div ref={divRef} className="absolute inset-0 z-0" />;
}
