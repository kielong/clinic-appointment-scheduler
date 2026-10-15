"use client";

import { useEffect, useMemo, useState } from "react";
import { AppointmentRow } from "@/components/appointment-row";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError, type Appointment, type AppointmentCreateInput, type Patient } from "@/lib/api";
import { isUpcoming, needsUpdate, TYPE_LABELS } from "@/lib/clinic";

const EMPTY_FORM: AppointmentCreateInput = {
  patient_id: "",
  appointment_type: "",
  scheduled_at: "",
  duration_minutes: 30,
  reason: "",
  notes: "",
};

const FILTERS = {
  upcoming: { label: "Upcoming", test: isUpcoming },
  attention: { label: "Needs update", test: needsUpdate },
  completed: { label: "Completed", test: (a: Appointment) => a.status === "completed" },
  cancelled: { label: "Cancelled", test: (a: Appointment) => a.status === "cancelled" },
  all: { label: "All", test: () => true },
} as const;

type FilterKey = keyof typeof FILTERS;

export default function AppointmentsPage() {
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [filter, setFilter] = useState<FilterKey>("upcoming");

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<AppointmentCreateInput>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.appointments.list().then(setAppointments);
    api.patients.list().then(setPatients);
  }, []);

  const visible = useMemo(() => {
    const rows = appointments.filter(FILTERS[filter].test);
    // Upcoming reads soonest-first; history reads newest-first.
    return filter === "upcoming" ? rows : [...rows].reverse();
  }, [appointments, filter]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.appointments.create({
        ...form,
        // datetime-local gives clinic wall time without an offset; the API interprets it in
        // the clinic's timezone regardless of the browser's timezone.
        scheduled_at: form.scheduled_at,
        duration_minutes: Number(form.duration_minutes) || 30,
        reason: form.reason?.trim() || null,
        notes: form.notes?.trim() || null,
      });
      setAppointments(await api.appointments.list());
      setShowForm(false);
      setForm(EMPTY_FORM);
      setFilter("upcoming");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not schedule the appointment.");
    } finally {
      setSubmitting(false);
    }
  }

  function handleCancel() {
    setShowForm(false);
    setForm(EMPTY_FORM);
    setError(null);
  }

  function replace(updated: Appointment) {
    setAppointments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold tracking-tight">Appointments</h1>
        <Button onClick={() => (showForm ? handleCancel() : setShowForm(true))}>
          {showForm ? "Cancel" : "Schedule Appointment"}
        </Button>
      </div>

      {showForm && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="text-base">New Appointment</CardTitle>
            <p className="text-sm text-gray-500">
              Times are clinic time (Central). Clinic hours: Monday–Friday, 8 AM–5 PM.
            </p>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">Patient *</label>
                <select
                  required
                  value={form.patient_id}
                  onChange={(e) => setForm({ ...form, patient_id: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                >
                  <option value="">Select patient…</option>
                  {patients.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.email})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Appointment Type *</label>
                <select
                  required
                  value={form.appointment_type}
                  onChange={(e) => setForm({ ...form, appointment_type: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                >
                  <option value="">Select type…</option>
                  {Object.entries(TYPE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Date &amp; Time *</label>
                <input
                  required
                  type="datetime-local"
                  step={900}
                  value={form.scheduled_at}
                  onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Duration (min)</label>
                <input
                  type="number"
                  min={5}
                  max={240}
                  step={5}
                  value={form.duration_minutes}
                  onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">Reason for visit</label>
                <input
                  type="text"
                  maxLength={512}
                  placeholder="e.g. Annual physical, flu symptoms"
                  value={form.reason ?? ""}
                  onChange={(e) => setForm({ ...form, reason: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              {error && <p className="sm:col-span-2 text-sm text-red-600">{error}</p>}

              <div className="sm:col-span-2 flex justify-end">
                <Button type="submit" disabled={submitting}>
                  {submitting ? "Scheduling…" : "Confirm Schedule"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <div className="mb-4 flex flex-wrap gap-2">
        {(Object.keys(FILTERS) as FilterKey[]).map((key) => {
          const count = appointments.filter(FILTERS[key].test).length;
          return (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded-full px-3 py-1 text-sm transition-colors ${
                filter === key
                  ? "bg-teal-600 text-white"
                  : "border border-gray-200 bg-white text-gray-600 hover:border-teal-300"
              }`}
            >
              {FILTERS[key].label} ({count})
            </button>
          );
        })}
      </div>

      <div className="space-y-3">
        {visible.length === 0 && <p className="text-sm text-gray-400">No appointments found.</p>}
        {visible.map((appointment) => (
          <Card key={appointment.id}>
            <CardContent className="p-4">
              <AppointmentRow appointment={appointment} onChanged={replace} />
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
