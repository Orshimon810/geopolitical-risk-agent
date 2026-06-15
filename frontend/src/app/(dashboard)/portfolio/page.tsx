"use client";

import { useState, useEffect } from "react";
import { Plus, Pencil, Trash2, Loader2, Check, X } from "lucide-react";
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
  value_usd: "",
};

interface EditState {
  id: string;
  name: string;
  quantity: string;
  value_usd: string;
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

  useEffect(() => {
    let cancelled = false;
    api.getPortfolio()
      .then((data) => { if (!cancelled) setHoldings(data); })
      .catch((e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load portfolio"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  /* ── Add holding ── */
  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setAddError(null);
    setAddBusy(true);
    try {
      const payload = {
        ticker:     addForm.ticker.toUpperCase(),
        name:       addForm.name,
        asset_type: addForm.asset_type,
        quantity:   addForm.quantity   ? parseFloat(addForm.quantity)   : null,
        value_usd:  addForm.value_usd  ? parseFloat(addForm.value_usd)  : null,
      };
      const newHolding = await api.addHolding(payload);
      setHoldings((prev) => [...prev, newHolding]);
      setAddForm(emptyAddForm);
      setShowAddForm(false);
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed to add holding");
    } finally {
      setAddBusy(false);
    }
  }

  /* ── Edit holding ── */
  function startEdit(h: PortfolioHolding) {
    setEditState({
      id:        h.id,
      name:      h.name,
      quantity:  h.quantity  != null ? String(h.quantity)  : "",
      value_usd: h.value_usd != null ? String(h.value_usd) : "",
    });
  }

  async function handleEditSave() {
    if (!editState) return;
    setEditBusy(true);
    try {
      const updated = await api.updateHolding(editState.id, {
        name:      editState.name,
        quantity:  editState.quantity  ? parseFloat(editState.quantity)  : null,
        value_usd: editState.value_usd ? parseFloat(editState.value_usd) : null,
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
            </div>
          </div>
          <Button
            size="sm"
            onClick={() => { setShowAddForm((s) => !s); setAddError(null); }}
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
              <div className="space-y-1.5">
                <Label htmlFor="new-ticker">Ticker *</Label>
                <Input
                  id="new-ticker"
                  placeholder="e.g. AAPL, BTC-USD"
                  value={addForm.ticker}
                  onChange={(e) => setAddForm((f) => ({ ...f, ticker: e.target.value.toUpperCase() }))}
                  required
                />
              </div>
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
              <div className="space-y-1.5">
                <Label htmlFor="new-qty">Quantity (optional)</Label>
                <Input
                  id="new-qty"
                  type="number"
                  min="0"
                  step="any"
                  placeholder="No. of units"
                  value={addForm.quantity}
                  onChange={(e) => setAddForm((f) => ({ ...f, quantity: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="new-value">Value USD (optional)</Label>
                <Input
                  id="new-value"
                  type="number"
                  min="0"
                  step="any"
                  placeholder="Position value"
                  value={addForm.value_usd}
                  onChange={(e) => setAddForm((f) => ({ ...f, value_usd: e.target.value }))}
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
                onClick={() => { setShowAddForm(false); setAddForm(emptyAddForm); setAddError(null); }}
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
                <TableHead className="text-right">Quantity</TableHead>
                <TableHead className="text-right">Value (USD)</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map((h) => {
                const isEditing = editState?.id === h.id;
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

                    <TableCell className="text-right">
                      {isEditing ? (
                        <Input
                          type="number"
                          min="0"
                          step="any"
                          value={editState.value_usd}
                          onChange={(e) => setEditState((s) => s ? { ...s, value_usd: e.target.value } : s)}
                          className="h-7 text-xs w-28 ml-auto"
                        />
                      ) : (
                        <span className="text-slate-400 font-mono text-xs">
                          {h.value_usd != null ? `$${h.value_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
                        </span>
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
