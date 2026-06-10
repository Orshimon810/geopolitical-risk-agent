"use client";

import { useState, useEffect } from "react";
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
}

export function AgentStepper({ status, error }: AgentStepperProps) {
  const [visibleCount, setVisibleCount] = useState(0);

  useEffect(() => {
    if (status === "PROCESSING") {
      setVisibleCount(1);
      const timers = STEPS.slice(1).map((_, i) =>
        setTimeout(() => setVisibleCount(i + 2), (i + 1) * STEP_DELAY_MS)
      );
      return () => timers.forEach(clearTimeout);
    }
    if (status === "SUCCESS" || status === "FAILED") {
      setVisibleCount(STEPS.length);
    }
    if (status === "PENDING") {
      setVisibleCount(0);
    }
  }, [status]);

  const isProcessing = status === "PROCESSING";
  const isFailed    = status === "FAILED";
  const isSuccess   = status === "SUCCESS";

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
        {isSuccess && (
          <span className="text-[10px] text-emerald-400 data-mono">done</span>
        )}
        {isFailed && (
          <span className="text-[10px] text-rose-400 data-mono">failed</span>
        )}
      </div>

      {/* Console body */}
      <div className="p-5 min-h-52 space-y-3 data-mono text-xs">
        {/* Boot header */}
        <div className="space-y-0.5">
          <p className="text-slate-600">GeoRisk Agent v1.0 — LangGraph Pipeline Executor</p>
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
                <span className="text-amber-500 shrink-0">$</span>
                <span className="text-slate-300">{step.cmd}</span>
              </p>
              <p className="flex items-start gap-1.5 pl-3.5">
                <span className="text-slate-700 shrink-0">→</span>
                <span className="text-slate-500">{step.out}</span>
              </p>
            </motion.div>
          ))}
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
