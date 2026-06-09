"use client";

import { Activity, LogOut, User, Wifi } from "lucide-react";
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
  const { logout } = useAuth();

  return (
    <header className="flex h-14 items-center justify-between border-b border-slate-800 bg-slate-950/80 backdrop-blur px-5">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-semibold text-slate-100">{title}</h1>
      </div>

      <div className="flex items-center gap-3">
        {/* System status */}
        <div className="hidden sm:flex items-center gap-1.5 rounded-full border border-emerald-800 bg-emerald-950 px-2.5 py-1">
          <Wifi className="h-3 w-3 text-emerald-400" />
          <span className="text-xs text-emerald-400 font-medium">API Online</span>
        </div>

        <div className="flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1">
          <Activity className="h-3 w-3 text-blue-400" />
          <span className="text-xs text-blue-400 font-medium">5 req/hr</span>
        </div>

        {/* User menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-lg p-1.5 hover:bg-slate-800 transition-colors">
              <Avatar className="h-7 w-7">
                <AvatarFallback>U</AvatarFallback>
              </Avatar>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>My Account</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem>
              <User className="h-3.5 w-3.5" />
              Profile
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Badge variant="blue" className="text-[10px] px-1.5 py-0">Free Tier</Badge>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={logout} className="text-rose-400 hover:text-rose-300 hover:bg-rose-950">
              <LogOut className="h-3.5 w-3.5" />
              Logout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
