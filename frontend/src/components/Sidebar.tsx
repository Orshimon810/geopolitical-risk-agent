"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Globe, History, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/analysis", label: "New Analysis", icon: Globe },
  { href: "/history",  label: "History",      icon: History },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        "relative flex flex-col border-r border-slate-800 bg-slate-950 transition-all duration-300 ease-in-out",
        collapsed ? "w-16" : "w-56"
      )}
    >
      {/* Logo */}
      <div className="flex h-14 items-center border-b border-slate-800 px-3">
        <div className="flex items-center gap-2.5">
          {/* Amber globe mark */}
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-amber-600/30 bg-amber-500/10">
            <Globe className="h-3.5 w-3.5 text-amber-400" />
          </div>
          {!collapsed && (
            <span className="text-sm font-bold text-slate-100 tracking-tight whitespace-nowrap data-mono">
              GeoRisk<span className="text-amber-400">_</span>AI
            </span>
          )}
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-1 p-2 flex-1">
        {!collapsed && (
          <p className="px-3 pt-2 pb-1 text-[9px] font-bold uppercase tracking-[0.2em] text-slate-700 data-mono">
            Workspace
          </p>
        )}
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-amber-500/8 text-amber-400 border border-amber-600/20"
                  : "text-slate-500 hover:bg-slate-800 hover:text-slate-200"
              )}
            >
              <Icon className={cn("h-4 w-4 shrink-0", active ? "text-amber-400" : "text-slate-600")} />
              {!collapsed && <span className={cn("text-xs", active ? "text-amber-400" : "")}>{label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Status dot at bottom */}
      {!collapsed && (
        <div className="px-3 pb-4">
          <div className="flex items-center gap-2 px-3 py-2 rounded-md border border-slate-800">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 shrink-0" />
            <span className="text-[10px] text-slate-600 data-mono">API Online</span>
          </div>
        </div>
      )}

      {/* Collapse toggle */}
      <button
        onClick={onToggle}
        className="absolute -right-3 top-1/2 -translate-y-1/2 flex h-6 w-6 items-center justify-center rounded-full border border-slate-700 bg-slate-900 text-slate-500 hover:text-amber-400 hover:border-amber-600/40 transition-colors z-10"
      >
        {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronLeft className="h-3 w-3" />}
      </button>
    </aside>
  );
}
