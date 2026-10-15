"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Loader2, LogOut } from "lucide-react";
import { homePathFor, useAuth } from "@/components/auth-provider";
import { Sidebar } from "@/components/sidebar";
import { Button } from "@/components/ui/button";

const PUBLIC_PATHS = ["/login", "/register"];
const PATIENT_PATHS = ["/chat"];

function allowed(path: string, role: "admin" | "patient") {
  if (role === "admin") return true;
  return PATIENT_PATHS.includes(path);
}

/**
 * Client-side routing guard. The backend enforces the same rules on every request; this only
 * keeps users on pages they can actually use.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  useEffect(() => {
    if (loading) return;
    if (!user && !isPublic) router.replace("/login");
    else if (user && isPublic) window.location.replace(homePathFor(user));
    else if (user && !allowed(pathname, user.role)) router.replace(homePathFor(user));
  }, [loading, user, isPublic, pathname, router]);

  if (isPublic && !user) {
    return <main className="min-h-screen">{children}</main>;
  }

  if (loading || !user || !allowed(pathname, user.role) || isPublic) {
    return (
      <div className="flex min-h-screen items-center justify-center text-gray-400">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (user.role === "patient") {
    return (
      <div className="flex h-screen flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 sm:px-6">
          <img src="/logo.svg" alt="BAPC" className="h-7" />
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-gray-600 sm:inline">{user.full_name}</span>
            <Button variant="outline" size="sm" onClick={logout}>
              <LogOut className="mr-1.5 h-3.5 w-3.5" />
              Sign out
            </Button>
          </div>
        </header>
        <main className="min-h-0 flex-1">{children}</main>
      </div>
    );
  }

  return (
    <>
      <Sidebar user={user} onLogout={logout} />
      <main className="ml-56 min-h-screen">{children}</main>
    </>
  );
}
