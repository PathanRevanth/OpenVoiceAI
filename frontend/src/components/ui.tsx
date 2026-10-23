"use client";

/**
 * Shared UI primitives. Editorial, hairline-divided layouts: numbers and
 * headings breathe in plain layout; boxed surfaces only where elevation
 * carries real hierarchy (composer, drawer, interactive selectors).
 */

import { useEffect, useState } from "react";
import { Icon, type IconName } from "@/components/icons";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className=" flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-xl">
        <h1 className="display-1 text-[var(--text-1)]">{title}</h1>
        <p className="mt-2.5 text-[13.5px] leading-relaxed text-[var(--text-2)]">
          {description}
        </p>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function SectionHead({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="display-2 text-[var(--text-1)]">{children}</h2>
  );
}

export type ChipTone = "ok" | "amber" | "danger" | "accent" | "neutral";

const TONES: Record<ChipTone, { bg: string; fg: string }> = {
  ok: { bg: "var(--ok-dim)", fg: "var(--ok)" },
  amber: { bg: "var(--warn-dim)", fg: "var(--warn)" },
  danger: { bg: "var(--danger-dim)", fg: "var(--danger)" },
  accent: { bg: "var(--accent-dim)", fg: "var(--accent)" },
  neutral: { bg: "var(--panel-hover)", fg: "var(--text-2)" },
};

export function Chip({
  tone = "neutral",
  children,
  dot,
  pulse,
}: {
  tone?: ChipTone;
  children: React.ReactNode;
  dot?: boolean;
  pulse?: boolean;
}) {
  const t = TONES[tone];
  return (
    <span
      className="cell inline-flex items-center gap-1.5 rounded-[3px] px-1.5 py-0.5 text-[12px] font-semibold font-medium"
      style={{ background: t.bg, color: t.fg }}
    >
      {dot && (
        <span
          className={`h-1.5 w-1.5 rounded-[1px] ${pulse ? "animate-soft-pulse" : ""}`}
          style={{ background: t.fg }}
        />
      )}
      {children}
    </span>
  );
}

/**
 * StatStrip: figures in plain layout, separated by hairlines. No boxes.
 * One `stat-figure` display type per number, label above in small caps
 * (the single permitted label style per page section).
 */
export function StatStrip({
  items,
}: {
  items: { label: string; value: string; sub?: string; tone?: ChipTone }[];
}) {
  const t = (tone?: ChipTone) => (tone ? TONES[tone].fg : "var(--text-1)");
  return (
    <dl className="flex flex-wrap items-baseline gap-x-10 gap-y-4 border-y border-[var(--hairline)] py-4">
      {items.map((s) => (
        <div key={s.label} className="flex items-baseline gap-2.5">
          <dt className="text-[11.5px] font-medium text-[var(--text-3)]">{s.label}</dt>
          <dd
            className="tabular text-[1.05rem] font-semibold leading-none"
            style={{ color: t(s.tone) }}
          >
            {s.value}
          </dd>
          {s.sub && (
            <dd className="text-[11px] text-[var(--text-3)]">({s.sub})</dd>
          )}
        </div>
      ))}
    </dl>
  );
}

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`fluid relative h-[22px] w-[38px] shrink-0 rounded-full ${
        disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer"
      }`}
      style={{ background: checked ? "var(--accent)" : "var(--hairline-strong)" }}
    >
      <span
        className="fluid absolute top-[3px] h-4 w-4 rounded-full bg-white shadow-sm"
        style={{ left: checked ? 19 : 3 }}
      />
    </button>
  );
}

export function EmptyState({
  icon,
  title,
  body,
}: {
  icon: IconName;
  title: string;
  body: string;
}) {
  return (
    <div className="flex flex-col items-center border border-dashed border-[var(--hairline-strong)] rounded-2xl px-6 py-16 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl bg-[var(--accent-dim)]">
        <Icon name={icon} size={20} className="text-[var(--accent)]" />
      </span>
      <p className="mt-4 text-[14px] font-semibold text-[var(--text-1)]">{title}</p>
      <p className="mt-1.5 max-w-sm text-[12.5px] leading-relaxed text-[var(--text-3)]">{body}</p>
    </div>
  );
}

/** Slide-over drawer from the right, with scrim. Transform-based motion. */
export function Drawer({
  open,
  onClose,
  title,
  children,
  width = 540,
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  width?: number;
}) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) setMounted(true);
  }, [open]);
  if (!mounted) return null;
  return (
    <>
      <button
        aria-label="Close panel"
        onClick={onClose}
        className="fixed inset-0 z-40 cursor-default bg-[var(--scrim)] backdrop-blur-[2px]"
        style={{ opacity: open ? 1 : 0, transition: "opacity 280ms var(--ease)" }}
        onAnimationEnd={() => {
          if (!open) setMounted(false);
        }}
      />
      <aside
        className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-[var(--hairline)] bg-[var(--bg-raised)] shadow-[var(--shadow-pop)]"
        style={{
          maxWidth: width,
          transform: open ? "translateX(0)" : "translateX(60px)",
          opacity: open ? 1 : 0,
          transition: "transform 340ms var(--ease), opacity 340ms var(--ease)",
        }}
        role="dialog"
      >
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--hairline)] px-5">
          <div className="min-w-0">{title}</div>
          <button
            onClick={onClose}
            className="press fluid grid h-8 w-8 place-items-center rounded-lg text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
            aria-label="Close"
          >
            <Icon name="x" size={15} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </>
  );
}

/** Read-only value with a copy affordance; shows a transient Copied state. */
export function CopyField({
  value,
  mono = true,
  className = "",
}: {
  value: string;
  mono?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      className={
        "fluid flex items-center gap-2 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 py-2 " +
        className
      }
    >
      <code
        className={
          "min-w-0 flex-1 truncate text-[11.5px] text-[var(--text-2)] " + (mono ? "font-mono" : "")
        }
      >
        {value}
      </code>
      <button
        onClick={() => {
          navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1600);
            },
            () => {},
          );
        }}
        className="press fluid shrink-0 rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
        aria-label="Copy"
      >
        {copied ? (
          <Icon name="check" size={13} strokeWidth={2} className="text-[var(--ok)]" />
        ) : (
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <rect x="9" y="9" width="12" height="12" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
    </div>
  );
}

export function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}
