"use client";

/**
 * Pinned projects, persisted locally like pinned conversations. Pure helpers
 * plus a small external store so pins stay in sync without prop drilling.
 */

import { useMemo, useSyncExternalStore } from "react";

const PIN_KEY = "vf:pinned-projects";

const listeners = new Set<() => void>();

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  const onStorage = (e: StorageEvent) => {
    if (e.key === PIN_KEY) callback();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(callback);
    window.removeEventListener("storage", onStorage);
  };
}

function readRaw(): string {
  try {
    return window.localStorage.getItem(PIN_KEY) ?? "[]";
  } catch {
    return "[]";
  }
}

function parse(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** Toggle a project's pinned state (persisted; notifies subscribers). */
export function toggleProjectPin(id: string): void {
  const current = parse(readRaw());
  const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
  window.localStorage.setItem(PIN_KEY, JSON.stringify(next));
  listeners.forEach((listener) => listener());
}

const EMPTY_RAW = "[]";

/** Pinned project ids. The snapshot is the raw string, so React sees a stable value. */
export function usePinnedProjectIds(): string[] {
  const raw = useSyncExternalStore(subscribe, readRaw, () => EMPTY_RAW);
  return useMemo(() => parse(raw), [raw]);
}
