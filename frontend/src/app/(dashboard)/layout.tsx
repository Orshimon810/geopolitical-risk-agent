"use client";

import { useState, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { Navbar } from "@/components/Navbar";
import { useAuth } from "@/context/AuthContext";

const PAGE_TITLES: Record<string, string> = {
  "/analysis":  "New Analysis",
  "/portfolio": "My Portfolio",
  "/history":   "Analysis History",
};

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isAuthenticated, isLoading, router]);

  // Close mobile sidebar on route change (setTimeout avoids synchronous setState in effect)
  useEffect(() => {
    const t = setTimeout(() => setMobileOpen(false), 0);
    return () => clearTimeout(t);
  }, [pathname]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950">
        <div className="h-5 w-5 rounded-full border-2 border-amber-500 border-t-transparent animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) return null;

  const title = PAGE_TITLES[pathname] ?? "GeoRisk AI";

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((c) => !c)}
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />
      <div className="flex flex-1 flex-col overflow-hidden min-w-0">
        <Navbar
          title={title}
          onMobileSidebarOpen={() => setMobileOpen(true)}
        />
        <main className="flex-1 overflow-y-auto p-4 md:p-5">{children}</main>
      </div>
    </div>
  );
}
