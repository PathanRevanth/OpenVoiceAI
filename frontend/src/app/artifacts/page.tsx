"use client";

/**
 * Artifacts  -  every deliverable the agents have produced, in one index.
 *
 * Sources come from GET /api/artifacts/index (assistant messages containing
 * fences) and are parsed with the thread's own artifact rules, so this page
 * always agrees with chat. Tabs mirror the reference: All, Yours, and
 * Shared with you (empty on a single-tenant workspace).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthProvider } from "@/components/auth-context";
import { AppShell, useShell } from "@/components/app-shell";
import { Icon, type IconName } from "@/components/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { groupConversations, togglePin, usePinnedIds } from "@/lib/conversations";
import { parseSegments, type ArtifactKind } from "@/lib/artifacts";
import { backend, type ArtifactSource } from "@/lib/backend";
import { notify } from "@/lib/notify";

interface IndexedArtifact {
  id: string;
  conversation_id: string;
  conversation_title: string;
  title: string;
  kind: ArtifactKind;
  lines: number;
  updated_at: string;
}

const KIND_ICON: Record<ArtifactKind, IconName> = {
  code: "code",
  markdown: "file",
  html: "eye",
  svg: "artifact",
  mermaid: "chart",
  react: "code",
  pdf: "file",
  csv: "panel",
  json: "json",
  image: "eye",
};

const KIND_LABEL: Record<ArtifactKind, string> = {
  code: "Code",
  markdown: "Document",
  html: "Web page",
  svg: "Graphic",
  mermaid: "Diagram",
  react: "React component",
  pdf: "PDF",
  csv: "Table",
  json: "JSON",
  image: "Image",
};

const TEMPLATES: { kind: string; title: string; badge: string; prompt: string; tint: string }[] = [
  {
    kind: "doc",
    title: "Docs",
    badge: "New",
    prompt: "Create a markdown document artifact about: ",
    tint: "linear-gradient(135deg, #dbeafe 0%, #eff6ff 60%, #ffffff 100%)",
  },
  {
    kind: "slides",
    title: "Slides",
    badge: "New",
    prompt: "Create a presentation outline as a markdown document artifact about: ",
    tint: "linear-gradient(135deg, #fef3c7 0%, #fffbeb 60%, #ffffff 100%)",
  },
  {
    kind: "design",
    title: "Design",
    badge: "New",
    prompt: "Create a single-file HTML page artifact for the following. Make it polished: ",
    tint: "linear-gradient(135deg, #ede9fe 0%, #f5f3ff 60%, #ffffff 100%)",
  },
  {
    kind: "dashboard",
    title: "Dashboard",
    badge: "Beta",
    prompt: "Create a single-file HTML dashboard artifact with charts for the following: ",
    tint: "linear-gradient(135deg, #e0e7ff 0%, #eef2ff 60%, #ffffff 100%)",
  },
];

function relTime(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "";
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function ArtifactsInner() {
  const router = useRouter();
  const { openConversation, newConversation } = useShell();
  const [sources, setSources] = useState<ArtifactSource[] | null>(null);
  const [tab, setTab] = useState<"all" | "yours" | "shared">("all");
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<ArtifactKind | null>(null);
  const [typeOpen, setTypeOpen] = useState(false);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const pinnedIds = usePinnedIds();

  useEffect(() => {
    let alive = true;
    backend
      .artifactsIndex()
      .then((rows) => {
        if (alive) setSources(rows);
      })
      .catch(() => {
        if (alive) setSources([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const entries = useMemo<IndexedArtifact[]>(() => {
    if (!sources) return [];
    const out: IndexedArtifact[] = [];
    for (const msg of sources) {
      for (const seg of parseSegments(msg.text, msg.message_id, true)) {
        if (seg.type !== "artifact") continue;
        out.push({
          id: `${msg.conversation_id}:${seg.artifact.id}`,
          conversation_id: msg.conversation_id,
          conversation_title: msg.conversation_title,
          title: seg.artifact.title,
          kind: seg.artifact.kind,
          lines: seg.artifact.content.split("\n").length,
          updated_at: msg.created_at ?? new Date().toISOString(),
        });
      }
    }
    return out.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  }, [sources]);

  const counts = useMemo(() => {
    const map = new Map<ArtifactKind, number>();
    for (const e of entries) map.set(e.kind, (map.get(e.kind) ?? 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [entries]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter(
      (e) =>
        (!typeFilter || e.kind === typeFilter) &&
        (!q || e.title.toLowerCase().includes(q) || e.conversation_title.toLowerCase().includes(q)),
    );
  }, [entries, query, typeFilter]);

  const groups = useMemo(() => groupConversations(filtered, []), [filtered]);

  const openEntry = useCallback(
    (e: IndexedArtifact) => {
      openConversation(e.conversation_id);
      window.setTimeout(() => window.dispatchEvent(new CustomEvent("vf:open-artifacts")), 350);
    },
    [openConversation],
  );

  const startTemplate = useCallback(
    (prompt: string) => {
      window.sessionStorage.setItem("vf:composer-draft", prompt);
      newConversation();
      if (window.location.pathname !== "/chat") router.push("/chat");
    },
    [newConversation, router],
  );

  const copyShareLink = useCallback(async (e: IndexedArtifact) => {
    setMenuId(null);
    try {
      const { share_token } = await backend.shareConversation(e.conversation_id);
      await navigator.clipboard?.writeText(`${window.location.origin}/shared/${share_token}`);
      setCopiedId(e.id);
      notify("Share link copied to clipboard.");
      window.setTimeout(() => setCopiedId(null), 1500);
    } catch {
      notify("Could not create a share link.");
    }
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 lg:px-10">
      <h1 className="font-serif text-[32px] tracking-tight text-[var(--text-1)]">Artifacts</h1>

      <div className="mt-4 flex flex-wrap items-center gap-1">
        {(["all", "yours", "shared"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`press rounded-lg px-3 py-1.5 text-[13.5px] ${
              tab === t
                ? "bg-[var(--panel-hover)] font-medium text-[var(--text-1)]"
                : "text-[var(--text-3)] hover:text-[var(--text-1)]"
            }`}
          >
            {t === "all" ? "All" : t === "yours" ? "Yours" : "Shared with you"}
          </button>
        ))}
        <span className="min-w-0 flex-1" />
        <span className="relative">
          <Icon name="search" size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-3)]" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search artifacts"
            aria-label="Search artifacts"
            className="fluid h-8 w-44 rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] pl-8 pr-2 text-[12px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
          />
        </span>
        <Popover open={typeOpen} onOpenChange={setTypeOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label="Filter by type"
              className="press fluid flex h-8 items-center gap-1.5 rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 text-[12px] text-[var(--text-2)] hover:text-[var(--text-1)]"
            >
              <Icon name="sliders" size={13} />
              {typeFilter ? KIND_LABEL[typeFilter] : "All types"}
            </button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-52 border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 text-[var(--text-1)]">
            <FilterRow label="All types" count={entries.length} checked={!typeFilter} onPick={() => { setTypeFilter(null); setTypeOpen(false); }} />
            {counts.map(([kind, n]) => (
              <FilterRow key={kind} label={KIND_LABEL[kind]} count={n} checked={typeFilter === kind} onPick={() => { setTypeFilter(kind); setTypeOpen(false); }} />
            ))}
          </PopoverContent>
        </Popover>
      </div>

      {tab === "shared" ? (
        <div className="mt-10 max-w-md">
          <p className="text-[15px] font-medium text-[var(--text-1)]">No shared artifacts yet</p>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--text-2)]">
            When someone shares an artifact with you, it appears here.
          </p>
        </div>
      ) : (
        <>
          <p className="mt-8 text-[13px] font-medium text-[var(--text-2)]">Make something new</p>
          <div className="mt-3 grid grid-cols-2 gap-4 lg:grid-cols-4">
            {TEMPLATES.map((t) => (
              <button key={t.kind} type="button" onClick={() => startTemplate(t.prompt)} className="press group text-left">
                <span className="block overflow-hidden rounded-2xl border border-[var(--hairline)] shadow-[var(--shadow-card)]" style={{ background: t.tint }}>
                  <TemplateThumb kind={t.kind} />
                </span>
                <span className="mt-2 flex items-center gap-1.5 px-0.5">
                  <span className="text-[13.5px] font-medium text-[var(--text-1)]">{t.title}</span>
                  <span className="rounded-md bg-[var(--accent-dim)] px-1.5 py-px text-[10.5px] font-semibold text-[var(--accent)]">
                    {t.badge}
                  </span>
                </span>
              </button>
            ))}
          </div>

          {sources === null ? (
            <p className="mt-10 text-[13px] text-[var(--text-3)]">Loading artifacts…</p>
          ) : filtered.length === 0 ? (
            <div className="mt-10 max-w-md">
              <p className="text-[15px] font-medium text-[var(--text-1)]">
                {entries.length === 0 ? "No artifacts yet" : "No matches"}
              </p>
              <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--text-2)]">
                {entries.length === 0
                  ? "Ask for a document, web page, table, or diagram in chat and it will appear here."
                  : "Try a different search or type filter."}
              </p>
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mt-8">
                <p className="px-1 pb-2 text-[13px] font-medium text-[var(--text-2)]">{group.label}</p>
                <div className="space-y-0.5">
                  {group.items.map((e) => {
                    const pinned = pinnedIds.includes(e.conversation_id);
                    return (
                      <div key={e.id} className="group/row relative flex items-center gap-3 rounded-xl px-1 py-1.5 hover:bg-[var(--hover)]">
                        <button onClick={() => openEntry(e)} className="flex min-w-0 flex-1 items-center gap-3 text-left" title={`Open in ${e.conversation_title}`}>
                          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--panel-hover)] text-[var(--text-2)]">
                            <Icon name={KIND_ICON[e.kind]} size={15} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[14px] font-medium text-[var(--text-1)]">
                              {e.title}
                            </span>
                            <span className="block truncate text-[11.5px] text-[var(--text-3)]">
                              {KIND_LABEL[e.kind]} · {e.conversation_title}
                            </span>
                          </span>
                          <span className="hidden shrink-0 items-center gap-1.5 text-[12px] text-[var(--text-3)] sm:flex">
                            Viewed {relTime(e.updated_at)}
                          </span>
                        </button>
                        <button
                          aria-label={`Options for ${e.title}`}
                          aria-expanded={menuId === e.id}
                          onClick={() => setMenuId(menuId === e.id ? null : e.id)}
                          className="press shrink-0 rounded-lg p-1.5 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                        >
                          <Icon name="more" size={14} />
                        </button>
                        {menuId === e.id && (
                          <>
                            <button aria-label="Close menu" className="fixed inset-0 z-10 cursor-default" onClick={() => setMenuId(null)} />
                            <span className="absolute right-0 top-full z-20 mt-1 w-48 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-1 shadow-[var(--shadow-pop)]">
                              <button
                                onClick={() => { setMenuId(null); openEntry(e); }}
                                className="press fluid flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                              >
                                <Icon name="external" size={13} /> Open
                              </button>
                              <button
                                onClick={() => { setMenuId(null); togglePin(e.conversation_id); }}
                                className="press fluid flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                              >
                                <Icon name="pin" size={13} /> {pinned ? "Unpin conversation" : "Pin conversation"}
                              </button>
                              <button
                                onClick={() => void copyShareLink(e)}
                                className="press fluid flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                              >
                                <Icon name="link" size={13} /> {copiedId === e.id ? "Copied" : "Copy share link"}
                              </button>
                            </span>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </>
      )}
    </div>
  );
}

function FilterRow({ label, count, checked, onPick }: { label: string; count: number; onPick: () => void; checked: boolean }) {
  return (
    <button
      onClick={onPick}
      className="press fluid flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
    >
      <span className="flex-1">{label}</span>
      <span className="tabular font-mono text-[11px] text-[var(--text-3)]">{count}</span>
      {checked && <Icon name="check" size={13} className="text-[var(--accent)]" />}
    </button>
  );
}

/** Stylized CSS thumbnails for the four template cards. */
function TemplateThumb({ kind }: { kind: string }) {
  if (kind === "doc") {
    return (
      <span className="grid place-items-center p-5">
        <span className="block w-16 rounded-md border border-[var(--hairline)] bg-white p-2 shadow-sm">
          <span className="mb-1 block h-1 w-8 rounded bg-[#2563eb]" />
          <span className="mb-1 block font-serif text-[10px] font-bold text-[#1e40af]">A</span>
          {[80, 95, 70, 85].map((w, i) => (
            <span key={i} className="mb-1 block h-1 rounded bg-slate-200" style={{ width: `${w}%` }} />
          ))}
        </span>
      </span>
    );
  }
  if (kind === "slides") {
    return (
      <span className="grid place-items-center p-5">
        <span className="block w-24 rounded-md border border-[var(--hairline)] bg-white p-2 shadow-sm">
          <span className="mb-1.5 block h-2 w-16 rounded bg-[#b45309]" />
          <span className="mb-1 block h-1 w-full rounded bg-amber-100" />
          <span className="flex justify-between">
            <span className="block h-1 w-10 rounded bg-amber-100" />
            <span className="block h-4 w-4 rounded bg-amber-200" />
          </span>
        </span>
      </span>
    );
  }
  if (kind === "design") {
    return (
      <span className="grid place-items-center p-5">
        <span className="block w-14 rounded-lg border border-[var(--hairline)] bg-white p-1.5 shadow-sm">
          <span className="mx-auto mb-1 block h-0.5 w-8 rounded bg-slate-300" />
          <span className="mb-1 flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />
            <span className="h-1 w-8 rounded bg-slate-200" />
          </span>
          <span className="mb-1 h-6 rounded bg-violet-100" />
          <span className="mx-auto flex w-8 justify-between">
            {[0, 1, 2].map((i) => (
              <span key={i} className="h-1 w-1 rounded-full bg-slate-300" />
            ))}
          </span>
        </span>
      </span>
    );
  }
  return (
    <span className="grid place-items-center p-5">
      <span className="flex w-24 items-end justify-center gap-1.5 rounded-md border border-[var(--hairline)] bg-white p-2 shadow-sm">
        {[10, 18, 28, 16].map((h, i) => (
          <span key={i} className="w-3.5 rounded-sm bg-violet-400" style={{ height: h }} />
        ))}
      </span>
    </span>
  );
}

export default function ArtifactsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <ArtifactsInner />
      </AppShell>
    </AuthProvider>
  );
}
