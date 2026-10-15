"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError, type Patient, type PatientCreateInput } from "@/lib/api";

const EMPTY_FORM: PatientCreateInput = {
  name: "",
  email: "",
  phone: "",
  date_of_birth: "",
  notes: "",
};

function PatientsList({ patients }: { patients: Patient[] }) {
  return (
    <div className="space-y-3">
      {patients.map((p) => (
        <Link key={p.id} href={`/patients/${p.id}`} className="block">
          <Card className="hover:shadow-md transition-shadow cursor-pointer">
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <h3 className="font-medium">{p.name}</h3>
                <p className="text-sm text-gray-500">{p.email}</p>
                {p.phone && <p className="text-xs text-gray-400 mt-0.5">{p.phone}</p>}
              </div>
              {p.has_account && (
                <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
                  Portal account
                </span>
              )}
            </CardContent>
          </Card>
        </Link>
      ))}
      {patients.length === 0 && (
        <p className="text-gray-400 text-sm">No patients found.</p>
      )}
    </div>
  );
}

function PatientsPageContent() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<PatientCreateInput>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  function loadPatients(q = query) {
    api.patients.list({ query: q.trim() || undefined }).then(setPatients);
  }

  useEffect(() => {
    const t = setTimeout(() => loadPatients(query), 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.patients.create({
        name: form.name.trim(),
        email: form.email.trim(),
        phone: form.phone?.trim() || null,
        date_of_birth: form.date_of_birth || null,
        notes: form.notes?.trim() || null,
      });
      loadPatients();
      setShowForm(false);
      setForm(EMPTY_FORM);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not add patient. Please check the form and try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  function handleCancel() {
    setShowForm(false);
    setForm(EMPTY_FORM);
    setError(null);
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold tracking-tight">Patients</h1>
        <Button onClick={() => (showForm ? handleCancel() : setShowForm(true))}>
          {showForm ? "Cancel" : "Add Patient"}
        </Button>
      </div>

      {showForm && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="text-base">New Patient</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Full name *</label>
                <input
                  required
                  type="text"
                  placeholder="Maria Garcia"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Email *</label>
                <input
                  required
                  type="email"
                  placeholder="maria@example.com"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Phone</label>
                <input
                  type="tel"
                  placeholder="+1-956-555-0100"
                  value={form.phone ?? ""}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Date of birth</label>
                <input
                  type="date"
                  max={new Date().toISOString().slice(0, 10)}
                  value={form.date_of_birth ?? ""}
                  onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm"
                />
              </div>

              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">Notes</label>
                <textarea
                  rows={2}
                  placeholder="Allergies, preferred contact method, etc."
                  value={form.notes ?? ""}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm resize-none"
                />
              </div>

              {error && (
                <p className="sm:col-span-2 text-sm text-red-600">{error}</p>
              )}

              <div className="sm:col-span-2 flex justify-end">
                <Button type="submit" disabled={submitting}>
                  {submitting ? "Adding…" : "Add Patient"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search by name or email…"
        aria-label="Search patients"
        className="mb-4 w-full max-w-sm rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
      />
      <PatientsList patients={patients} />
    </div>
  );
}

export default function PatientsPage() {
  return (
    <Suspense>
      <PatientsPageContent />
    </Suspense>
  );
}
