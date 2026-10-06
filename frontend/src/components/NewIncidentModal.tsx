import { useState, type ChangeEvent, type FormEvent } from "react";
import { api } from "../api";
import type { IncidentState } from "../types";
import { INCIDENT_TYPE_LABELS } from "../types";
import AlertBanner from "./AlertBanner";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (incident: IncidentState) => void;
}

const INITIAL_FORM = {
  incident_type: "cardiac_arrest",
  lat: "18.5204",
  lon: "73.8567",
  address: "",
  patient_count: "1",
  symptoms: "",
  breathing_status: "normal",
  bleeding_status: "none",
  family_contact: "",
};

const DEMO_FILL = {
  incident_type: "cardiac_arrest",
  lat: "18.5204",
  lon: "73.8567",
  address: "FC Road, Shivajinagar, Pune",
  patient_count: "1",
  symptoms: "chest pain, collapsed, unresponsive",
  breathing_status: "not_breathing",
  bleeding_status: "none",
  family_contact: "+91 98220 12345",
};

export default function NewIncidentModal({ open, onClose, onCreated }: Props) {
  const [form, setForm] = useState({ ...INITIAL_FORM });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const set =
    (key: keyof typeof form) =>
    (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const lat = Number(form.lat);
    const lon = Number(form.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      setError("Latitude and longitude must be valid numbers.");
      return;
    }
    const patient_count = Math.max(
      1,
      Math.floor(Number(form.patient_count) || 1),
    );
    setSubmitting(true);
    try {
      const incident = await api.createIncident({
        incident_type: form.incident_type,
        lat,
        lon,
        address: form.address.trim(),
        patient_count,
        symptoms: form.symptoms
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        breathing_status: form.breathing_status,
        bleeding_status: form.bleeding_status,
        family_contact: form.family_contact.trim(),
      });
      setForm({ ...INITIAL_FORM });
      onCreated(incident);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create incident");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/70"
        onClick={() => !submitting && onClose()}
      />
      <div className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-relay-border bg-relay-panel p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-slate-100">New incident</h2>
            <p className="text-xs text-slate-500">
              Dispatches the full 6-agent pipeline.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setForm({ ...DEMO_FILL })}
            className="shrink-0 rounded-md border border-sky-500/50 bg-sky-500/10 px-2.5 py-1.5 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-500/20"
          >
            Fill cardiac-arrest demo
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Incident type
            </label>
            <select
              className="relay-input"
              value={form.incident_type}
              onChange={set("incident_type")}
            >
              {Object.entries(INCIDENT_TYPE_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">
                Latitude
              </label>
              <input
                className="relay-input font-mono"
                inputMode="decimal"
                value={form.lat}
                onChange={set("lat")}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">
                Longitude
              </label>
              <input
                className="relay-input font-mono"
                inputMode="decimal"
                value={form.lon}
                onChange={set("lon")}
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Address
            </label>
            <input
              className="relay-input"
              placeholder="e.g. FC Road, Shivajinagar, Pune"
              value={form.address}
              onChange={set("address")}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Patient count
            </label>
            <input
              className="relay-input"
              type="number"
              min={1}
              value={form.patient_count}
              onChange={set("patient_count")}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Symptoms (comma-separated)
            </label>
            <input
              className="relay-input"
              placeholder="e.g. chest pain, collapsed, unresponsive"
              value={form.symptoms}
              onChange={set("symptoms")}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">
                Breathing
              </label>
              <select
                className="relay-input"
                value={form.breathing_status}
                onChange={set("breathing_status")}
              >
                <option value="normal">Normal</option>
                <option value="labored">Labored</option>
                <option value="not_breathing">Not breathing</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">
                Bleeding
              </label>
              <select
                className="relay-input"
                value={form.bleeding_status}
                onChange={set("bleeding_status")}
              >
                <option value="none">None</option>
                <option value="minor">Minor</option>
                <option value="severe">Severe</option>
              </select>
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Family contact
            </label>
            <input
              className="relay-input font-mono"
              placeholder="+91 …"
              value={form.family_contact}
              onChange={set("family_contact")}
            />
          </div>

          {error && (
            <AlertBanner variant="critical" title="Could not create incident" detail={error} />
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded-md border border-relay-border px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-relay-panel2 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-500 disabled:opacity-50"
            >
              {submitting ? "Dispatching…" : "Dispatch agents"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
