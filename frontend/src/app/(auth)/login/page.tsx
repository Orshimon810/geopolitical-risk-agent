"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2, ChevronRight, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/context/AuthContext";

function LoginForm() {
  const { login } = useAuth();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const sessionExpired = searchParams.get("reason") === "session_expired";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="terminal-window">
      {/* Terminal chrome */}
      <div className="terminal-titlebar">
        <div className="terminal-dot bg-rose-500/70" />
        <div className="terminal-dot bg-amber-500/70" />
        <div className="terminal-dot bg-emerald-500/70" />
        <span className="ml-2 flex-1 text-[10px] text-slate-600 data-mono">georisk-auth — bash</span>
        <span className="text-[10px] text-slate-700 data-mono">v1.0</span>
      </div>

      {/* Brand header */}
      <div className="px-6 pt-6 pb-5 border-b border-slate-800">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-amber-600/30 bg-amber-500/8">
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5 text-amber-400" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418" />
            </svg>
          </div>
          <div>
            <h1 className="text-sm font-bold text-slate-100 tracking-tight data-mono">
              GeoRisk<span className="text-amber-400">_</span>AI
            </h1>
            <p className="text-[10px] text-slate-600 data-mono uppercase tracking-widest">
              Intelligence Platform
            </p>
          </div>
        </div>
        <p className="text-[11px] text-slate-500 leading-relaxed">
          Institutional-grade geopolitical risk analysis for investment professionals.
        </p>
      </div>

      {/* Form */}
      <div className="px-6 py-5 space-y-4">
        {sessionExpired && (
          <div className="rounded border border-amber-800/40 bg-amber-950/20 px-3 py-2.5 text-[11px] text-amber-500 data-mono flex items-center gap-2">
            <span className="text-amber-400">⚠</span>
            Session expired — re-authenticate to continue.
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="email" className="text-[10px] uppercase tracking-widest text-slate-600 data-mono">
              Email
            </Label>
            <Input
              id="email"
              type="email"
              placeholder="analyst@firm.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="password" className="text-[10px] uppercase tracking-widest text-slate-600 data-mono">
                Password
              </Label>
              <Link
                href="/forgot-password"
                className="text-[10px] text-amber-600/80 hover:text-amber-400 transition-colors data-mono"
              >
                forgot?
              </Link>
            </div>
            <div className="relative">
              <Input
                id="password"
                type={showPassword ? "text" : "password"}
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-amber-400 transition-colors cursor-pointer focus-visible:outline-none"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword
                  ? <EyeOff className="h-3.5 w-3.5" />
                  : <Eye    className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>

          {error && (
            <div className="rounded border border-rose-800/40 bg-rose-950/20 px-3 py-2 text-[11px] text-rose-400 data-mono flex items-center gap-2">
              <span>✗</span> {error}
            </div>
          )}

          <Button type="submit" className="w-full gap-2 mt-1" disabled={loading}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <ChevronRight className="h-4 w-4" />
                Authenticate
              </>
            )}
          </Button>
        </form>

        <div className="pt-1 border-t border-slate-800/60">
          <p className="text-center text-[11px] text-slate-600 data-mono">
            no account?{" "}
            <Link href="/register" className="text-amber-500 hover:text-amber-400 transition-colors">
              request access →
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
