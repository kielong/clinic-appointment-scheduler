"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertCircle, Calendar, CalendarCheck, Users } from "lucide-react";
import { AppointmentRow } from "@/components/appointment-row";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type Appointment } from "@/lib/api";
import { clinicDateKey, isUpcoming, needsUpdate } from "@/lib/clinic";

function StatCard({
  title,
  value,
  icon: Icon,
  tone = "text-gray-500",
}: {
  title: string;
  value: number | null;
  icon: typeof Users;
  tone?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className={`h-4 w-4 ${tone}`} />
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-bold">{value ?? "..."}</div>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const [patientCount, setPatientCount] = useState<number | null>(null);
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);

  useEffect(() => {
    api.patients.count().then((d) => setPatientCount(d.count));
    api.appointments.list().then(setAppointments);
  }, []);

  const upcoming = useMemo(() => (appointments ?? []).filter(isUpcoming), [appointments]);
  const stale = useMemo(() => (appointments ?? []).filter(needsUpdate), [appointments]);
  const today = clinicDateKey(new Date());
  const todayCount = upcoming.filter((a) => clinicDateKey(new Date(a.scheduled_at)) === today).length;

  function replace(updated: Appointment) {
    setAppointments((prev) => prev?.map((a) => (a.id === updated.id ? updated : a)) ?? prev);
  }

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold tracking-tight mb-2">Dashboard</h1>
      <p className="text-sm text-gray-500 mb-6">BAPC Clinic</p>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard title="Patients" value={patientCount} icon={Users} />
        <StatCard title="Upcoming Appointments" value={appointments ? upcoming.length : null} icon={Calendar} />
        <StatCard title="Today" value={appointments ? todayCount : null} icon={CalendarCheck} />
        <StatCard
          title="Past Visits Needing Update"
          value={appointments ? stale.length : null}
          icon={AlertCircle}
          tone={stale.length ? "text-orange-500" : "text-gray-500"}
        />
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Next appointments</CardTitle>
            <Link href="/appointments" className="text-sm text-teal-700 hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent className="space-y-4">
            {upcoming.length === 0 && <p className="text-sm text-gray-400">Nothing scheduled.</p>}
            {upcoming.slice(0, 6).map((a) => (
              <AppointmentRow key={a.id} appointment={a} onChanged={replace} />
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Past visits still marked scheduled</CardTitle>
            <p className="text-sm text-gray-500">
              Mark each as completed or cancelled so the records stay accurate.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {stale.length === 0 && <p className="text-sm text-gray-400">All caught up.</p>}
            {stale.map((a) => (
              <AppointmentRow key={a.id} appointment={a} onChanged={replace} />
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
