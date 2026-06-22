"use client";

import { useRef, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, BarChart2, Lightbulb, GitBranch, BookOpen, Map, Briefcase, ThumbsUp, ThumbsDown, Loader2 } from "lucide-react";
import { MarketSignals } from "@/components/MarketSignals";
import { api } from "@/lib/api";
import type { AnalysisResult, Confidence, PortfolioHoldingImpact, Verdict } from "@/lib/types";

interface ResultsDisplayProps {
  result?: AnalysisResult | null;
  query: string;
  analysisId?: string | null;
  /** Accumulated token chunks from the SSE stream */
  streamingText?: string;
  /** True while Task B SSE stream is still open */
  isStreaming?: boolean;
  /** Currently-executing pipeline node name from SSE node_start events */
  activeNode?: string;
}

const NODE_STATUS_LABELS: Record<string, string> = {
  rag_research:          "Retrieving evidence from corpus",
  signals:               "Fetching live market signals",
  analysis:              "Synthesizing geopolitical analysis",
  consistency_validator: "Validating portfolio consistency",
  reviewer:              "Running quality review",
  final_output:          "Finalizing report",
};

function StreamingView({
  query,
  streamingText,
  activeNode,
}: {
  query: string;
  streamingText: string;
  activeNode?: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [streamingText]);

  const nodeLabel = activeNode ? (NODE_STATUS_LABELS[activeNode] ?? activeNode) : null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-amber-800/30 bg-slate-900 overflow-hidden"
    >
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[9px] text-amber-600/70 uppercase tracking-[0.18em] data-mono mb-1.5">
            LIVE ANALYSIS
          </p>
          <p className="text-sm text-slate-400 leading-relaxed line-clamp-2">{query}</p>
        </div>
        {nodeLabel && (
          <div className="flex items-center gap-2 shrink-0 rounded-md border border-amber-800/30 bg-amber-500/5 px-2.5 py-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse shrink-0" />
            <span className="text-[10px] text-amber-400/80 data-mono">{nodeLabel}</span>
          </div>
        )}
      </div>

      {/* Streaming token output */}
      <div
        ref={scrollRef}
        className="px-6 py-5 h-52 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent"
      >
        {streamingText ? (
          <pre className="text-xs text-slate-400 leading-relaxed whitespace-pre-wrap break-words font-mono">
            {streamingText}
            <span className="inline-block h-3 w-1.5 bg-amber-400 cursor-blink ml-0.5 align-text-bottom" />
          </pre>
        ) : (
          <div className="flex items-center gap-2 text-xs text-slate-600 data-mono">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500/60 animate-pulse" />
            Waiting for LLM output&hellip;
          </div>
        )}
      </div>
    </motion.div>
  );
}

type FeedbackState = "idle" | "loading" | "submitted" | "error";

function FeedbackFooter({ analysisId }: { analysisId?: string | null }) {
  const [feedbackState, setFeedbackState] = useState<FeedbackState>("idle");
  const [submittedScore, setSubmittedScore] = useState<0 | 1 | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!analysisId) return null;

  async function handleFeedback(score: 0 | 1) {
    if (feedbackState !== "idle") return;
    setFeedbackState("loading");
    setSubmittedScore(score);
    try {
      await api.submitFeedback(analysisId!, score);
      setFeedbackState("submitted");
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to submit feedback");
      setFeedbackState("error");
    }
  }

  const isLoading = feedbackState === "loading";
  const isSubmitted = feedbackState === "submitted";
  const isDisabled = feedbackState !== "idle";

  return (
    <div className="border-t border-slate-800 px-6 py-4 flex items-center justify-between gap-4">
      <p className="text-[9px] text-slate-600 uppercase tracking-[0.18em] data-mono">
        {isSubmitted ? "Feedback received" : feedbackState === "error" ? errorMsg : "Was this analysis helpful?"}
      </p>

      {!isSubmitted && feedbackState !== "error" && (
        <div className="flex items-center gap-2">
          {/* Thumbs Up */}
          <button
            onClick={() => handleFeedback(1)}
            disabled={isDisabled}
            aria-label="Helpful"
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50 disabled:cursor-not-allowed ${
              isLoading && submittedScore === 1
                ? "border-emerald-600/50 bg-emerald-500/10 text-emerald-400"
                : "border-slate-700 bg-slate-800/60 text-slate-500 hover:border-emerald-600/50 hover:text-emerald-400 hover:bg-emerald-500/10"
            }`}
          >
            {isLoading && submittedScore === 1
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <ThumbsUp className="h-3.5 w-3.5" />}
          </button>

          {/* Thumbs Down */}
          <button
            onClick={() => handleFeedback(0)}
            disabled={isDisabled}
            aria-label="Not helpful"
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50 disabled:cursor-not-allowed ${
              isLoading && submittedScore === 0
                ? "border-rose-600/50 bg-rose-500/10 text-rose-400"
                : "border-slate-700 bg-slate-800/60 text-slate-500 hover:border-rose-600/50 hover:text-rose-400 hover:bg-rose-500/10"
            }`}
          >
            {isLoading && submittedScore === 0
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <ThumbsDown className="h-3.5 w-3.5" />}
          </button>
        </div>
      )}

      {isSubmitted && (
        <div className={`flex items-center gap-1.5 text-xs ${submittedScore === 1 ? "text-emerald-400" : "text-rose-400"}`}>
          {submittedScore === 1
            ? <ThumbsUp className="h-3.5 w-3.5" />
            : <ThumbsDown className="h-3.5 w-3.5" />}
          <span className="data-mono text-[10px] uppercase tracking-wide">Submitted</span>
        </div>
      )}

      {feedbackState === "error" && (
        <button
          onClick={() => { setFeedbackState("idle"); setSubmittedScore(null); setErrorMsg(null); }}
          className="text-[10px] text-amber-600 hover:text-amber-400 transition-colors data-mono uppercase tracking-wide"
        >
          Retry
        </button>
      )}
    </div>
  );
}

