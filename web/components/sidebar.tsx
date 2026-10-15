"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Calendar, Home, LogOut, MessageSquare, Users } from "lucide-react";
import type { User } from "@/lib/api";
import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Dashboard", icon: Home },
  { href: "/patients", label: "Patients", icon: Users },
  { href: "/appointments", label: "Appointments", icon: Calendar },
  { href: "/chat", label: "Chat", icon: MessageSquare },
];

export function Sidebar({ user, onLogout }: { user: User; onLogout: () => void }) {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-10 flex w-56 flex-col border-r border-gray-200 bg-white">
      <div className="flex h-14 items-center border-b border-gray-200 px-4">
        <img src="/logo.svg" alt="BAPC" className="h-7" />
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {links.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-teal-50 text-teal-700"
                  : "text-gray-500 hover:bg-teal-50/50 hover:text-teal-700"
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-gray-200 p-3">
        <p className="truncate px-3 text-sm font-medium text-gray-700">{user.full_name}</p>
        <p className="truncate px-3 text-xs text-gray-400">{user.email} · Admin</p>
        <button
          onClick={onLogout}
          className="mt-2 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
