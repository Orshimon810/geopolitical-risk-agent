"use client";

import { LogOut, User, Activity } from "lucide-react";
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
import { useAuth } from "@/context/AuthContext";

interface NavbarProps {
  title: string;
}

export function Navbar({ title }: NavbarProps) {
  const { logout, token } = useAuth();

  // Decode JWT payload (no verification — display only)
  let email: string | null = null;
  let initials = "U";
  try {
    if (token) {
      const payload = JSON.parse(atob(token.split(".")[1]));
      email = payload.sub ?? null;
      if (email) initials = email.slice(0, 2).toUpperCase();
    }
  } catch {
    // malformed token — use fallback
  }

  return (
    <header className="flex h-14 items-center justify-between border-b border-slate-800 bg-slate-950/90 backdrop-blur px-5">
      {/* Left: page title */}
      <div className="flex items-center gap-3">
        <h1 className="text-xs font-semibold text-slate-200 tracking-widest uppercase data-mono">
          {title}
        </h1>
      </div>

      {/* Right: indicators + user */}
      <div className="flex items-center gap-2.5">
        {/* Rate limit indicator */}
        <div className="hidden sm:flex items-center gap-1.5 rounded border border-slate-800 bg-slate-900 px-2.5 py-1">
          <Activity className="h-3 w-3 text-amber-500" />
          <span className="text-[10px] text-amber-500/80 font-medium data-mono">5 req/hr</span>
        </div>

        {/* User menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-slate-800 transition-colors group">
              <Avatar className="h-6 w-6 border border-slate-700 group-hover:border-amber-600/40 transition-colors">
                <AvatarFallback className="text-[10px] font-bold bg-amber-500/10 text-amber-400 data-mono">
                  {initials}
                </AvatarFallback>
              </Avatar>
              {email && (
                <span className="hidden md:block text-[11px] text-slate-500 data-mono max-w-[140px] truncate">
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
