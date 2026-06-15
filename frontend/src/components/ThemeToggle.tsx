"use client";

import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

export function ThemeToggle() {
  // Lazy initializer reads localStorage once on the client; SSR defaults to dark
  const [isDark, setIsDark] = useState(() => {
    if (typeof window === "undefined") return true;
    return localStorage.getItem("georisk-theme") !== "light";
  });

  // Sync DOM class whenever isDark changes — no setState inside this effect
  useEffect(() => {
    document.documentElement.classList.toggle("light", !isDark);
  }, [isDark]);

  const toggle = () => {
    const nowDark = !isDark;
    setIsDark(nowDark);
    document.documentElement.classList.toggle("light", !nowDark);
    localStorage.setItem("georisk-theme", nowDark ? "dark" : "light");
  };

  return (
    <button
      onClick={toggle}
      className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-800 bg-slate-900 text-slate-500 hover:text-amber-400 hover:border-amber-600/40 transition-colors"
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark
        ? <Sun  className="h-3.5 w-3.5" />
        : <Moon className="h-3.5 w-3.5" />}
    </button>
  );
}
