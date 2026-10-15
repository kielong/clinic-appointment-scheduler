const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      const first = body.detail[0];
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "";
      return field ? `${field}: ${first.msg}` : first.msg;
    }
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    // Session expired: send the user back to sign in.
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  if (!res.ok) {
    throw new ApiError(res.status, await errorMessage(res));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

export type Role = "admin" | "patient";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  patient_id: string | null;
}

export interface RegisterInput {
  name: string;
  email: string;
  password: string;
  date_of_birth: string;
  phone?: string | null;
}

export interface Patient {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  date_of_birth: string | null;
  notes: string | null;
  created_at: string;
  has_account: boolean;
}

export interface PatientCreateInput {
  name: string;
  email: string;
  phone?: string | null;
  date_of_birth?: string | null;
  notes?: string | null;
}

export interface PatientUpdateInput {
  name?: string;
  email?: string;
  phone?: string | null;
  date_of_birth?: string | null;
  notes?: string | null;
}

export type AppointmentStatus = "scheduled" | "rescheduled" | "completed" | "cancelled";

export interface Appointment {
  id: string;
  patient_id: string;
  patient_name: string | null;
  appointment_type: string;
  scheduled_at: string;
  duration_minutes: number;
  status: AppointmentStatus;
  reason: string | null;
  notes: string | null;
  created_at: string;
}

export interface AppointmentCreateInput {
  patient_id: string;
  appointment_type: string;
  /** Clinic-local wall time, e.g. "2026-10-20T10:30" (no offset). */
  scheduled_at: string;
  duration_minutes?: number;
  reason?: string | null;
  notes?: string | null;
}

export interface AppointmentUpdateInput {
  scheduled_at?: string;
  status?: AppointmentStatus;
  duration_minutes?: number;
  appointment_type?: string;
  reason?: string | null;
  notes?: string | null;
}

export const api = {
  auth: {
    me: () => apiFetch<User>("/auth/me"),
    login: (email: string, password: string) =>
      apiFetch<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
    register: (body: RegisterInput) =>
      apiFetch<User>("/auth/register", { method: "POST", body: JSON.stringify(body) }),
    logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
  },
  patients: {
    list: (params?: { query?: string }) => {
      const query = new URLSearchParams();
      if (params?.query) query.set("query", params.query);
      const qs = query.toString();
      return apiFetch<Patient[]>(`/patients${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => apiFetch<Patient>(`/patients/${id}`),
    count: () => apiFetch<{ count: number }>("/patients/count"),
    create: (body: PatientCreateInput) =>
      apiFetch<Patient>("/patients", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: PatientUpdateInput) =>
      apiFetch<Patient>(`/patients/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    delete: (id: string) => apiFetch<void>(`/patients/${id}`, { method: "DELETE" }),
  },
  appointments: {
    list: (params?: { patient_id?: string; status?: string }) => {
      const query = new URLSearchParams();
      if (params?.patient_id) query.set("patient_id", params.patient_id);
      if (params?.status) query.set("status", params.status);
      const qs = query.toString();
      return apiFetch<Appointment[]>(`/appointments${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => apiFetch<Appointment>(`/appointments/${id}`),
    create: (body: AppointmentCreateInput) =>
      apiFetch<Appointment>("/appointments", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: AppointmentUpdateInput) =>
      apiFetch<Appointment>(`/appointments/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    delete: (id: string) => apiFetch<void>(`/appointments/${id}`, { method: "DELETE" }),
  },
};
