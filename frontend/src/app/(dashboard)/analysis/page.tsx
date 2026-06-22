"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import Link from "next/link";
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
  const [query, setQuery]           = useState("");
  const [uiState, setUiState]       = useState<UIState>("idle");
  const [taskStatus, setTaskStatus] = useState<TaskStatus>("PENDING");
  const [result, setResult]         = useState<AnalysisResult | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [error, setError]           = useState<string | null>(null);
  const [subQuestions, setSubQuestions] = useState<string[]>([]);
  const [taskId, setTaskId]         = useState<string | null>(null);

  // Streaming state
  const [streamingText, setStreamingText] = useState("");
  const [activeNode, setActiveNode]       = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const esRef   = useRef<EventSource | null>(null);
  // Tracks the last-seen poll status so we can detect WAITING→PROCESSING for auto-approve.
  const prevPollStatusRef = useRef<TaskStatus>("PENDING");

  const [includePortfolio, setIncludePortfolio] = useState(false);
  const [portfolioCount, setPortfolioCount]     = useState<number | null>(null);

  useEffect(() => {
    api.getPortfolio()
      .then((holdings) => {
        setPortfolioCount(holdings.length);
        if (holdings.length > 0) setIncludePortfolio(true);
      })
      .catch(() => setPortfolioCount(0));
  }, []);

  // Clean up both polling interval and SSE stream on unmount.
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (esRef.current) esRef.current.close();
    };
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const closeStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  /**
   * Opens an SSE stream for Task B.  Handles all stream events and cleans up
   * the EventSource when the stream finishes (complete/error) or on unmount.
   */
  const openStream = useCallback((id: string) => {
    let streamDone = false;

    let es: EventSource;
    try {
      es = api.streamAnalysis(id, (event) => {
        if (event.type === "node_start") {
          setActiveNode(event.node);
          setTaskStatus("PROCESSING");
        } else if (event.type === "token") {
          setStreamingText((prev) => prev + event.content);
        } else if (event.type === "complete") {
          streamDone = true;
          setResult(event.output);
          setAnalysisId(event.analysis_id ?? null);
          setUiState("done");
          setTaskStatus("SUCCESS");
          es.close();
          esRef.current = null;
        } else if (event.type === "error") {
          streamDone = true;
          setError(event.message);
          setUiState("error");
          setTaskStatus("FAILED");
          es.close();
          esRef.current = null;
        }
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Not authenticated");
      setUiState("error");
      return;
    }

    es.onerror = () => {
      if (!streamDone) {
        streamDone = true;
        setError("Lost connection to analysis stream");
        setUiState("error");
        setTaskStatus("FAILED");
        esRef.current = null;
      }
    };

    esRef.current = es;
  }, []);

  /**
   * Poll for Task A state (planner → WAITING_FOR_INPUT).
   * Once Task B starts (PROCESSING after WAITING_FOR_INPUT), hands off to SSE.
   * Also handles the cached-result fast path (immediate SUCCESS).
   */
  const startPolling = useCallback((id: string) => {
    prevPollStatusRef.current = "PENDING";
    pollRef.current = setInterval(async () => {
      try {
        const data = await api.getTaskStatus(id);
        const prev = prevPollStatusRef.current;
        prevPollStatusRef.current = data.status as TaskStatus;
        setTaskStatus(data.status as TaskStatus);

        if (data.status === "WAITING_FOR_INPUT") {
          if (data.sub_questions && data.sub_questions.length > 0) {
            setSubQuestions(data.sub_questions);
          }
        } else if (data.status === "SUCCESS") {
          // Cached result — no Task B was run so no SSE stream exists.
          stopPolling();
          setResult(data.result);
          setAnalysisId(data.analysis_id ?? null);
          setUiState("done");
        } else if (data.status === "FAILED") {
          stopPolling();
          setError(data.error ?? "Analysis failed");
          setUiState("error");
        } else if (data.status === "PROCESSING" && prev === "WAITING_FOR_INPUT") {
          // HITL auto-approved (10-min timeout) — switch from polling to SSE.
          stopPolling();
          openStream(id);
        }
      } catch {
        stopPolling();
        setError("Lost connection to server");
        setUiState("error");
      }
    }, 2000);
  }, [stopPolling, openStream]);

  const handleSubmit = useCallback(async () => {
    if (!query.trim() || uiState === "running") return;

    setUiState("running");
    setTaskStatus("PENDING");
    setResult(null);
    setError(null);
    setSubQuestions([]);
    setStreamingText("");
    setActiveNode(null);

    try {
      const { task_id } = await api.analyzeQuery(query.trim(), includePortfolio);
      setTaskId(task_id);
      startPolling(task_id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start analysis");
      setUiState("error");
    }
  }, [query, uiState, startPolling, includePortfolio]);

  const handleApprove = useCallback(async (questions: string[]) => {
    if (!taskId) return;
    try {
      await api.approvePlan(taskId, questions);
      // Stop polling immediately and open the SSE stream — Task B is now running.
      stopPolling();
      setTaskStatus("PROCESSING");
      openStream(taskId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to approve plan");
      setUiState("error");
      stopPolling();
    }
  }, [taskId, stopPolling, openStream]);

  const handleReset = () => {
    stopPolling();
    closeStream();
    setQuery("");
    setUiState("idle");
    setResult(null);
    setAnalysisId(null);
    setError(null);
    setTaskStatus("PENDING");
    setSubQuestions([]);
    setTaskId(null);
    setStreamingText("");
    setActiveNode(null);
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

        {/* Portfolio toggle */}
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            role="checkbox"
            aria-checked={includePortfolio}
            disabled={!portfolioCount || uiState !== "idle"}
            onClick={() => setIncludePortfolio((v) => !v)}
            className={`relative h-4 w-4 shrink-0 rounded border transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 ${
              includePortfolio
                ? "bg-amber-500 border-amber-500"
                : "border-slate-600 bg-slate-800"
            }`}
          >
            {includePortfolio && (
              <svg className="absolute inset-0 h-full w-full p-0.5 text-slate-950" viewBox="0 0 12 12" fill="none">
                <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
          <label
            className={`text-xs select-none ${portfolioCount ? "text-slate-400 cursor-pointer" : "text-slate-600 cursor-not-allowed"}`}
            onClick={() => portfolioCount && uiState === "idle" && setIncludePortfolio((v) => !v)}
          >
            Include my portfolio analysis
            {portfolioCount === 0 && (
              <span className="ml-1.5 text-slate-600">
                —{" "}
                <Link href="/portfolio" className="text-amber-600 hover:text-amber-400 transition-colors">
                  add holdings first
                </Link>
              </span>
            )}
            {portfolioCount != null && portfolioCount > 0 && (
              <span className="ml-1.5 text-slate-600">({portfolioCount} holdings)</span>
            )}
          </label>
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
                  className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 hover:border-blue-600/50 hover:text-blue-400 transition-colors text-left cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50"
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

      {/* Agent stepper — shown while running or after error */}
      {(running || uiState === "error") && (
        <AgentStepper
          status={taskStatus}
          error={error}
          subQuestions={subQuestions}
          onApprove={handleApprove}
          activeNode={activeNode ?? undefined}
        />
      )}

      {/* Live streaming preview — shown once SSE token/node events start arriving */}
      {running && (streamingText.length > 0 || activeNode != null) && (
        <ResultsDisplay
          query={query}
          isStreaming
          streamingText={streamingText}
          activeNode={activeNode ?? undefined}
        />
      )}

      {/* Final structured results after stream completes */}
      {uiState === "done" && result && (
        <ResultsDisplay result={result} query={query} analysisId={analysisId} />
      )}

      {/* Rate limit / generic error shown below stepper in error state */}
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
