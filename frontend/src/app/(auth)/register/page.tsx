"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/context/AuthContext";

function validatePassword(pw: string): string | null {
  if (pw.length < 8) return "Password must be at least 8 characters";
  if (!/[A-Z]/.test(pw)) return "Password must contain at least one uppercase letter";
  if (!/\d/.test(pw)) return "Password must contain at least one number";
  return null;
}

const PW_HINTS = [
  { label: "8+ characters",        test: (pw: string) => pw.length >= 8 },
  { label: "one uppercase letter", test: (pw: string) => /[A-Z]/.test(pw) },
  { label: "one number",           test: (pw: string) => /\d/.test(pw) },
];

export default function RegisterPage() {
  const { register } = useAuth();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const pwError = validatePassword(password);
    if (pwError) { setError(pwError); return; }
    setLoading(true);
    try {
      await register(email, password, fullName);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
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
        <span className="ml-2 flex-1 text-[10px] text-slate-600 data-mono">georisk-auth — new-user</span>
        <span className="text-[10px] text-slate-700 data-mono">v1.0</span>
      </div>

      {/* Brand header */}
      <div className="px-6 pt-6 pb-4 border-b border-slate-800">
        <h1 className="text-sm font-bold text-slate-100 tracking-tight data-mono mb-0.5">
          Create Account
        </h1>
        <p className="text-[11px] text-slate-500">
          Access enterprise-grade geopolitical risk intelligence.
        </p>
      </div>

      {/* Form */}
      <div className="px-6 py-5 space-y-4">
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="name" className="text-[10px] uppercase tracking-widest text-slate-600 data-mono">
              Full Name
            </Label>
            <Input
              id="name"
              type="text"
              placeholder="Jane Smith"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
            />
          </div>

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
            <Label htmlFor="password" className="text-[10px] uppercase tracking-widest text-slate-600 data-mono">
              Password
            </Label>
            <Input
              id="password"
              type="password"
              placeholder="Min. 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
            {password.length > 0 && (
              <ul className="space-y-0.5 pt-1">
                {PW_HINTS.map(({ label, test }) => {
                  const ok = test(password);
                  return (
                    <li key={label} className={`text-[10px] flex items-center gap-1.5 data-mono ${ok ? "text-emerald-400" : "text-slate-600"}`}>
                      <span>{ok ? "✓" : "○"}</span>
                      {label}
                    </li>
                  );
                })}
              </ul>
            )}
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
                Create Account
              </>
            )}
          </Button>
        </form>

        <div className="pt-1 border-t border-slate-800/60">
          <p className="text-center text-[11px] text-slate-600 data-mono">
            have an account?{" "}
            <Link href="/login" className="text-amber-500 hover:text-amber-400 transition-colors">
              sign in →
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
