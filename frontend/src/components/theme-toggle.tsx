"use client";

/**
 * Theme toggle: flips data-theme on <html>, persists to localStorage.
 * Bootstrap in layout.tsx applies it before paint (no flash).
 */

import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";

export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    setTheme(
      document.documentElement.dataset.theme === "dark" ? "dark" : "light",
    );
  }, []);

  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem("vf-theme", next);
    setTheme(next);
  };

  return (
    <button
      onClick={flip}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      title={theme === "dark" ? "Light theme" : "Dark theme"}
      className="press fluid grid h-8 w-8 place-items-center rounded-lg text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={15} />
    </button>
  );
}
