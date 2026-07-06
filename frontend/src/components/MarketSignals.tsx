"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Cell,
  Tooltip,
} from "recharts";
import type { Signals } from "@/lib/types";

interface MarketSignalsProps {
  signals: Signals;
}

/* ── Single ticker card ── */
function TickerCard({
  ticker,
  label,
  price,
  changePct,
}: {
  ticker: string;
  label: string;
  price: number;
  changePct: number | null;
}) {
  const positive = changePct !== null && changePct > 0;
  const negative = changePct !== null && changePct < 0;
  const neutral  = changePct === null || changePct === 0;

  const deltaColor = positive ? "text-emerald-400" : negative ? "text-rose-400" : "text-slate-500";
  const borderColor = positive
    ? "border-emerald-900/50"
    : negative
    ? "border-rose-900/50"
    : "border-slate-800";

  const formattedPrice = price.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const formattedDelta =
    changePct !== null
      ? `${changePct > 0 ? "+" : ""}${changePct.toFixed(2)}%`
      : "N/A";

  return (
    <div className={`rounded-lg border ${borderColor} bg-slate-950 px-3.5 py-3 space-y-1`}>
      {/* Ticker symbol */}
      <p className="text-[9px] font-bold uppercase tracking-widest text-slate-600 data-mono">
        {ticker}
      </p>
      {/* Short label */}
      <p className="text-[10px] text-slate-500 truncate leading-tight">{label}</p>
      {/* Price — large monospace */}
      <p className="text-lg font-bold text-slate-100 data-mono leading-none">
        {formattedPrice}
      </p>
      {/* Delta */}
      <p className={`text-xs font-semibold data-mono ${deltaColor}`}>
        {neutral ? "—" : positive ? "▲" : "▼"}{" "}
        {formattedDelta}
      </p>
    </div>
  );
}

/* ── Custom tooltip for recharts ── */
function MacroTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: { fullLabel: string; value: number; year?: string } }>;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-xs shadow-xl">
      <p className="text-slate-300 font-medium mb-0.5">{d.fullLabel}</p>
      <p className="text-amber-400 data-mono font-bold">
        {d.value.toFixed(1)}%{d.year ? ` (${d.year})` : ""}
      </p>
    </div>
  );
}

export function MarketSignals({ signals }: MarketSignalsProps) {
  const marketEntries = Object.entries(signals.market_data ?? {}).filter(
    ([, v]) => v.status === "ok"
  );

  interface MacroRow {
    name: string;
    fullLabel: string;
    value: number;
    year?: string;
  }

  const macroRows: MacroRow[] = Object.entries(signals.countries ?? {}).flatMap(
    ([iso, data]) => {
      const rows: MacroRow[] = [];
      if (data.trade_gdp?.status === "ok" && data.trade_gdp.value !== undefined) {
        rows.push({
          name: `${iso} Trade`,
          fullLabel: `${iso} — Trade % of GDP`,
          value: data.trade_gdp.value,
          year: data.trade_gdp.year,
        });
      }
      if (data.oil_rents?.status === "ok" && data.oil_rents.value !== undefined) {
        rows.push({
          name: `${iso} Oil`,
          fullLabel: `${iso} — Oil Rents % of GDP`,
          value: data.oil_rents.value,
          year: data.oil_rents.year,
        });
      }
      return rows;
    }
  );

  if (marketEntries.length === 0 && macroRows.length === 0) return null;

  const chartHeight = Math.max(macroRows.length * 36 + 8, 80);

  return (
    <div className="space-y-5">
      {/* ── Live Prices grid ── */}
      {marketEntries.length > 0 && (
        <div className="space-y-2.5">
          <p className="section-header">
            LIVE PRICES
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {marketEntries.map(([ticker, entry]) => (
              <TickerCard
                key={ticker}
                ticker={ticker}
                label={entry.label}
                price={entry.price}
                changePct={entry.change_1d_pct}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Macro Indicators — recharts horizontal bar chart ── */}
      {macroRows.length > 0 && (
        <div className="space-y-2.5">
          <p className="section-header">
            MACRO INDICATORS
          </p>
          <div style={{ height: chartHeight }} className="w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={macroRows}
                layout="vertical"
                margin={{ top: 0, right: 48, bottom: 0, left: 72 }}
                barSize={10}
              >
                <XAxis
                  type="number"
                  domain={[0, "auto"]}
                  tick={{ fill: "#404040", fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}
                  tickFormatter={(v) => `${v}%`}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{ fill: "#737373", fontSize: 9, fontFamily: "var(--font-mono, monospace)" }}
                  axisLine={false}
                  tickLine={false}
                  width={68}
                />
                <Tooltip
                  content={<MacroTooltip />}
                  cursor={{ fill: "rgba(255,255,255,0.03)" }}
                />
                <Bar dataKey="value" radius={[0, 3, 3, 0]}>
                  {macroRows.map((_, i) => (
                    <Cell key={i} fill="#d97706" fillOpacity={0.75} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* signals.note is only ever set for the "no countries detected" case
          (nodes_signals.py) — displaying it reads as a failure message rather
          than a neutral non-event, so it's hidden entirely at the display
          layer rather than shown with reworded text (Phase 2A.4 polish). */}
      {signals.note && macroRows.length > 0 && (
        <p className="text-[10px] text-slate-600 italic data-mono">{signals.note}</p>
      )}
    </div>
  );
}
