"use client";

import { createContext, useContext, useState, useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

interface AuthContextType {
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    const stored = localStorage.getItem("georisk_token");
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored) setToken(stored);
    setIsLoading(false);
  }, []);

  const login = async (email: string, password: string) => {
    const data = await api.login(email, password);
    localStorage.setItem("georisk_token", data.access_token);
    setToken(data.access_token);
    router.push("/analysis");
  };

  const register = async (email: string, password: string, fullName: string) => {
    await api.register(email, password, fullName);
    await login(email, password);
  };

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      // Even if the server call fails, clear local state
    }
    localStorage.removeItem("georisk_token");
    setToken(null);
    router.push("/login");
  };

  return (
    <AuthContext.Provider value={{ token, isAuthenticated: !!token, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
