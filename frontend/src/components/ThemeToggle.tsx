"use client";

import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

export function ThemeToggle() {
  const [isDark, setIsDark] = useState(true);

  useEffect(() => {
    const stored = localStorage.getItem("georisk-theme");
    const dark = stored !== "light";
    document.documentElement.classList.toggle("light", !dark);
    const t = setTimeout(() => setIsDark(dark), 0);
    return () => clearTimeout(t);
  }, []);

  const toggle = () => {
    const nowDark = !isDark;
    setIsDark(nowDark);
    document.documentElement.classList.toggle("light", !nowDark);
    localStorage.setItem("georisk-theme", nowDark ? "dark" : "light");
  };

  return (
    <button
      onClick={toggle}
      className="flex h-11 w-11 md:h-7 md:w-7 shrink-0 items-center justify-center rounded-md border border-slate-800 bg-slate-900 text-slate-500 hover:text-amber-400 hover:border-amber-600/40 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500/50"
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark
        ? <Sun  className="h-3.5 w-3.5" />
        : <Moon className="h-3.5 w-3.5" />}
    </button>
  );
}
