"use client";

import { LogOut, User, Activity, Menu } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/context/AuthContext";

interface NavbarProps {
  title: string;
  onMobileSidebarOpen: () => void;
  mobileOpen?: boolean;
}

export function Navbar({ title, onMobileSidebarOpen, mobileOpen = false }: NavbarProps) {
  const { logout, token } = useAuth();

  // Decode JWT payload for display (no verification needed — display only)
  let email: string | null = null;
  let initials = "U";
  try {
    if (token) {
      const payload = JSON.parse(atob(token.split(".")[1]));
      email = payload.email ?? null;
      if (email) initials = email.slice(0, 2).toUpperCase();
    }
  } catch {
    // malformed token — use fallback
  }

  return (
    <header className="flex h-14 items-center justify-between border-b border-slate-800 bg-slate-950/90 backdrop-blur px-4 gap-3">
      {/* Left: hamburger (mobile only) + page title */}
      <div className="flex items-center gap-3 min-w-0">
        <button
          className="md:hidden flex h-11 w-11 items-center justify-center rounded-md border border-slate-800 bg-slate-900 text-slate-500 hover:text-amber-400 hover:border-amber-600/40 transition-colors shrink-0 cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50"
          onClick={onMobileSidebarOpen}
          aria-label="Open sidebar"
          aria-expanded={mobileOpen}
          aria-controls="mobile-sidebar"
        >
          <Menu className="h-4 w-4" />
        </button>
        <h1 className="text-xs font-semibold text-slate-200 tracking-widest uppercase data-mono truncate">
          {title}
        </h1>
      </div>

      {/* Right: theme toggle + rate limit + user menu */}
      <div className="flex items-center gap-2 shrink-0">
        <ThemeToggle />

        {/* Rate limit indicator — hidden on very small screens */}
        <div className="hidden sm:flex items-center gap-1.5 rounded border border-slate-800 bg-slate-900 px-2.5 py-1">
          <Activity className="h-3 w-3 text-amber-500" />
          <span className="text-[10px] text-amber-500/80 font-medium data-mono">5 req/hr</span>
        </div>

        {/* User menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-slate-800 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50 group">
              <Avatar className="h-6 w-6 border border-slate-700 group-hover:border-amber-600/40 transition-colors">
                <AvatarFallback className="text-[10px] font-bold bg-amber-500/10 text-amber-400 data-mono">
                  {initials}
                </AvatarFallback>
              </Avatar>
              {email && (
                <span className="hidden lg:block text-[11px] text-slate-500 data-mono max-w-[140px] truncate">
                  {email}
                </span>
              )}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuLabel className="data-mono text-[10px] uppercase tracking-widest text-slate-500 font-normal">
              Account
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem>
              <User className="h-3.5 w-3.5" />
              Profile
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Badge variant="blue" className="text-[10px] px-1.5 py-0">Free Tier</Badge>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={logout}
              className="text-rose-400 hover:text-rose-300 hover:bg-rose-950/40"
            >
              <LogOut className="h-3.5 w-3.5" />
              Logout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
