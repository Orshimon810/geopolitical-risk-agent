"use client";

import { useState } from "react";
import Link from "next/link";
import { TrendingUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useAuth } from "@/context/AuthContext";

function validatePassword(pw: string): string | null {
  if (pw.length < 8) return "Password must be at least 8 characters";
  if (!/[A-Z]/.test(pw)) return "Password must contain at least one uppercase letter";
  if (!/\d/.test(pw)) return "Password must contain at least one number";
  return null;
}

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
    if (pwError) {
      setError(pwError);
      return;
    }
    setLoading(true);
    try {
      await register(email, password, fullName);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  const pwHints = [
    { label: "8+ characters", ok: password.length >= 8 },
    { label: "One uppercase letter", ok: /[A-Z]/.test(password) },
    { label: "One number", ok: /\d/.test(password) },
  ];

  return (
    <Card className="border-slate-800 bg-slate-900/80 backdrop-blur">
      <CardHeader className="pb-2 text-center">
        <div className="flex justify-center mb-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600">
            <TrendingUp className="h-5 w-5 text-white" />
          </div>
        </div>
        <h1 className="text-xl font-bold text-slate-50">Create Account</h1>
        <p className="text-xs text-slate-500 mt-0.5">Access enterprise-grade risk intelligence</p>
      </CardHeader>

      <CardContent className="space-y-4 pt-4">
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="name">Full Name</Label>
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
            <Label htmlFor="email">Email</Label>
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
            <Label htmlFor="password">Password</Label>
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

          {error && (
            <div className="rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-xs text-rose-400">
              {error}
            </div>
          )}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create Account"}
          </Button>
        </form>

        <p className="text-center text-xs text-slate-600">
          Already have an account?{" "}
          <Link href="/login" className="text-blue-400 hover:text-blue-300">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
