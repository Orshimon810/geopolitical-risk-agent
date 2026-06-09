import type { TaskStatusResponse, AnalysisResult } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("georisk_token");
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  login(email: string, password: string) {
    return apiFetch<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  register(email: string, password: string, full_name: string) {
    return apiFetch<{ id: string; email: string; full_name: string; tier: string; is_active: boolean }>(
      "/auth/register",
      { method: "POST", body: JSON.stringify({ email, password, full_name }) }
    );
  },

  analyzeQuery(query: string) {
    return apiFetch<{ status: string; task_id: string }>("/agent/analyze", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
  },

  getTaskStatus(taskId: string) {
    return apiFetch<TaskStatusResponse>(`/agent/tasks/${taskId}`);
  },

  getHistory(limit = 20, offset = 0): Promise<import("./types").HistoryItem[]> {
    return apiFetch<import("./types").HistoryItem[]>(
      `/agent/history?limit=${limit}&offset=${offset}`
    );
  },
};
