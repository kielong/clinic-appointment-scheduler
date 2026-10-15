"use client";

import { useState } from "react";
import Link from "next/link";
import { Calendar, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, ApiError, type Appointment, type AppointmentStatus } from "@/lib/api";
import { formatClinicDateTime, isActive, needsUpdate, STATUS_STYLES, TYPE_LABELS } from "@/lib/clinic";

export function StatusBadge({ appointment }: { appointment: Appointment }) {
  return (
    <span className="flex shrink-0 flex-col items-end gap-1">
      <span
        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
          STATUS_STYLES[appointment.status] ?? "bg-gray-100 text-gray-600"
        }`}
      >
        {appointment.status.charAt(0).toUpperCase() + appointment.status.slice(1)}
      </span>
      {needsUpdate(appointment) && (
        <span className="inline-flex items-center rounded-full bg-orange-50 px-2.5 py-0.5 text-xs font-medium text-orange-700">
          Past — needs update
        </span>
      )}
    </span>
  );
}

/** One appointment with status actions for staff. */
export function AppointmentRow({
  appointment,
  showPatient = true,
  onChanged,
}: {
  appointment: Appointment;
  showPatient?: boolean;
  onChanged: (updated: Appointment) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setStatus(status: AppointmentStatus, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.appointments.update(appointment.id, { status }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed.");
    } finally {
      setBusy(false);
    }
  }

  const label = TYPE_LABELS[appointment.appointment_type] ?? appointment.appointment_type;
  const isPast = new Date(appointment.scheduled_at).getTime() < Date.now();

  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        {showPatient ? (
          <>
            <Link
              href={`/patients/${appointment.patient_id}`}
              className="font-medium hover:text-teal-700 hover:underline"
            >
              {appointment.patient_name ?? "Unknown patient"}
            </Link>
            <p className="text-sm text-gray-500">{label}</p>
          </>
        ) : (
          <p className="font-medium">{label}</p>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            {formatClinicDateTime(appointment.scheduled_at)}
          </span>
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {appointment.duration_minutes} min
          </span>
        </div>
        {appointment.reason && (
          <p className="mt-1 truncate text-xs text-teal-700">{appointment.reason}</p>
        )}
        {appointment.notes && (
          <p className="mt-1 whitespace-pre-line text-xs text-gray-400">{appointment.notes}</p>
        )}
        {isActive(appointment) && (
          <div className="mt-2 flex gap-2">
            {isPast && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => setStatus("completed")}>
                Mark completed
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                setStatus("cancelled", `Cancel ${appointment.patient_name ?? "this patient"}'s ${label.toLowerCase()}?`)
              }
            >
              Cancel appointment
            </Button>
          </div>
        )}
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      </div>
      <StatusBadge appointment={appointment} />
    </div>
  );
}
