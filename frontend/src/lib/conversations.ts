/**
 * Conversation organization for the sidebar and command palette: recency
 * grouping and a locally persisted pin list. Pure helpers plus a small
 * external store, so pins stay in sync across components without props drilling.
 */

import { useMemo, useSyncExternalStore } from "react";

export interface ConversationGroup<T> {
  label: string;
  items: T[];
}

const PIN_KEY = "vf:pinned";

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * Pinned first, then Today / Yesterday / Previous 7 days / Previous 30 days /
 * Older. Empty groups are dropped. Items keep the order they arrived in
 * (the backend sorts by recency).
 */
export function groupConversations<T extends { id: string; updated_at: string }>(
  items: T[],
  pinned: readonly string[],
  now: Date = new Date(),
): ConversationGroup<T>[] {
  const pinSet = new Set(pinned);
  const today = startOfDay(now);
  const buckets: Record<string, T[]> = {
    Pinned: [],
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    "Previous 30 days": [],
    Older: [],
  };
  for (const item of items) {
    if (pinSet.has(item.id)) {
      buckets.Pinned.push(item);
      continue;
    }
    const stamp = new Date(item.updated_at);
    const age = Number.isNaN(stamp.getTime()) ? Infinity : Math.round((today - startOfDay(stamp)) / 86_400_000);
    if (age <= 0) buckets.Today.push(item);
    else if (age === 1) buckets.Yesterday.push(item);
    else if (age <= 7) buckets["Previous 7 days"].push(item);
    else if (age <= 30) buckets["Previous 30 days"].push(item);
    else buckets.Older.push(item);
  }
  return Object.entries(buckets)
    .filter(([, group]) => group.length > 0)
    .map(([label, group]) => ({ label, items: group }));
}

const listeners = new Set<() => void>();

function subscribePins(callback: () => void): () => void {
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

function readPinsRaw(): string {
  try {
    return window.localStorage.getItem(PIN_KEY) ?? "[]";
  } catch {
    return "[]";
  }
}

function parsePins(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** Toggle a conversation's pinned state (persisted; notifies subscribers). */
export function togglePin(id: string): void {
  const current = parsePins(readPinsRaw());
  const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
  window.localStorage.setItem(PIN_KEY, JSON.stringify(next));
  listeners.forEach((listener) => listener());
}

const EMPTY_RAW = "[]";

/** Pinned conversation ids. The snapshot is the raw string, so React sees a stable value. */
export function usePinnedIds(): string[] {
  const raw = useSyncExternalStore(subscribePins, readPinsRaw, () => EMPTY_RAW);
  return useMemo(() => parsePins(raw), [raw]);
}
