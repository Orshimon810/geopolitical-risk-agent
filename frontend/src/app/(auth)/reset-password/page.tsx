"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

const PW_HINTS = [
  { label: "8+ characters",        test: (pw: string) => pw.length >= 8 },
  { label: "one uppercase letter", test: (pw: string) => /[A-Z]/.test(pw) },
  { label: "one number",           test: (pw: string) => /\d/.test(pw) },
];

function validatePassword(pw: string): string | null {
  if (pw.length < 8) return "Password must be at least 8 characters";
  if (!/[A-Z]/.test(pw)) return "Password must contain at least one uppercase letter";
  if (!/\d/.test(pw)) return "Password must contain at least one number";
  return null;
}

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!token) { setError("Invalid reset link. Please request a new one."); return; }
    const pwError = validatePassword(password);
    if (pwError) { setError(pwError); return; }
    if (password !== confirm) { setError("Passwords do not match."); return; }
    setLoading(true);
    try {
      await api.resetPassword(token, password);
      setSuccess(true);
      setTimeout(() => router.push("/login"), 2500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Reset failed. The link may have expired.");
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="space-y-3">
        <div className="rounded border border-rose-800/40 bg-rose-950/20 px-3 py-2 text-[11px] text-rose-400 data-mono">
          ✗ Invalid or missing reset token. Please request a new link.
        </div>
        <Link href="/forgot-password">
          <Button variant="outline" className="w-full">Request New Link</Button>
        </Link>
      </div>
    );
  }

  if (success) {
    return (
      <div className="rounded border border-emerald-800/40 bg-emerald-950/20 px-3 py-3 text-[11px] text-emerald-400 data-mono space-y-1">
        <p>✓ Password updated successfully.</p>
        <p className="text-slate-500">Redirecting to sign in…</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="password" className="text-[10px] uppercase tracking-widest text-slate-600 data-mono">
          New Password
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

      <div className="space-y-1.5">
        <Label htmlFor="confirm" className="text-[10px] uppercase tracking-widest text-slate-600 data-mono">
          Confirm Password
        </Label>
        <Input
          id="confirm"
          type="password"
          placeholder="Repeat your password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
          autoComplete="new-password"
        />
      </div>

      {error && (
        <div className="rounded border border-rose-800/40 bg-rose-950/20 px-3 py-2 text-[11px] text-rose-400 data-mono flex items-center gap-2">
          <span>✗</span> {error}
        </div>
      )}

      <Button type="submit" className="w-full gap-2" disabled={loading}>
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <>
            <ChevronRight className="h-4 w-4" />
            Set New Password
          </>
        )}
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="terminal-window">
      <div className="terminal-titlebar">
        <div className="terminal-dot bg-rose-500/70" />
        <div className="terminal-dot bg-amber-500/70" />
        <div className="terminal-dot bg-emerald-500/70" />
        <span className="ml-2 flex-1 text-[10px] text-slate-600 data-mono">georisk-auth — set-password</span>
      </div>
      <div className="px-6 pt-6 pb-4 border-b border-slate-800">
        <h1 className="text-sm font-bold text-slate-100 tracking-tight data-mono mb-0.5">Set New Password</h1>
        <p className="text-[11px] text-slate-500">Choose a strong password for your account.</p>
      </div>
      <div className="px-6 py-5">
        <Suspense fallback={<div className="text-[11px] text-slate-500 data-mono">Loading…</div>}>
          <ResetPasswordForm />
        </Suspense>
      </div>
    </div>
  );
}
