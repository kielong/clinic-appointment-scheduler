"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AppointmentRow } from "@/components/appointment-row";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError, type Appointment, type Patient } from "@/lib/api";

function formatDob(iso: string) {
  // A date of birth has no time component; format it without timezone shifting.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export default function PatientDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [patient, setPatient] = useState<Patient | null>(null);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!id) return;
    api.patients
      .get(id)
      .then(setPatient)
      .catch((err) => {
        if (err instanceof ApiError && (err.status === 404 || err.status === 422)) setNotFound(true);
      });
    api.appointments
      .list({ patient_id: id })
      .then((rows) => setAppointments([...rows].reverse()))
      .catch(() => setAppointments([]));
  }, [id]);

  if (notFound) {
    return (
      <div className="p-8 text-gray-500">
        Patient not found.{" "}
        <Link href="/patients" className="text-teal-700 hover:underline">
          Back to patients
        </Link>
      </div>
    );
  }

  if (!patient) {
    return <div className="p-8 text-gray-400">Loading...</div>;
  }

  function replace(updated: Appointment) {
    setAppointments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
  }

  return (
    <div className="p-8 max-w-2xl space-y-6">
      <Link href="/patients" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-teal-700">
        <ArrowLeft className="h-3.5 w-3.5" /> Patients
      </Link>
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold tracking-tight">{patient.name}</h1>
        {patient.has_account && (
          <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
            Portal account
          </span>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Patient Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Row label="Email" value={patient.email} />
          <Row label="Phone" value={patient.phone || "N/A"} />
          <Row label="Date of birth" value={patient.date_of_birth ? formatDob(patient.date_of_birth) : "N/A"} />
          <Row label="Registered" value={new Date(patient.created_at).toLocaleDateString()} />
          {patient.notes && <Row label="Notes" value={patient.notes} />}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Appointment History</CardTitle>
        </CardHeader>
        <CardContent>
          {appointments.length === 0 ? (
            <p className="text-sm text-gray-400">No appointments scheduled.</p>
          ) : (
            <div className="space-y-5">
              {appointments.map((appointment) => (
                <AppointmentRow
                  key={appointment.id}
                  appointment={appointment}
                  showPatient={false}
                  onChanged={replace}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-4">
      <span className="w-28 shrink-0 font-medium text-gray-500">{label}</span>
      <span>{value}</span>
    </div>
  );
}
