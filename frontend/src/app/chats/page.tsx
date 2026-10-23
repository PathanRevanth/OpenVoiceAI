"use client";

/**
 * Chats and tasks - the full conversation library, like the reference.
 *
 * The sidebar and the command palette show only the 10 latest; this page
 * lists everything with search, sort, bulk select (archive/delete), and
 * relative timestamps. Clicking a row opens it in /chat.
 */

import { useEffect, useMemo, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell, useShell, type ConversationSummary } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { backend } from "@/lib/backend";
import { notify } from "@/lib/notify";

type SortMode = "newest" | "oldest" | "name";

const SORT_LABEL: Record<SortMode, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  name: "Name A to Z",
};

function relativeTime(iso: string): string {
  const stamp = new Date(iso).getTime();
  if (Number.isNaN(stamp)) return "";
  const diff = Date.now() - stamp;
  const minute = 60_000;
  const hour = 3_600_000;
  const day = 86_400_000;
  if (diff < hour) {
    const mins = Math.max(1, Math.round(diff / minute));
    return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  }
  if (diff < day) {
    const hours = Math.round(diff / hour);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  if (diff < 30 * day) {
    const days = Math.round(diff / day);
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function ChatsInner() {
  const { openConversation, newConversation, refreshConversations } = useShell();
  const [rows, setRows] = useState<ConversationSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [sort, setSort] = useState<SortMode>("newest");
  const [sortOpen, setSortOpen] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setRows(await backend.conversations());
    } catch {
      setRows([]);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = (rows ?? []).filter(
      (c) => !c.archived && (!q || c.title.toLowerCase().includes(q)),
    );
    return [...list].sort((a, b) => {
      if (sort === "name") return a.title.localeCompare(b.title);
      return sort === "newest"
        ? b.updated_at.localeCompare(a.updated_at)
        : a.updated_at.localeCompare(b.updated_at);
    });
  }, [rows, query, sort]);

  const archived = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (rows ?? []).filter(
      (c) => c.archived && (!q || c.title.toLowerCase().includes(q)),
    );
  }, [rows, query]);

  const archiveSelected = async () => {
    if (selected.length === 0 || busy) return;
    setBusy(true);
    const ids = selected;
    setSelected([]);
    setSelecting(false);
    for (const id of ids) {
      try {
        await backend.archiveConversation(id);
      } catch {
        /* per-item best effort */
      }
    }
    await Promise.all([load(), refreshConversations()]);
    setBusy(false);
    notify(ids.length === 1 ? "Conversation archived." : `${ids.length} conversations archived.`);
  };

  const deleteSelected = async () => {
    if (selected.length === 0 || busy) return;
    if (!window.confirm(`Delete ${selected.length} conversation${selected.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    setBusy(true);
    const ids = selected;
    setSelected([]);
    setSelecting(false);
    for (const id of ids) {
      try {
        await backend.deleteConversation(id);
      } catch {
        /* per-item best effort */
      }
    }
    await Promise.all([load(), refreshConversations()]);
    setBusy(false);
    notify(ids.length === 1 ? "Conversation deleted." : `${ids.length} conversations deleted.`);
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 lg:px-10">
      <div className="flex items-center gap-2">
        <h1 className="font-serif text-[32px] tracking-tight text-[var(--text-1)]">Chats and tasks</h1>
        <span className="min-w-0 flex-1" />
        {searching ? (
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setQuery("");
                setSearching(false);
              }
            }}
            placeholder="Search chats"
            aria-label="Search chats"
            className="fluid h-9 w-52 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[13px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
          />
        ) : (
          <button
            type="button"
            aria-label="Search chats"
            onClick={() => setSearching(true)}
            className="press grid h-9 w-9 place-items-center rounded-full text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
          >
            <Icon name="search" size={16} />
          </button>
        )}
        <Popover open={sortOpen} onOpenChange={setSortOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label="Sort chats"
              title={SORT_LABEL[sort]}
              className="press grid h-9 w-9 place-items-center rounded-full text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
            >
              <Icon name="sliders" size={16} />
            </button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-48 border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 text-[var(--text-1)]">
            {(Object.keys(SORT_LABEL) as SortMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => {
                  setSort(mode);
                  setSortOpen(false);
                }}
                className="press fluid flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
              >
                <span className="flex-1">{SORT_LABEL[mode]}</span>
                {sort === mode && <Icon name="check" size={13} className="text-[var(--accent)]" />}
              </button>
            ))}
          </PopoverContent>
        </Popover>
        <button
          type="button"
          onClick={() => {
            setSelecting((v) => !v);
            setSelected([]);
          }}
          className={`press h-9 rounded-xl border px-4 text-[13px] font-medium ${
            selecting
              ? "border-[var(--accent-border)] bg-[var(--accent-dim)] text-[var(--accent)]"
              : "border-[var(--hairline)] bg-[var(--bg-raised)] text-[var(--text-1)] hover:border-[var(--hairline-strong)]"
          }`}
        >
          {selecting ? "Done" : "Select"}
        </button>
        <button
          type="button"
          onClick={newConversation}
          className="press h-9 rounded-xl bg-[var(--solid)] px-4 text-[13px] font-semibold text-[var(--on-solid)] hover:opacity-90"
        >
          New
        </button>
      </div>

      {selecting && (
        <div className="mt-4 flex items-center gap-2 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-2.5">
          <span className="tabular flex-1 text-[12.5px] text-[var(--text-3)]">
            {selected.length} selected
          </span>
          <button
            type="button"
            onClick={() => void archiveSelected()}
            disabled={selected.length === 0 || busy}
            className="press rounded-lg border border-[var(--hairline)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--text-2)] hover:text-[var(--text-1)] disabled:opacity-40"
          >
            Archive
          </button>
          <button
            type="button"
            onClick={() => void deleteSelected()}
            disabled={selected.length === 0 || busy}
            className="press rounded-lg border border-[var(--hairline)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--danger)] disabled:opacity-40"
          >
            Delete
          </button>
        </div>
      )}

      {rows === null ? (
        <p className="mt-10 text-[13px] text-[var(--text-3)]">Loading chats…</p>
      ) : visible.length === 0 && archived.length === 0 ? (
        <div className="mt-10 max-w-md">
          <p className="text-[15px] font-medium text-[var(--text-1)]">
            {(rows ?? []).length === 0 ? "No chats yet" : "No matches"}
          </p>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--text-2)]">
            {(rows ?? []).length === 0
              ? "Start a new chat and it will appear here."
              : "Try a different search."}
          </p>
        </div>
      ) : (
        <div className="mt-6">
          {visible.map((c) => {
            const checked = selected.includes(c.id);
            return (
              <span
                key={c.id}
                className="flex items-center gap-3 border-b border-[var(--hairline)] py-3.5 first:border-t"
              >
                {selecting && (
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={checked}
                    aria-label={`Select ${c.title}`}
                    onClick={() => toggle(c.id)}
                    className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border ${
                      checked
                        ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-contrast)]"
                        : "border-[var(--hairline-strong)] text-transparent hover:border-[var(--accent)]"
                    }`}
                  >
                    <Icon name="check" size={12} strokeWidth={3} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => (selecting ? toggle(c.id) : openConversation(c.id))}
                  className="press flex min-w-0 flex-1 items-center gap-3 text-left"
                >
                  <span className="min-w-0 flex-1 truncate text-[14.5px] text-[var(--text-1)]">
                    {c.title}
                  </span>
                  <span className="shrink-0 font-mono text-[11.5px] tabular text-[var(--text-3)]">
                    {relativeTime(c.updated_at)}
                  </span>
                </button>
              </span>
            );
          })}
          {archived.length > 0 && (
            <div className="mt-6">
              <button
                type="button"
                onClick={() => setShowArchived((v) => !v)}
                aria-expanded={showArchived}
                className="press flex items-center gap-1.5 text-[12.5px] font-medium text-[var(--text-3)] hover:text-[var(--text-1)]"
              >
                Archived ({archived.length})
                <Icon name="chevron" size={12} className={`transition-transform ${showArchived ? "rotate-90" : "-rotate-90"}`} />
              </button>
              {showArchived && (
                <div className="mt-2">
                  {archived.map((c) => (
                    <span
                      key={c.id}
                      className="flex items-center gap-3 border-b border-[var(--hairline)] py-3 first:border-t"
                    >
                      <button
                        type="button"
                        onClick={() => openConversation(c.id)}
                        className="press flex min-w-0 flex-1 items-center gap-3 text-left"
                      >
                        <span className="min-w-0 flex-1 truncate text-[14px] text-[var(--text-3)]">
                          {c.title}
                        </span>
                        <span className="shrink-0 font-mono text-[11px] tabular text-[var(--text-3)]">
                          {relativeTime(c.updated_at)}
                        </span>
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <ChatsInner />
      </AppShell>
    </AuthProvider>
  );
}
