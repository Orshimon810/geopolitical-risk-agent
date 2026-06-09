"use client";

import { useState, useCallback, useRef } from "react";
import { Send, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { AgentStepper } from "@/components/AgentStepper";
import { ResultsDisplay } from "@/components/ResultsDisplay";
import { api } from "@/lib/api";
import type { TaskStatus, AnalysisResult } from "@/lib/types";

type UIState = "idle" | "running" | "done" | "error";

const EXAMPLE_QUERIES = [
  "How would a Taiwan strait blockade impact semiconductor supply chains and tech equities?",
  "What are the investment implications of escalating Houthi attacks on Red Sea shipping?",
  "Assess how a snap UK election would affect gilt markets and sterling volatility.",
];


export default function AnalysisPage() {
  const [query, setQuery] = useState("");
  const [uiState, setUiState] = useState<UIState>("idle");
  const [taskStatus, setTaskStatus] = useState<TaskStatus>("PENDING");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const handleSubmit = useCallback(async () => {
    if (!query.trim() || uiState === "running") return;

    setUiState("running");
    setTaskStatus("PENDING");
    setResult(null);
    setError(null);

    try {
      const { task_id } = await api.analyzeQuery(query.trim());

      pollRef.current = setInterval(async () => {
        try {
          const data = await api.getTaskStatus(task_id);
          setTaskStatus(data.status as TaskStatus);

          if (data.status === "SUCCESS") {
            stopPolling();
            setResult(data.result);
            setUiState("done");
          } else if (data.status === "FAILED") {
            stopPolling();
            setError(data.error ?? "Analysis failed");
            setUiState("error");
          }
        } catch {
          stopPolling();
          setError("Lost connection to server");
          setUiState("error");
        }
      }, 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start analysis");
      setUiState("error");
    }
  }, [query, uiState]);

  const handleReset = () => {
    stopPolling();
    setQuery("");
    setUiState("idle");
    setResult(null);
    setError(null);
    setTaskStatus("PENDING");
  };

  const running = uiState === "running";

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      {/* Input area */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
            Query Input
          </p>
          {uiState !== "idle" && (
            <Button size="sm" variant="ghost" onClick={handleReset}>
              <RotateCcw className="h-3.5 w-3.5" />
              New Query
            </Button>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="query">Geopolitical Query</Label>
          <Textarea
            id="query"
            rows={4}
            placeholder="e.g. How would a Taiwan blockade affect semiconductor supply chains and global tech equities?"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={running || uiState === "done"}
            className="text-sm leading-relaxed"
          />
          <p className="text-[10px] text-slate-600">{query.length}/2000 characters · minimum 10</p>
        </div>

        {/* Example queries */}
        {uiState === "idle" && (
          <div className="space-y-1.5">
            <p className="text-[10px] text-slate-600 font-medium">Example queries:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_QUERIES.map((q) => (
                <button
                  key={q}
                  onClick={() => setQuery(q)}
                  className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 hover:border-blue-600/50 hover:text-blue-400 transition-colors text-left"
                >
                  {q.slice(0, 60)}…
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex justify-end">
          <Button
            onClick={handleSubmit}
            disabled={query.trim().length < 10 || running || uiState === "done"}
            size="lg"
            className="gap-2"
          >
            {running ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Analyzing…
              </>
            ) : (
              <>
                <Send className="h-4 w-4" />
                Run Analysis
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Agent stepper */}
      {(running || uiState === "error") && (
        <AgentStepper status={taskStatus} error={error} />
      )}

      {/* Results */}
      {uiState === "done" && result && (
        <ResultsDisplay result={result} query={query} />
      )}

      {/* Rate limit / generic error (idle state) */}
      {uiState === "error" && error && (
        <div className="rounded-xl border border-rose-800 bg-rose-950/30 p-4">
          <p className="text-sm text-rose-400">
            <span className="font-semibold">Error: </span>{error}
          </p>
        </div>
      )}
    </div>
  );
}
