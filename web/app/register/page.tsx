"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthCard, Field } from "@/components/auth-card";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";

const EMPTY = { name: "", email: "", date_of_birth: "", phone: "", password: "", confirm: "" };

export default function RegisterPage() {
  const { register } = useAuth();
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (field: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [field]: e.target.value });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (form.password !== form.confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await register({
        name: form.name.trim(),
        email: form.email.trim(),
        date_of_birth: form.date_of_birth,
        phone: form.phone.trim() || null,
        password: form.password,
      });
      window.location.replace("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create your account.");
      setSubmitting(false);
    }
  }

  return (
    <AuthCard
      title="Create your patient account"
      subtitle="Book and manage your visits by chatting with our scheduling assistant."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-indigo-600 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Full name" required autoComplete="name" value={form.name} onChange={set("name")} />
        <Field
          label="Email"
          type="email"
          required
          autoComplete="email"
          value={form.email}
          onChange={set("email")}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Date of birth"
            type="date"
            required
            max={new Date().toISOString().slice(0, 10)}
            value={form.date_of_birth}
            onChange={set("date_of_birth")}
          />
          <Field label="Phone (optional)" type="tel" autoComplete="tel" value={form.phone} onChange={set("phone")} />
        </div>
        <Field
          label="Password (8+ characters)"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          value={form.password}
          onChange={set("password")}
        />
        <Field
          label="Confirm password"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          value={form.confirm}
          onChange={set("confirm")}
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthCard>
  );
}
