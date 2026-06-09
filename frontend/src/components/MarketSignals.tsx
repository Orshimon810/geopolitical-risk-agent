"use client";

import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import type { Signals } from "@/lib/types";

interface MarketSignalsProps {
  signals: Signals;
}

function TrendIcon({ pct }: { pct: number | null }) {
  if (pct === null || pct === undefined) return <Minus className="h-3 w-3 text-slate-500" />;
  if (pct > 0) return <TrendingUp className="h-3 w-3 text-emerald-400" />;
  if (pct < 0) return <TrendingDown className="h-3 w-3 text-red-400" />;
  return <Minus className="h-3 w-3 text-slate-500" />;
}

function pctColor(pct: number | null) {
  if (pct === null || pct === undefined) return "text-slate-500";
  if (pct > 0) return "text-emerald-400";
  if (pct < 0) return "text-red-400";
  return "text-slate-500";
}

export function MarketSignals({ signals }: MarketSignalsProps) {
  const marketEntries = Object.entries(signals.market_data ?? {}).filter(
    ([, v]) => v.status === "ok"
  );

  const countryEntries = Object.entries(signals.countries ?? {});

  if (marketEntries.length === 0 && countryEntries.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Market Signals</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Live market prices */}
        {marketEntries.length > 0 && (
          <>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Live Prices
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {marketEntries.map(([ticker, entry]) => (
                <div
                  key={ticker}
                  className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5"
                >
                  <p className="text-[10px] text-slate-500 font-medium truncate">
                    {entry.label}
                  </p>
                  <p className="text-sm font-semibold text-slate-100 mt-0.5">
                    {entry.price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                  </p>
                  <div className={`flex items-center gap-1 mt-0.5 ${pctColor(entry.change_1d_pct)}`}>
                    <TrendIcon pct={entry.change_1d_pct} />
                    <span className="text-[10px]">
                      {entry.change_1d_pct !== null && entry.change_1d_pct !== undefined
                        ? `${entry.change_1d_pct > 0 ? "+" : ""}${entry.change_1d_pct.toFixed(2)}%`
                        : "N/A"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Macro indicators by country */}
        {countryEntries.length > 0 && (
          <>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600 pt-2">
              Macro Indicators
            </p>
            <div className="space-y-1.5">
              {countryEntries.flatMap(([iso, data]) => {
                const rows = [];
                if (data.trade_gdp?.status === "ok" && data.trade_gdp.value !== undefined) {
                  rows.push(
                    <div
                      key={`${iso}-trade`}
                      className="flex items-center justify-between rounded-md px-3 py-2 bg-slate-950 border border-slate-800"
                    >
                      <span className="text-xs text-slate-400">
                        {iso} — Trade % of GDP
                      </span>
                      <span className="text-xs font-semibold text-slate-200">
                        {data.trade_gdp.value.toFixed(1)}%
                        <span className="text-slate-600 font-normal ml-1">
                          ({data.trade_gdp.year})
                        </span>
                      </span>
                    </div>
                  );
                }
                if (data.oil_rents?.status === "ok" && data.oil_rents.value !== undefined) {
                  rows.push(
                    <div
                      key={`${iso}-oil`}
                      className="flex items-center justify-between rounded-md px-3 py-2 bg-slate-950 border border-slate-800"
                    >
                      <span className="text-xs text-slate-400">
                        {iso} — Oil Rents % of GDP
                      </span>
                      <span className="text-xs font-semibold text-slate-200">
                        {data.oil_rents.value.toFixed(1)}%
                        <span className="text-slate-600 font-normal ml-1">
                          ({data.oil_rents.year})
                        </span>
                      </span>
                    </div>
                  );
                }
                return rows;
              })}
            </div>
          </>
        )}

        {signals.note && (
          <p className="text-xs text-slate-600 italic">{signals.note}</p>
        )}
      </CardContent>
    </Card>
  );
}
