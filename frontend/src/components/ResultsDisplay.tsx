"use client";

import ReactMarkdown from "react-markdown";
import { AlertTriangle, BarChart2, Lightbulb, Map, BookOpen } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MarketSignals } from "@/components/MarketSignals";
import type { AnalysisResult, Confidence } from "@/lib/types";

function confidenceVariant(c: Confidence) {
  return c === "High" ? "high" : c === "Medium" ? "medium" : "low";
}

interface ResultsDisplayProps {
  result: AnalysisResult;
  query: string;
}

export function ResultsDisplay({ result, query }: ResultsDisplayProps) {
  const synthesized = [
    result.market_impacts?.length ? `## Market Impacts\n${result.market_impacts.map((i) => `- ${i}`).join("\n")}` : "",
    result.risks?.length ? `## Key Risks\n${result.risks.map((r) => `- ${r}`).join("\n")}` : "",
    result.scenarios?.length ? `## Scenarios\n${result.scenarios.map((s) => `- ${s}`).join("\n")}` : "",
    result.investor_takeaway?.length
      ? `## Investor Takeaway\n${result.investor_takeaway.map((t) => `- ${t}`).join("\n")}`
      : "",
  ]
    .filter(Boolean)
    .join("\n\n");

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600 mb-1">
            Analysis Complete
          </p>
          <p className="text-sm text-slate-400 line-clamp-2">{query}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs text-slate-500">Confidence</span>
          <Badge variant={confidenceVariant(result.confidence)} className="text-xs">
            {result.confidence}
          </Badge>
        </div>
      </div>

      {/* Main report */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BookOpen className="h-3.5 w-3.5 text-blue-400" />
            Synthesized Report
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="prose max-w-none">
            <ReactMarkdown>{synthesized}</ReactMarkdown>
          </div>
        </CardContent>
      </Card>

      {/* Metrics grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <MetricCard
          icon={<BarChart2 className="h-4 w-4 text-blue-400" />}
          label="Market Impacts"
          items={result.market_impacts}
          color="blue"
        />
        <MetricCard
          icon={<AlertTriangle className="h-4 w-4 text-amber-400" />}
          label="Key Risks"
          items={result.risks}
          color="amber"
        />
        <MetricCard
          icon={<Lightbulb className="h-4 w-4 text-emerald-400" />}
          label="Investor Takeaway"
          items={result.investor_takeaway}
          color="emerald"
        />
      </div>

      {/* Market signals */}
      {result.signals && Object.keys(result.signals).length > 0 && (
        <MarketSignals signals={result.signals} />
      )}

      {/* Sources */}
      {result.sources?.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Map className="h-3.5 w-3.5 text-slate-400" />
              Sources
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1">
              {result.sources.map((src, i) => (
                <li key={i} className="text-xs text-slate-400 flex gap-2">
                  <span className="text-slate-600">{i + 1}.</span>
                  {src}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function MetricCard({
  icon,
  label,
  items,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  items: string[];
  color: "blue" | "amber" | "emerald";
}) {
  const borderMap = { blue: "border-blue-800/40", amber: "border-amber-800/40", emerald: "border-emerald-800/40" };
  const bgMap = { blue: "bg-blue-950/20", amber: "bg-amber-950/20", emerald: "bg-emerald-950/20" };

  return (
    <div className={`rounded-xl border ${borderMap[color]} ${bgMap[color]} p-4`}>
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <span className="text-xs font-semibold text-slate-300">{label}</span>
      </div>
      {items?.length ? (
        <ul className="space-y-1.5">
          {items.slice(0, 4).map((item, i) => (
            <li key={i} className="text-xs text-slate-400 leading-relaxed">
              • {item}
            </li>
          ))}
          {items.length > 4 && (
            <li className="text-xs text-slate-600">+{items.length - 4} more</li>
          )}
        </ul>
      ) : (
        <p className="text-xs text-slate-600">No data</p>
      )}
    </div>
  );
}
