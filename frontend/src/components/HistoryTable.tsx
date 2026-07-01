"use client";

import { useState } from "react";
import { Eye, Trash2, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";
import type { Confidence, HistoryItem } from "@/lib/types";

const PAGE_SIZE = 8;

type Filter = "all" | Confidence;

function confidenceVariant(c: Confidence) {
  if (c === "High") return "high";
  if (c === "Medium") return "medium";
  if (c === "Low") return "low";
  return "outline";
}

function confidenceLabel(c: Confidence) {
  return c === "insufficient_data" ? "No Data" : c;
}

interface HistoryTableProps {
  items: HistoryItem[];
  onViewReport: (item: HistoryItem) => void;
  onDelete: (id: string) => Promise<void>;
}

export function HistoryTable({ items, onViewReport, onDelete }: HistoryTableProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [page, setPage] = useState(0);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = filter === "all" ? items : items.filter((i) => i.confidence === filter);
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await onDelete(id);
    } finally {
      setDeletingId(null);
      setConfirmId(null);
    }
  }

  return (
    <div className="space-y-3">
      {/* Filters */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500">Filter:</span>
        {(["all", "High", "Medium", "Low", "insufficient_data"] as const).map((f) => (
          <button
            key={f}
            onClick={() => { setFilter(f); setPage(0); }}
            className={`rounded-full px-3 py-0.5 text-xs font-medium transition-colors ${
              filter === f
                ? "bg-blue-600 text-white"
                : "border border-slate-700 text-slate-400 hover:border-slate-600 hover:text-slate-200"
            }`}
          >
            {f === "all" ? "All" : f === "insufficient_data" ? "No Data" : f}
          </button>
        ))}
        <span className="ml-auto text-xs text-slate-600">{filtered.length} records</span>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Query</TableHead>
              <TableHead>Risk Level</TableHead>
              <TableHead>Markets Impacted</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-slate-600 py-10">
                  No records found
                </TableCell>
              </TableRow>
            ) : (
              paged.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="whitespace-nowrap text-slate-500 text-xs">
                    {formatDate(item.created_at)}
                  </TableCell>
                  <TableCell className="max-w-xs">
                    <p className="truncate text-slate-200">{item.query}</p>
                  </TableCell>
                  <TableCell>
                    <Badge variant={confidenceVariant(item.confidence)}>
                      {confidenceLabel(item.confidence)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {item.market_impacts.slice(0, 2).map((m, i) => (
                        <span key={i} className="rounded-sm bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                          {m}
                        </span>
                      ))}
                      {item.market_impacts.length > 2 && (
                        <span className="text-[10px] text-slate-600">
                          +{item.market_impacts.length - 2}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      {confirmId === item.id ? (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-slate-400 hover:text-slate-200 text-xs h-7 px-2"
                            onClick={() => setConfirmId(null)}
                            disabled={deletingId === item.id}
                          >
                            Cancel
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-red-400 hover:text-red-300 hover:bg-red-950/40 text-xs h-7 px-2"
                            onClick={() => handleDelete(item.id)}
                            disabled={deletingId === item.id}
                          >
                            {deletingId === item.id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              "Delete"
                            )}
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => onViewReport(item)}>
                            <Eye className="h-3.5 w-3.5" />
                            View
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-slate-600 hover:text-red-400"
                            onClick={() => setConfirmId(item.id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="text-xs text-slate-500">
            {page + 1} / {totalPages}
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}