/* ── Confidence meter — three ascending bars like signal strength ── */
function ConfidenceMeter({ level }: { level: Confidence }) {
  const rank: Record<Confidence, number> = { Low: 1, Medium: 2, High: 3 };
  const colorClass: Record<Confidence, string> = {
    Low:    "bg-rose-500",
    Medium: "bg-amber-500",
    High:   "bg-emerald-500",
  };
  const labelClass: Record<Confidence, string> = {
    Low:    "text-rose-400",
    Medium: "text-amber-400",
    High:   "text-emerald-400",
  };

  return (
    <div className="text-right shrink-0">
      <p className="text-[9px] text-slate-600 uppercase tracking-[0.18em] data-mono mb-2">
        CONFIDENCE
      </p>
      <div className="flex items-end justify-end gap-1 mb-1">
        {(["Low", "Medium", "High"] as Confidence[]).map((l) => {
          const filled = rank[level] >= rank[l];
          const heights = { Low: "h-2", Medium: "h-3.5", High: "h-5" };
          return (
            <div
              key={l}
              className={`w-4 rounded-sm transition-all duration-500 ${heights[l]} ${
                filled ? colorClass[level] : "bg-slate-800"
              }`}
            />
          );
        })}
      </div>
      <span className={`text-xs font-bold data-mono uppercase ${labelClass[level]}`}>
        {level}
      </span>
    </div>
  );
}

/* ── Bloomberg-style section wrapper ── */
function Section({
  label,
  icon,
  children,
  delay = 0,
}: {
  label: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay }}
      className="border-t border-slate-800 pt-4 space-y-3"
    >
      <div className="section-header">
        <span className="text-amber-600/70 shrink-0">{icon}</span>
        {label}
      </div>
      {children}
    </motion.div>
  );
}

/* ── Bullet list used in most sections ── */
function BulletList({ items }: { items: string[] }) {
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2.5 text-sm text-slate-300 leading-relaxed">
          <span className="text-amber-500 mt-1 shrink-0 text-[10px] data-mono">▸</span>
          {item}
        </li>
      ))}
    </ul>
  );
}

/* ── Per-holding verdict badge ── */
const VERDICT_STYLES: Record<Verdict, string> = {
  Bullish: "text-emerald-400 bg-emerald-500/10 border-emerald-500/30",
  Bearish: "text-rose-400 bg-rose-500/10 border-rose-500/30",
  Neutral: "text-slate-400 bg-slate-700/40 border-slate-600/30",
};

