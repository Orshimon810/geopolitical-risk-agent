import type { FeedbackResponse, PortfolioHolding, TaskStatusResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("georisk_token");
}

function setToken(token: string): void {
  if (typeof window !== "undefined") localStorage.setItem("georisk_token", token);
}

function clearToken(): void {
  if (typeof window !== "undefined") localStorage.removeItem("georisk_token");
}

async function apiFetch<T>(path: string, options: RequestInit = {}, _isRetry = false): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",  // send httpOnly refresh cookie on every request
  });

  if (res.status === 401 && !path.startsWith("/auth/") && !_isRetry) {
    // Access token expired — attempt a silent refresh before giving up
    try {
      const refreshed = await apiFetch<{ access_token: string }>("/auth/refresh", {
        method: "POST",
      }, true);
      setToken(refreshed.access_token);
      // Retry the original request once with the new token
      return apiFetch<T>(path, options, true);
    } catch {
      // Refresh failed (token revoked / expired) — redirect to login with reason
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/login?reason=session_expired";
      }
      throw new Error("Session expired. Please log in again.");
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as unknown as T;
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

  analyzeQuery(query: string, includePortfolio = false) {
    return apiFetch<{ status: string; task_id: string }>("/agent/analyze", {
      method: "POST",
      body: JSON.stringify({ query, include_portfolio: includePortfolio }),
    });
  },

  getTaskStatus(taskId: string) {
    return apiFetch<TaskStatusResponse>(`/agent/tasks/${taskId}`);
  },

  approvePlan(taskId: string, subQuestions: string[]) {
    return apiFetch<{ task_id: string; status: string }>(
      `/agent/tasks/${taskId}/approve-plan`,
      { method: "POST", body: JSON.stringify({ sub_questions: subQuestions }) },
    );
  },

  getHistory(limit = 20, offset = 0): Promise<import("./types").HistoryItem[]> {
    return apiFetch<import("./types").HistoryItem[]>(
      `/agent/history?limit=${limit}&offset=${offset}`
    );
  },

  deleteAnalysis(id: string): Promise<{ deleted: boolean }> {
    return apiFetch<{ deleted: boolean }>(`/agent/history/${id}`, {
      method: "DELETE",
    });
  },

  submitFeedback(analysisId: string, score: 0 | 1, comment?: string): Promise<FeedbackResponse> {
    return apiFetch<FeedbackResponse>(`/agent/analysis/${analysisId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ score, comment: comment ?? null }),
    });
  },

  forgotPassword(email: string) {
    return apiFetch<{ message: string }>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
  },

  resetPassword(token: string, newPassword: string) {
    return apiFetch<{ message: string }>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, new_password: newPassword }),
    });
  },

  logout() {
    return apiFetch<{ message: string }>("/auth/logout", { method: "POST" });
  },

  // ── Portfolio ─────────────────────────────────────────────────────────────

  getPortfolio(): Promise<PortfolioHolding[]> {
    return apiFetch<PortfolioHolding[]>("/portfolio/holdings");
  },

  addHolding(data: {
    ticker: string;
    name: string;
    asset_type: string;
    quantity?: number | null;
    cost_basis_usd?: number | null;
  }): Promise<PortfolioHolding> {
    return apiFetch<PortfolioHolding>("/portfolio/holdings", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  updateHolding(
    id: string,
    data: { name?: string; quantity?: number | null; cost_basis_usd?: number | null }
  ): Promise<PortfolioHolding> {
    return apiFetch<PortfolioHolding>(`/portfolio/holdings/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  deleteHolding(id: string): Promise<void> {
    return apiFetch<void>(`/portfolio/holdings/${id}`, { method: "DELETE" });
  },

  searchTickers(q: string): Promise<{ ticker: string; name: string; asset_type: string }[]> {
    return apiFetch<{ ticker: string; name: string; asset_type: string }[]>(
      `/portfolio/search?q=${encodeURIComponent(q)}`
    );
  },

  getTickerQuote(ticker: string): Promise<{ ticker: string; price: number | null; currency: string }> {
    return apiFetch<{ ticker: string; price: number | null; currency: string }>(
      `/portfolio/quote?ticker=${encodeURIComponent(ticker)}`
    );
  },

  getPortfolioQuotes(tickers: string[]): Promise<{ ticker: string; price: number | null; currency: string }[]> {
    return apiFetch<{ ticker: string; price: number | null; currency: string }[]>(
      `/portfolio/quotes?tickers=${encodeURIComponent(tickers.join(","))}`
    );
  },
};
