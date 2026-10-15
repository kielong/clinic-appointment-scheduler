"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, type RegisterInput, type User } from "@/lib/api";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (body: RegisterInput) => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const CHAT_STORAGE_PREFIX = "bapc-chat:";

function clearChatHistory() {
  try {
    Object.keys(sessionStorage)
      .filter((k) => k.startsWith(CHAT_STORAGE_PREFIX))
      .forEach((k) => sessionStorage.removeItem(k));
  } catch {
    // storage unavailable
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.auth
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await api.auth.login(email, password);
    clearChatHistory();
    setUser(u);
    return u;
  }, []);

  const register = useCallback(async (body: RegisterInput) => {
    const u = await api.auth.register(body);
    clearChatHistory();
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      clearChatHistory();
      setUser(null);
      window.location.href = "/login";
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export function homePathFor(user: User) {
  return user.role === "admin" ? "/" : "/chat";
}
