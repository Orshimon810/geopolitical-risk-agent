"use client";

import { useState, useEffect } from "react";
import { Clock, Loader2 } from "lucide-react";
import { HistoryTable } from "@/components/HistoryTable";
import { ResultsDisplay } from "@/components/ResultsDisplay";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { HistoryItem } from "@/lib/types";

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<HistoryItem | null>(null);

  useEffect(() => {
    api
      .getHistory()
      .then(setItems)
      .finally(() => setLoading(false));
  }, []);

  if (selected) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
            ← Back to History
          </Button>
        </div>
        <ResultsDisplay
          result={selected.result ?? {
            market_impacts: selected.market_impacts,
            risks: [],
            scenarios: [],
            investor_takeaway: [],
            confidence: selected.confidence,
            sources: [],
            signals: {},
          }}
          query={selected.query}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">Analysis Archive</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Historical geopolitical risk reports — sorted by date
          </p>
        </div>
        <div className="flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900 px-3 py-1">
          <Clock className="h-3 w-3 text-slate-500" />
          <span className="text-xs text-slate-500">{items.length} analyses</span>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
        </div>
      ) : (
        <HistoryTable
            items={items}
            onViewReport={setSelected}
            onDelete={async (id) => {
              await api.deleteAnalysis(id);
              setItems((prev) => prev.filter((i) => i.id !== id));
            }}
          />
      )}

    </div>
  );
}
