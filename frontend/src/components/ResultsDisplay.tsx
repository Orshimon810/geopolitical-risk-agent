"use client";

import { motion } from "framer-motion";
import { AlertTriangle, BarChart2, Lightbulb, GitBranch, BookOpen, Map } from "lucide-react";
import { MarketSignals } from "@/components/MarketSignals";
import type { AnalysisResult, Confidence } from "@/lib/types";

interface ResultsDisplayProps {
  result: AnalysisResult;
  query: string;
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

export function ResultsDisplay({ result, query }: ResultsDisplayProps) {
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
    </motion.div>
  );
}
