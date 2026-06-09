"use client";

import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";

const TICKER_LABELS: Record<string, string> = {
  "^VIX": "VIX",
  "BZ=F": "Brent Crude",
  "GC=F": "Gold",
  "DX-Y.NYB": "DXY",
  "NG=F": "Nat Gas",
  "EEM": "EM ETF",
  "FEZ": "Euro Stoxx",
  "FXI": "China (FXI)",
  "TSM": "TSMC",
};

interface MarketSignalsProps {
  signals: Record<string, number | string | null>;
}

function formatValue(val: number | string | null): string {
  if (val === null || val === undefined) return "N/A";
  if (typeof val === "number") return val.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return String(val);
}

export function MarketSignals({ signals }: MarketSignalsProps) {
  const tickers = Object.entries(signals).filter(([k]) => !k.includes("_"));
  const macro = Object.entries(signals).filter(([k]) => k.includes("_"));

  if (tickers.length === 0 && macro.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Market Signals</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {tickers.length > 0 && (
          <>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Live Prices
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {tickers.map(([ticker, val]) => (
                <div
                  key={ticker}
                  className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5"
                >
                  <p className="text-[10px] text-slate-500 font-medium truncate">
                    {TICKER_LABELS[ticker] ?? ticker}
                  </p>
                  <p className="text-sm font-semibold text-slate-100 mt-0.5">
                    {formatValue(val)}
                  </p>
                </div>
              ))}
            </div>
          </>
        )}

        {macro.length > 0 && (
          <>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600 pt-2">
              Macro Indicators
            </p>
            <div className="space-y-1.5">
              {macro.map(([key, val]) => {
                const label = key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
                return (
                  <div key={key} className="flex items-center justify-between rounded-md px-3 py-2 bg-slate-950 border border-slate-800">
                    <span className="text-xs text-slate-400">{label}</span>
                    <span className="text-xs font-semibold text-slate-200">{formatValue(val)}</span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
