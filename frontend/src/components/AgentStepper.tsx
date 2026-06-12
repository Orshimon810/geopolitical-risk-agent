"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { TaskStatus } from "@/lib/types";

interface TerminalLine {
  cmd: string;
  out: string;
}

const STEPS: TerminalLine[] = [
  {
    cmd: "georisk decompose --model gpt-4o-mini --sub-questions 5",
    out: "Decomposing query into research vectors...",
  },
  {
    cmd: "georisk retrieve --k 3 --backend neon-pgvector",
    out: "Scanning corpus · retrieving evidence chunks...",
  },
  {
    cmd: "georisk signals --tickers VIX,BRENT,GOLD,DXY --worldbank",
    out: "Fetching live prices & macro indicators...",
  },
  {
    cmd: "georisk synthesize --structured-output AnalysisOutput",
    out: "Generating risk analysis report...",
  },
];

const STEP_DELAY_MS = 2200;

interface AgentStepperProps {
  status: TaskStatus;
  error?: string | null;
  subQuestions?: string[];
  onApprove?: (questions: string[]) => void;
}

export function AgentStepper({ status, error, subQuestions, onApprove }: AgentStepperProps) {
  const [visibleCount, setVisibleCount] = useState(0);
  const [editedQuestions, setEditedQuestions] = useState<string[]>([]);
  const prevStatusRef = useRef<TaskStatus>(status);

  // Seed editable questions when we enter WAITING_FOR_INPUT
  useEffect(() => {
    if (status === "WAITING_FOR_INPUT" && subQuestions && subQuestions.length > 0) {
      setEditedQuestions(subQuestions);
    }
  }, [status, subQuestions]);

  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;

    if (status === "PROCESSING") {
      // Resuming after HITL approval — step 1 already done, start from step 2
      const startFrom = prev === "WAITING_FOR_INPUT" ? 1 : 0;
      setVisibleCount(startFrom);
      const timers = STEPS.slice(startFrom).map((_, i) =>
        setTimeout(() => setVisibleCount(startFrom + i + 1), i * STEP_DELAY_MS)
      );
      return () => timers.forEach(clearTimeout);
    }
    if (status === "SUCCESS" || status === "FAILED") {
      const t = setTimeout(() => setVisibleCount(STEPS.length), 0);
      return () => clearTimeout(t);
    }
    if (status === "PENDING") {
      setVisibleCount(0);
    }
    if (status === "WAITING_FOR_INPUT") {
      // Step 1 (planner) is done; pause here
      setVisibleCount(1);
    }
  }, [status]);

  const isProcessing     = status === "PROCESSING";
  const isWaiting        = status === "WAITING_FOR_INPUT";
  const isFailed         = status === "FAILED";
  const isSuccess        = status === "SUCCESS";

  const handleQuestionChange = (idx: number, value: string) => {
    setEditedQuestions(prev => prev.map((q, i) => (i === idx ? value : q)));
  };

  const handleApprove = () => {
    if (onApprove) onApprove(editedQuestions.filter(q => q.trim().length > 0));
  };

  return (
    <div className="terminal-window">
      {/* Chrome */}
      <div className="terminal-titlebar">
        <div className="terminal-dot bg-rose-500/70" />
        <div className="terminal-dot bg-amber-500/70" />
        <div className="terminal-dot bg-emerald-500/70" />
        <span className="ml-2 flex-1 text-[10px] text-slate-600 data-mono">
          georisk-agent — pipeline executor
        </span>
        {isProcessing && (
          <span className="flex items-center gap-1 text-[10px] text-amber-500/70 data-mono">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
            running
          </span>
        )}
        {isWaiting && (
          <span className="flex items-center gap-1 text-[10px] text-blue-400/80 data-mono">
            <span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
            awaiting review
          </span>
        )}
        {isSuccess && (
          <span className="text-[10px] text-emerald-400 data-mono">done</span>
        )}
        {isFailed && (
          <span className="text-[10px] text-rose-400 data-mono">failed</span>
        )}
      </div>

      {/* Console body */}
      <div
        role="log"
        aria-label="Pipeline execution log"
        aria-live="polite"
        className="p-5 min-h-52 space-y-3 data-mono text-xs"
      >
        {/* Boot header */}
        <div className="space-y-0.5">
          <p className="text-slate-600">GeoRisk Agent v2.0 — Dynamic LangGraph Pipeline</p>
          <p className="text-slate-800">{"─".repeat(48)}</p>
        </div>

        {/* Step lines */}
        <AnimatePresence>
          {STEPS.slice(0, visibleCount).map((step, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 3 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              className="space-y-0.5"
            >
              <p className="flex items-start gap-1.5">
                <span className="text-emerald-500 shrink-0">✓</span>
                <span className="text-slate-400 line-through">{step.cmd}</span>
              </p>
              <p className="flex items-start gap-1.5 pl-3.5">
                <span className="text-slate-700 shrink-0">→</span>
                <span className="text-slate-600">{step.out}</span>
              </p>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* WAITING_FOR_INPUT — editable sub-questions panel */}
        <AnimatePresence>
          {isWaiting && editedQuestions.length > 0 && (
            <motion.div
              key="hitl-panel"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.22, ease: "easeOut" }}
              className="mt-2 rounded-lg border border-blue-800/50 bg-blue-950/20 p-4 space-y-3 not-data-mono font-sans"
            >
              <p className="text-[10px] font-semibold uppercase tracking-widest text-blue-400">
                Research Plan — Review &amp; Edit
              </p>
              <p className="text-xs text-slate-400 leading-relaxed">
                The planner decomposed your query into the sub-questions below.
                Edit any that seem off-target, then click <strong className="text-slate-300">Approve</strong> to continue.
              </p>
              <div className="space-y-2">
                {editedQuestions.map((q, idx) => (
                  <div key={idx} className="flex items-start gap-2">
                    <span className="mt-2 text-[10px] text-slate-600 shrink-0 w-4 text-right">{idx + 1}.</span>
                    <textarea
                      rows={2}
                      value={q}
                      onChange={e => handleQuestionChange(idx, e.target.value)}
                      className="flex-1 rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 leading-relaxed resize-none focus:outline-none focus:ring-1 focus:ring-blue-500/60 placeholder:text-slate-600"
                    />
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between pt-1">
                <p className="text-[10px] text-slate-600">
                  Auto-approved if you don&apos;t respond within 10 minutes.
                </p>
                <button
                  onClick={handleApprove}
                  className="rounded-md bg-blue-600 hover:bg-blue-500 active:bg-blue-700 px-4 py-1.5 text-xs font-semibold text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
                >
                  Approve &amp; Continue
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Blinking cursor while waiting for next step */}
        {isProcessing && visibleCount < STEPS.length && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center gap-1.5"
          >
            <span className="text-amber-500">$</span>
            <span className="inline-block h-3.5 w-1.5 bg-amber-400 cursor-blink" />
          </motion.p>
        )}

        {/* Success output */}
        {isSuccess && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.1 }}
            className="pt-1 space-y-0.5"
          >
            <p className="text-slate-800">{"─".repeat(48)}</p>
            <p className="text-emerald-400">✓  analysis complete — rendering results below</p>
          </motion.div>
        )}

        {/* Error output */}
        {isFailed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="pt-1 space-y-0.5"
          >
            <p className="text-slate-800">{"─".repeat(48)}</p>
            <p className="text-rose-400">✗  pipeline error</p>
            {error && (
              <p className="flex items-start gap-1.5 pl-3.5">
                <span className="text-slate-700 shrink-0">→</span>
                <span className="text-slate-500">{error}</span>
              </p>
            )}
          </motion.div>
        )}
      </div>
    </div>
  );
}
