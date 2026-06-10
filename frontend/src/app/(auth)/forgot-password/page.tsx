"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.forgotPassword(email);
      setSubmitted(true);
    } catch {
      setError("Something went wrong. Please try again.");
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
        <span className="ml-2 flex-1 text-[10px] text-slate-600 data-mono">georisk-auth — reset-password</span>
      </div>

      {/* Header */}
      <div className="px-6 pt-6 pb-4 border-b border-slate-800">
        <h1 className="text-sm font-bold text-slate-100 tracking-tight data-mono mb-0.5">
          Reset Password
        </h1>
        <p className="text-[11px] text-slate-500">
          Enter your email to receive a secure reset link.
        </p>
      </div>

      {/* Content */}
      <div className="px-6 py-5 space-y-4">
        {submitted ? (
          <div className="space-y-4">
            <div className="rounded border border-emerald-800/40 bg-emerald-950/20 px-3 py-3 text-[11px] text-emerald-400 data-mono space-y-1">
              <p>✓ Reset link sent</p>
              <p className="text-slate-500">
                If an account exists for <span className="text-slate-300">{email}</span>, check your inbox.
              </p>
            </div>
            <Link href="/login">
              <Button variant="outline" className="w-full">
                ← Back to Sign In
              </Button>
            </Link>
          </div>
        ) : (
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
                  Send Reset Link
                </>
              )}
            </Button>

            <div className="pt-1 border-t border-slate-800/60">
              <p className="text-center text-[11px] text-slate-600 data-mono">
                <Link href="/login" className="text-amber-500 hover:text-amber-400 transition-colors">
                  ← back to sign in
                </Link>
              </p>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