function PortfolioImpactCard({ impact }: { impact: PortfolioHoldingImpact }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-3">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="font-mono font-bold text-slate-100 text-sm">{impact.ticker}</span>
          <span className="ml-2 text-xs text-slate-500">{impact.name}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded border ${VERDICT_STYLES[impact.verdict]}`}>
            {impact.verdict}
          </span>
          <span className="text-[10px] text-slate-600 data-mono">{impact.confidence}</span>
        </div>
      </div>

      {/* Impact rows */}
      <div className="space-y-2">
        <div className="grid grid-cols-[5rem_1fr] gap-2 text-xs">
          <span className="text-slate-600 font-medium pt-0.5">Short-term</span>
          <span className="text-slate-300 leading-relaxed">{impact.short_term_impact}</span>
        </div>
        <div className="grid grid-cols-[5rem_1fr] gap-2 text-xs">
          <span className="text-slate-600 font-medium pt-0.5">Long-term</span>
          <span className="text-slate-300 leading-relaxed">{impact.long_term_impact}</span>
        </div>
        <div className="grid grid-cols-[5rem_1fr] gap-2 text-xs">
          <span className="text-slate-600 font-medium pt-0.5">Reasoning</span>
          <span className="text-slate-400 leading-relaxed">{impact.reasoning}</span>
        </div>
      </div>
    </div>
  );
}

export function ResultsDisplay({ result, query, analysisId, streamingText, isStreaming, activeNode }: ResultsDisplayProps) {
  if (isStreaming) {
    return (
      <StreamingView
        query={query}
        streamingText={streamingText ?? ""}
        activeNode={activeNode}
      />
    );
  }
  if (!result) return null;
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden"
    >
      {/* ── Header ── */}
      <div className="px-6 py-5 border-b border-slate-800 flex items-start justify-between gap-6">
        <div className="min-w-0">
          <p className="text-[9px] text-amber-600/70 uppercase tracking-[0.18em] data-mono mb-2">
            ANALYSIS COMPLETE
          </p>
          <p className="text-sm text-slate-300 leading-relaxed line-clamp-3">{query}</p>
        </div>
        <ConfidenceMeter level={result.confidence} />
      </div>

      {/* ── Sections ── */}
      <div className="px-6 py-5 space-y-4">

        {/* Market Impacts */}
        {result.market_impacts?.length > 0 && (
          <Section
            label="MARKET IMPACTS"
            icon={<BarChart2 className="h-3 w-3" />}
            delay={0.05}
          >
            <BulletList items={result.market_impacts} />
          </Section>
        )}

        {/* Key Risks */}
        {result.risks?.length > 0 && (
          <Section
            label="KEY RISKS"
            icon={<AlertTriangle className="h-3 w-3" />}
            delay={0.1}
          >
            <BulletList items={result.risks} />
          </Section>
        )}

        {/* Scenarios */}
        {result.scenarios?.length > 0 && (
          <Section
            label="SCENARIOS"
            icon={<GitBranch className="h-3 w-3" />}
            delay={0.15}
          >
            <div className="space-y-2">
              {result.scenarios.map((scenario, i) => (
                <div
                  key={i}
                  className="flex items-start gap-3 rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2.5"
                >
                  <span className="text-[10px] font-bold text-slate-600 data-mono mt-0.5 shrink-0 w-4">
                    {String.fromCharCode(65 + i)}
                  </span>
                  <p className="text-sm text-slate-300 leading-relaxed">{scenario}</p>
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* Investor Takeaway — pull-quote style */}
        {result.investor_takeaway?.length > 0 && (
          <Section
            label="INVESTOR TAKEAWAY"
            icon={<Lightbulb className="h-3 w-3" />}
            delay={0.2}
          >
            <div className="border-l-2 border-amber-500/50 pl-4 space-y-2">
              {result.investor_takeaway.map((item, i) => (
                <p key={i} className="text-sm text-slate-200 leading-relaxed font-medium">
                  {item}
                </p>
              ))}
            </div>
          </Section>
        )}

        {/* Market signals */}
        {result.signals && (result.signals.market_data || result.signals.countries) && (
          <Section
            label="MARKET SIGNALS"
            icon={<BookOpen className="h-3 w-3" />}
            delay={0.25}
          >
            <MarketSignals signals={result.signals} />
          </Section>
        )}

        {/* Portfolio Impact */}
        {result.portfolio_impacts && result.portfolio_impacts.length > 0 && (
          <Section
            label="PORTFOLIO IMPACT"
            icon={<Briefcase className="h-3 w-3" />}
            delay={0.28}
          >
            <div className="space-y-3">
              {result.portfolio_impacts.map((impact, i) => (
                <PortfolioImpactCard key={i} impact={impact} />
              ))}
            </div>
          </Section>
        )}

        {/* Sources */}
        {result.sources?.length > 0 && (
          <Section
            label="SOURCES"
            icon={<Map className="h-3 w-3" />}
            delay={0.3}
          >
            <ol className="space-y-1.5">
              {result.sources.map((src, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-slate-500 leading-relaxed">
                  <span className="text-slate-700 data-mono shrink-0 w-4">{i + 1}.</span>
                  <span>{src}</span>
                </li>
              ))}
            </ol>
          </Section>
        )}
      </div>

      {/* Feedback */}
      <FeedbackFooter analysisId={analysisId} />
    </motion.div>
  );
}
