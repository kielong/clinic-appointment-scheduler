import type { Appointment } from "@/lib/api";

/** Appointments are booked and displayed in the clinic's own timezone. */
export const CLINIC_TIME_ZONE = "America/Chicago";

export const TYPE_LABELS: Record<string, string> = {
  checkup: "Checkup",
  follow_up: "Follow-up",
  sick_visit: "Sick Visit",
  physical: "Physical",
  vaccination: "Vaccination",
  new_patient: "New Patient",
};

export const STATUS_STYLES: Record<string, string> = {
  scheduled: "bg-blue-50 text-blue-700",
  rescheduled: "bg-yellow-50 text-yellow-700",
  completed: "bg-green-50 text-green-700",
  cancelled: "bg-gray-100 text-gray-600",
};

export function formatClinicDateTime(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    timeZone: CLINIC_TIME_ZONE,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export function isActive(a: Appointment) {
  return a.status === "scheduled" || a.status === "rescheduled";
}

/** Active appointment whose time has passed — staff should mark it completed or cancelled. */
export function needsUpdate(a: Appointment) {
  return isActive(a) && new Date(a.scheduled_at).getTime() < Date.now();
}

export function isUpcoming(a: Appointment) {
  return isActive(a) && new Date(a.scheduled_at).getTime() >= Date.now();
}

/** "YYYY-MM-DD" for a date in the clinic's timezone. */
export function clinicDateKey(date: Date) {
  return date.toLocaleDateString("en-CA", { timeZone: CLINIC_TIME_ZONE });
}
