"use client";

import { useAuth } from "@/components/auth-provider";
import { ChatInterface } from "@/components/chat-interface";

export default function ChatPage() {
  const { user } = useAuth();
  const isPatient = user?.role === "patient";

  return (
    // Patients get a header bar above this page; admins get the full viewport height.
    <div className={`flex min-h-0 flex-col ${isPatient ? "h-full" : "h-screen"}`}>
      <div className="border-b border-gray-200 bg-white px-4 py-4 sm:px-8">
        <h1 className="text-lg font-semibold">
          {isPatient ? "Schedule an appointment" : "Scheduling assistant"}
        </h1>
        <p className="text-sm text-gray-500">
          {isPatient
            ? "Tell us the type of visit and when you'd like to come in. Clinic hours are Monday–Friday, 8 AM–5 PM (Central)."
            : "Look up patients and book, reschedule, or cancel appointments. Every change asks for confirmation first."}
        </p>
      </div>
      <div className="min-h-0 flex-1">
        <ChatInterface />
      </div>
    </div>
  );
}
