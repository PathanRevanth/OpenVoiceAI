"use client";

/**
 * Brand marks for model providers, from frontend/public/logos. Both variants are
 * rendered and CSS shows the one matching the active theme (data-theme on <html>),
 * so the mark switches with the app's own light/dark toggle, not the OS setting.
 * Providers without a supplied mark fall back to their colored tile.
 */

import { cn } from "@/lib/utils";

interface Mark {
  light: string;
  dark: string;
}

const MARKS: Record<string, Mark> = {
  openai: { light: "/logos/openai-light.svg", dark: "/logos/openai-dark.svg" },
  anthropic: { light: "/logos/claude.svg", dark: "/logos/claude.svg" },
  google: { light: "/logos/gemini.svg", dark: "/logos/gemini.svg" },
};

const ALIASES: Record<string, string> = {
  "google-gla": "google",
  "google-vertex": "google",
};

/** Brand marks for specialist agents, from frontend/public/logos. Same
 * theme-switching treatment as provider marks; agents without a supplied
 * mark (orchestrator, research, marketing) return null so callers fall back
 * to their lucide glyph. */
const AGENT_MARKS: Record<string, Mark> = {
  github: { light: "/logos/github-light.svg", dark: "/logos/github-dark.svg" },
  slack: { light: "/logos/slack.svg", dark: "/logos/slack.svg" },
  mongodb: { light: "/logos/mongodb-badge.svg", dark: "/logos/mongodb-badge.svg" },
};

export function agentMark(slug: string | null | undefined): Mark | null {
  if (!slug) return null;
  return AGENT_MARKS[slug.toLowerCase()] ?? null;
}

export function AgentLogo({
  slug,
  size = 14,
  className,
}: {
  slug: string;
  size?: number;
  className?: string;
}) {
  const mark = agentMark(slug);
  if (!mark) return null;
  return (
    <span
      aria-hidden
      className={cn("inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG brand mark */}
      <img src={mark.light} alt="" width={size} height={size} className="logo-theme-light" />
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG brand mark */}
      <img src={mark.dark} alt="" width={size} height={size} className="logo-theme-dark" />
    </span>
  );
}

/** Provider id from a pydantic-ai model string such as `anthropic:claude-haiku-4-5`. */
export function providerFromModel(model: string | null | undefined): string {
  const head = (model ?? "").split(":", 1)[0].toLowerCase();
  return ALIASES[head] ?? head;
}

export function ProviderLogo({
  provider,
  size = 14,
  fallbackTile,
  className,
}: {
  provider: string;
  size?: number;
  fallbackTile?: string;
  className?: string;
}) {
  const mark = MARKS[ALIASES[provider] ?? provider];
  if (!mark) {
    return (
      <span
        aria-hidden
        className={cn("inline-block shrink-0 rounded-full", className)}
        style={{ width: size * 0.55, height: size * 0.55, background: fallbackTile ?? "var(--text-3)" }}
      />
    );
  }
  return (
    <span
      aria-hidden
      className={cn("inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG brand mark */}
      <img src={mark.light} alt="" width={size} height={size} className="logo-theme-light" />
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG brand mark */}
      <img src={mark.dark} alt="" width={size} height={size} className="logo-theme-dark" />
    </span>
  );
}

/** Square provider tile: the brand mark on a neutral chip, or the colored initial when no mark exists. */
export function ProviderTile({
  provider,
  label,
  tile,
  className,
}: {
  provider: string;
  label: string;
  tile?: string;
  className?: string;
}) {
  const hasMark = Boolean(MARKS[ALIASES[provider] ?? provider]);
  if (hasMark) {
    return (
      <span
        className={cn(
          "grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--panel-hover)] ring-1 ring-[var(--hairline)]",
          className,
        )}
      >
        <ProviderLogo provider={provider} size={18} />
      </span>
    );
  }
  return (
    <span
      className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg text-[12px] font-bold text-white", className)}
      style={{ background: tile ?? "var(--text-3)" }}
    >
      {label.slice(0, 1)}
    </span>
  );
}
