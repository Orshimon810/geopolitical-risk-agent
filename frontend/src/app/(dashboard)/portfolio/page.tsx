"use client";

import { useState, useEffect, useRef } from "react";
import { Plus, Pencil, Trash2, Loader2, Check, X, Zap, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import type { AssetType, PortfolioHolding } from "@/lib/types";

const MAX_HOLDINGS = 20;

const ASSET_TYPES: AssetType[] = ["stock", "etf", "crypto", "commodity", "bond"];

const ASSET_LABELS: Record<AssetType, string> = {
  stock: "Stock",
  etf: "ETF",
  crypto: "Crypto",
  commodity: "Commodity",
  bond: "Bond",
};

const ASSET_COLORS: Record<AssetType, string> = {
  stock:     "text-blue-400 bg-blue-500/10 border-blue-500/20",
  etf:       "text-purple-400 bg-purple-500/10 border-purple-500/20",
  crypto:    "text-amber-400 bg-amber-500/10 border-amber-500/20",
  commodity: "text-orange-400 bg-orange-500/10 border-orange-500/20",
  bond:      "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
};

const emptyAddForm = {
  ticker: "",
  name: "",
  asset_type: "stock" as AssetType,
  quantity: "",
  cost_basis_usd: "",
};

interface TickerSuggestion {
  ticker: string;
  name: string;
  asset_type: AssetType;
}

interface EditState {
  id: string;
  name: string;
  quantity: string;
  cost_basis_usd: string;
}

interface LivePrice {
  price: number | null;
  currency: string;
  loading: boolean;
}

function fmt(value: number, decimals = 2): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export default function PortfolioPage() {
  const [holdings, setHoldings]     = useState<PortfolioHolding[]>([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm]       = useState(emptyAddForm);
  const [addError, setAddError]     = useState<string | null>(null);
  const [addBusy, setAddBusy]       = useState(false);
  const [editState, setEditState]   = useState<EditState | null>(null);
  const [editBusy, setEditBusy]     = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Live prices keyed by ticker
  const [livePrices, setLivePrices] = useState<Record<string, LivePrice>>({});
  const [pricesLoading, setPricesLoading] = useState(false);

  // Autocomplete state
  const [suggestions, setSuggestions]         = useState<TickerSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [suggestLoading, setSuggestLoading]   = useState(false);
  const [livePrice, setLivePrice]             = useState<number | null>(null);
  const [liveCurrency, setLiveCurrency]       = useState<string>("USD");
  const [autoValue, setAutoValue]             = useState(false);
  const [quoteFetching, setQuoteFetching]     = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch holdings then batch-fetch live prices
  useEffect(() => {
    let cancelled = false;
    api.getPortfolio()
      .then((data) => {
        if (cancelled) return;
        setHoldings(data);
        if (data.length > 0) {
          fetchLivePrices(data.map((h) => h.ticker));
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load portfolio");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function fetchLivePrices(tickers: string[]) {
    if (tickers.length === 0) return;
    setPricesLoading(true);
    // Optimistically mark all as loading
    setLivePrices((prev) => {
      const next = { ...prev };
      for (const t of tickers) {
        next[t] = { price: prev[t]?.price ?? null, currency: prev[t]?.currency ?? "USD", loading: true };
      }
      return next;
    });
    try {
      const quotes = await api.getPortfolioQuotes(tickers);
      setLivePrices((prev) => {
        const next = { ...prev };
        for (const q of quotes) {
          next[q.ticker] = { price: q.price, currency: q.currency, loading: false };
        }
        return next;
      });
    } catch {
      // Mark all as loaded with null price on failure
      setLivePrices((prev) => {
        const next = { ...prev };
        for (const t of tickers) {
          next[t] = { price: null, currency: "USD", loading: false };
        }
        return next;
      });
    } finally {
      setPricesLoading(false);
    }
  }

  /* ── Autocomplete ── */
  function resetAddForm() {
    setAddForm(emptyAddForm);
    setSuggestions([]);
    setShowSuggestions(false);
    setLivePrice(null);
    setAutoValue(false);
    setAddError(null);
  }

  function handleTickerInput(value: string) {
    setAddForm((f) => ({ ...f, ticker: value }));
    setLivePrice(null);
    setAutoValue(false);

    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!value.trim()) { setSuggestions([]); setShowSuggestions(false); return; }

    searchTimer.current = setTimeout(async () => {
      setSuggestLoading(true);
      try {
        const results = await api.searchTickers(value.trim());
        setSuggestions(results as TickerSuggestion[]);
        setShowSuggestions(results.length > 0);
      } catch {
        setSuggestions([]);
      } finally {
        setSuggestLoading(false);
      }
    }, 300);
  }

  async function handleSelectSuggestion(s: TickerSuggestion) {
    setAddForm((f) => ({ ...f, ticker: s.ticker, name: s.name, asset_type: s.asset_type }));
    setShowSuggestions(false);
    setSuggestions([]);
    setQuoteFetching(true);
    try {
      const quote = await api.getTickerQuote(s.ticker);
      if (quote.price != null) {
        setLivePrice(quote.price);
        setLiveCurrency(quote.currency);
        setAutoValue(true);
        setAddForm((f) => {
          const qty = parseFloat(f.quantity);
          if (!isNaN(qty) && qty > 0) {
            return { ...f, cost_basis_usd: (qty * quote.price!).toFixed(2) };
          }
          return f;
        });
      }
    } catch {
      // price unavailable — user fills manually
    } finally {
      setQuoteFetching(false);
    }
  }

  function handleQuantityChange(value: string) {
    setAddForm((f) => {
      const next = { ...f, quantity: value };
      if (autoValue && livePrice != null) {
        const qty = parseFloat(value);
        next.cost_basis_usd = !isNaN(qty) && qty > 0 ? (qty * livePrice).toFixed(2) : "";
      }
      return next;
    });
  }

  /* ── Add holding ── */
  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setAddError(null);
    setAddBusy(true);
    try {
      const payload = {
        ticker:         addForm.ticker.toUpperCase(),
        name:           addForm.name,
        asset_type:     addForm.asset_type,
        quantity:       addForm.quantity       ? parseFloat(addForm.quantity)       : null,
        cost_basis_usd: addForm.cost_basis_usd ? parseFloat(addForm.cost_basis_usd) : null,
      };
      const newHolding = await api.addHolding(payload);
      setHoldings((prev) => [...prev, newHolding]);
      resetAddForm();
      setShowAddForm(false);
      // Fetch live price for the newly added ticker
      fetchLivePrices([newHolding.ticker]);
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed to add holding");
    } finally {
      setAddBusy(false);
    }
  }

  /* ── Edit holding ── */
  function startEdit(h: PortfolioHolding) {
    setEditState({
      id:             h.id,
      name:           h.name,
      quantity:       h.quantity       != null ? String(h.quantity)       : "",
      cost_basis_usd: h.cost_basis_usd != null ? String(h.cost_basis_usd) : "",
    });
  }

  async function handleEditSave() {
    if (!editState) return;
    setEditBusy(true);
    try {
      const updated = await api.updateHolding(editState.id, {
        name:           editState.name,
        quantity:       editState.quantity       ? parseFloat(editState.quantity)       : null,
        cost_basis_usd: editState.cost_basis_usd ? parseFloat(editState.cost_basis_usd) : null,
      });
      setHoldings((prev) => prev.map((h) => h.id === updated.id ? updated : h));
      setEditState(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Update failed");
    } finally {
      setEditBusy(false);
    }
  }

  /* ── Delete holding ── */
  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await api.deleteHolding(id);
      setHoldings((prev) => prev.filter((h) => h.id !== id));
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  }

  /* ── P&L helpers ── */
  function getCurrentValue(h: PortfolioHolding): number | null {
    const lp = livePrices[h.ticker];
    if (!lp || lp.price == null || h.quantity == null) return null;
    return lp.price * h.quantity;
  }

  function getPnL(h: PortfolioHolding): number | null {
    const currentValue = getCurrentValue(h);
    if (currentValue == null || h.cost_basis_usd == null) return null;
    return currentValue - h.cost_basis_usd;
  }

  const atLimit = holdings.length >= MAX_HOLDINGS;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 className="h-5 w-5 animate-spin text-amber-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-rose-800 bg-rose-950/30 p-4">
        <p className="text-sm text-rose-400">{error}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">

      {/* Header */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600 mb-1">
              Portfolio Holdings
            </p>
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-300">
                {holdings.length} / {MAX_HOLDINGS} holdings
              </span>
              {atLimit && (
                <span className="text-xs text-rose-400 border border-rose-800 bg-rose-950/40 px-2 py-0.5 rounded-full">
                  Limit reached
                </span>
              )}
              {pricesLoading && (
                <span className="text-xs text-amber-400/70 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" /> Fetching live prices…
                </span>
              )}
            </div>
          </div>
          <Button
            size="sm"
            onClick={() => { setShowAddForm((s) => !s); resetAddForm(); }}
            disabled={atLimit}
            className="gap-1.5"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Holding
          </Button>
        </div>

        {/* Add form (inline) */}
        {showAddForm && (
          <form onSubmit={handleAdd} className="mt-4 border-t border-slate-800 pt-4 space-y-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">

              {/* Ticker autocomplete */}
              <div className="space-y-1.5 sm:col-span-2 relative">
                <Label htmlFor="new-ticker">Ticker / Company *</Label>
                <div className="relative">
                  <Input
                    id="new-ticker"
                    placeholder="Search by ticker or company name…"
                    value={addForm.ticker}
                    autoComplete="off"
                    onChange={(e) => handleTickerInput(e.target.value)}
                    onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
                    onFocus={() => { if (suggestions.length > 0) setShowSuggestions(true); }}
                    required
                  />
                  {suggestLoading && (
                    <Loader2 className="absolute right-2.5 top-2.5 h-4 w-4 animate-spin text-slate-500" />
                  )}
                </div>

                {/* Dropdown */}
                {showSuggestions && suggestions.length > 0 && (
                  <ul className="absolute z-20 left-0 right-0 top-full mt-1 rounded-md border border-slate-700 bg-slate-900 shadow-xl overflow-hidden">
                    {suggestions.map((s) => (
                      <li key={s.ticker}>
                        <button
                          type="button"
                          onMouseDown={() => handleSelectSuggestion(s)}
                          className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-slate-800 transition-colors"
                        >
                          <span className="font-mono font-semibold text-slate-100 text-xs w-16 shrink-0">{s.ticker}</span>
                          <span className="text-slate-300 text-xs truncate flex-1">{s.name}</span>
                          <span className={`text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded border shrink-0 ${ASSET_COLORS[s.asset_type]}`}>
                            {ASSET_LABELS[s.asset_type]}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                {/* Live price indicator */}
                {(quoteFetching || livePrice != null) && (
                  <p className="text-[11px] text-amber-400/80 flex items-center gap-1 mt-0.5">
                    {quoteFetching
                      ? <><Loader2 className="h-3 w-3 animate-spin" /> Fetching live price…</>
                      : <><Zap className="h-3 w-3" /> {liveCurrency} {livePrice!.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} / unit</>
                    }
                  </p>
                )}
              </div>

              {/* Asset Type */}
              <div className="space-y-1.5">
                <Label htmlFor="new-type">Asset Type *</Label>
                <select
                  id="new-type"
                  value={addForm.asset_type}
                  onChange={(e) => setAddForm((f) => ({ ...f, asset_type: e.target.value as AssetType }))}
                  className="w-full h-9 rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-amber-500/50"
                >
                  {ASSET_TYPES.map((t) => (
                    <option key={t} value={t}>{ASSET_LABELS[t]}</option>
                  ))}
                </select>
              </div>

              {/* Name */}
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="new-name">Name *</Label>
                <Input
                  id="new-name"
                  placeholder="e.g. Apple Inc."
                  value={addForm.name}
                  onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))}
                  required
                />
              </div>

              {/* Quantity */}
              <div className="space-y-1.5">
                <Label htmlFor="new-qty">Quantity</Label>
                <Input
                  id="new-qty"
                  type="number"
                  min="0"
                  step="any"
                  placeholder="No. of units"
                  value={addForm.quantity}
                  onChange={(e) => handleQuantityChange(e.target.value)}
                />
              </div>

              {/* Cost Basis — auto-computed when live price available */}
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="new-value" className="flex items-center gap-1.5">
                  Cost Basis (USD)
                  {autoValue && (
                    <span className="text-[10px] font-semibold text-amber-400 bg-amber-500/10 border border-amber-500/20 px-1.5 py-0.5 rounded-full flex items-center gap-0.5">
                      <Zap className="h-2.5 w-2.5" /> Auto
                    </span>
                  )}
                </Label>
                <Input
                  id="new-value"
                  type="number"
                  min="0"
                  step="any"
                  placeholder="What you paid (position value)"
                  value={addForm.cost_basis_usd}
                  onChange={(e) => {
                    setAutoValue(false);
                    setAddForm((f) => ({ ...f, cost_basis_usd: e.target.value }));
                  }}
                />
              </div>
            </div>

            {addError && (
              <p className="text-xs text-rose-400">{addError}</p>
            )}

            <div className="flex gap-2 justify-end">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => { setShowAddForm(false); resetAddForm(); }}
              >
                Cancel
              </Button>
              <Button type="submit" size="sm" disabled={addBusy} className="gap-1.5">
                {addBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                Add
              </Button>
            </div>
          </form>
        )}
      </div>

      {/* Holdings table */}
      {holdings.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-10 text-center">
          <p className="text-sm text-slate-500">No holdings yet.</p>
          <p className="text-xs text-slate-600 mt-1">
            Add your first ticker to get portfolio impact analysis alongside each query.
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Cost Basis</TableHead>
                <TableHead className="text-right">Current Value</TableHead>
                <TableHead className="text-right">P&amp;L</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map((h) => {
                const isEditing = editState?.id === h.id;
                const lp = livePrices[h.ticker];
                const currentValue = getCurrentValue(h);
                const pnl = getPnL(h);
                const pnlPositive = pnl != null && pnl > 0;
                const pnlNegative = pnl != null && pnl < 0;

                return (
                  <TableRow key={h.id}>
                    <TableCell>
                      <span className="font-mono font-semibold text-slate-100 text-xs">{h.ticker}</span>
                    </TableCell>

                    <TableCell>
                      {isEditing ? (
                        <Input
                          value={editState.name}
                          onChange={(e) => setEditState((s) => s ? { ...s, name: e.target.value } : s)}
                          className="h-7 text-xs w-40"
                        />
                      ) : (
                        <span className="text-slate-300">{h.name}</span>
                      )}
                    </TableCell>

                    <TableCell>
                      <span className={`text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded border ${ASSET_COLORS[h.asset_type as AssetType]}`}>
                        {ASSET_LABELS[h.asset_type as AssetType]}
                      </span>
                    </TableCell>

                    <TableCell className="text-right">
                      {isEditing ? (
                        <Input
                          type="number"
                          min="0"
                          step="any"
                          value={editState.quantity}
                          onChange={(e) => setEditState((s) => s ? { ...s, quantity: e.target.value } : s)}
                          className="h-7 text-xs w-24 ml-auto"
                        />
                      ) : (
                        <span className="text-slate-400 font-mono text-xs">
                          {h.quantity != null ? h.quantity.toLocaleString() : "—"}
                        </span>
                      )}
                    </TableCell>

                    {/* Cost Basis */}
                    <TableCell className="text-right">
                      {isEditing ? (
                        <Input
                          type="number"
                          min="0"
                          step="any"
                          value={editState.cost_basis_usd}
                          onChange={(e) => setEditState((s) => s ? { ...s, cost_basis_usd: e.target.value } : s)}
                          className="h-7 text-xs w-28 ml-auto"
                        />
                      ) : (
                        <span className="text-slate-400 font-mono text-xs">
                          {h.cost_basis_usd != null ? `$${fmt(h.cost_basis_usd)}` : "—"}
                        </span>
                      )}
                    </TableCell>

                    {/* Current Value */}
                    <TableCell className="text-right">
                      {lp?.loading ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-600 ml-auto" />
                      ) : currentValue != null ? (
                        <span className="text-slate-200 font-mono text-xs font-medium">
                          ${fmt(currentValue)}
                        </span>
                      ) : lp && lp.price == null ? (
                        <span className="text-slate-600 text-xs">unavailable</span>
                      ) : (
                        <span className="text-slate-600 text-xs">—</span>
                      )}
                    </TableCell>

                    {/* P&L */}
                    <TableCell className="text-right">
                      {lp?.loading ? (
                        <span className="text-slate-600 text-xs">…</span>
                      ) : pnl != null ? (
                        <span className={`font-mono text-xs font-medium flex items-center justify-end gap-0.5 ${pnlPositive ? "text-emerald-400" : pnlNegative ? "text-rose-400" : "text-slate-400"}`}>
                          {pnlPositive ? <TrendingUp className="h-3 w-3" /> : pnlNegative ? <TrendingDown className="h-3 w-3" /> : <Minus className="h-3 w-3" />}
                          {pnl >= 0 ? "+" : ""}{fmt(pnl)}
                        </span>
                      ) : (
                        <span className="text-slate-600 text-xs">—</span>
                      )}
                    </TableCell>

                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        {isEditing ? (
                          <>
                            <button
                              onClick={handleEditSave}
                              disabled={editBusy}
                              className="h-7 w-7 flex items-center justify-center rounded-md text-emerald-400 hover:bg-emerald-500/10 transition-colors disabled:opacity-50 cursor-pointer"
                              title="Save"
                            >
                              {editBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                            </button>
                            <button
                              onClick={() => setEditState(null)}
                              disabled={editBusy}
                              className="h-7 w-7 flex items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 transition-colors disabled:opacity-50 cursor-pointer"
                              title="Cancel"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={() => startEdit(h)}
                              className="h-7 w-7 flex items-center justify-center rounded-md text-slate-500 hover:text-slate-200 hover:bg-slate-800 transition-colors cursor-pointer"
                              title="Edit"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() => handleDelete(h.id)}
                              disabled={deletingId === h.id}
                              className="h-7 w-7 flex items-center justify-center rounded-md text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 transition-colors disabled:opacity-50 cursor-pointer"
                              title="Delete"
                            >
                              {deletingId === h.id
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Trash2 className="h-3.5 w-3.5" />}
                            </button>
                          </>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
