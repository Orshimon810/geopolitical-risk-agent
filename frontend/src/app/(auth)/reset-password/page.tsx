"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { TrendingUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { api } from "@/lib/api";

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

  const pwHints = [
    { label: "8+ characters", ok: password.length >= 8 },
    { label: "One uppercase letter", ok: /[A-Z]/.test(password) },
    { label: "One number", ok: /\d/.test(password) },
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!token) {
      setError("Invalid reset link. Please request a new one.");
      return;
    }
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
      <div className="space-y-4">
        <div className="rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-xs text-rose-400">
          Invalid or missing reset token. Please request a new link.
        </div>
        <Link href="/forgot-password">
          <Button variant="outline" className="w-full">Request New Link</Button>
        </Link>
      </div>
    );
  }

  if (success) {
    return (
      <div className="rounded-md border border-emerald-800 bg-emerald-950/40 px-3 py-3 text-xs text-emerald-400">
        Password updated successfully. Redirecting to sign in…
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="password">New Password</Label>
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
          <ul className="space-y-0.5 mt-1">
            {pwHints.map(({ label, ok }) => (
              <li
                key={label}
                className={`text-xs flex items-center gap-1.5 ${ok ? "text-emerald-400" : "text-slate-500"}`}
              >
                <span>{ok ? "✓" : "○"}</span>
                {label}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="confirm">Confirm Password</Label>
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
        <div className="rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-xs text-rose-400">
          {error}
        </div>
      )}

      <Button type="submit" className="w-full" disabled={loading}>
        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Set New Password"}
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <Card className="border-slate-800 bg-slate-900/80 backdrop-blur">
      <CardHeader className="pb-2 text-center">
        <div className="flex justify-center mb-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600">
            <TrendingUp className="h-5 w-5 text-white" />
          </div>
        </div>
        <h1 className="text-xl font-bold text-slate-50">Set New Password</h1>
        <p className="text-xs text-slate-500 mt-0.5">Choose a strong password for your account</p>
      </CardHeader>
      <CardContent className="space-y-4 pt-4">
        <Suspense fallback={<div className="text-xs text-slate-500 text-center">Loading…</div>}>
          <ResetPasswordForm />
        </Suspense>
      </CardContent>
    </Card>
  );
}
