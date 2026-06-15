"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Globe, History, Briefcase, ChevronLeft, ChevronRight, X } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/analysis",  label: "New Analysis", icon: Globe },
  { href: "/portfolio", label: "Portfolio",     icon: Briefcase },
  { href: "/history",   label: "History",       icon: History },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export function Sidebar({ collapsed, onToggle, mobileOpen, onMobileClose }: SidebarProps) {
  const pathname = usePathname();

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={onMobileClose}
        />
      )}

      <aside
        id="mobile-sidebar"
        className={cn(
          // Base
          "flex flex-col border-r border-slate-800 bg-slate-950 transition-all duration-300 ease-in-out",
          // Mobile: fixed overlay, slides in from left
          "fixed inset-y-0 left-0 z-50",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          // Desktop: static in flex layout, always visible
          "md:relative md:translate-x-0 md:z-auto",
          // Width: full-ish on mobile drawer, collapsed/expanded on desktop
          "w-64",
          collapsed ? "md:w-16" : "md:w-56"
        )}
      >
        {/* Logo */}
        <div className="flex h-14 items-center justify-between border-b border-slate-800 px-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-amber-600/30 bg-amber-500/10">
              <Globe className="h-3.5 w-3.5 text-amber-400" />
            </div>
            {(!collapsed || mobileOpen) && (
              <span className="text-sm font-bold text-slate-100 tracking-tight whitespace-nowrap data-mono">
                GeoRisk<span className="text-amber-400">_</span>AI
              </span>
            )}
          </div>

          {/* Mobile close button */}
          <button
            className="md:hidden flex h-11 w-11 items-center justify-center rounded-md text-slate-500 hover:text-slate-200 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50"
            onClick={onMobileClose}
            aria-label="Close sidebar"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex flex-col gap-1 p-2 flex-1">
          {(!collapsed || mobileOpen) && (
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
                onClick={onMobileClose}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-amber-500/8 text-amber-400 border border-amber-600/20"
                    : "text-slate-500 hover:bg-slate-800 hover:text-slate-200"
                )}
              >
                <Icon className={cn("h-4 w-4 shrink-0", active ? "text-amber-400" : "text-slate-600")} />
                {(!collapsed || mobileOpen) && (
                  <span className={cn("text-xs", active ? "text-amber-400" : "")}>{label}</span>
                )}
              </Link>
            );
          })}
        </nav>

        {/* Status dot */}
        {(!collapsed || mobileOpen) && (
          <div className="px-3 pb-4">
            <div className="flex items-center gap-2 px-3 py-2 rounded-md border border-slate-800">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 shrink-0" />
              <span className="text-[10px] text-slate-600 data-mono">API Online</span>
            </div>
          </div>
        )}

        {/* Desktop collapse toggle */}
        <button
          onClick={onToggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="hidden md:flex absolute -right-3 top-1/2 -translate-y-1/2 h-6 w-6 items-center justify-center rounded-full border border-slate-700 bg-slate-900 text-slate-500 hover:text-amber-400 hover:border-amber-600/40 transition-colors z-10 cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50"
        >
          {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronLeft className="h-3 w-3" />}
        </button>
      </aside>
    </>
  );
}
