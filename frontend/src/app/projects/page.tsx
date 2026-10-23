"use client";

/**
 * Projects  -  named workspaces with shared instructions, like the reference.
 *
 * Cards show the name, the instructions as the description, and the creation
 * date. The New project button opens the creation dialog (name + goals).
 * Clicking a card filters the sidebar to that project and opens the chat.
 */

import { useEffect, useMemo, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell, useShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CreateProjectDialog, ProjectIcon } from "@/components/project-picker";
import { backend, type Project } from "@/lib/backend";

type SortMode = "newest" | "oldest" | "name";

const SORT_LABEL: Record<SortMode, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  name: "Name A to Z",
};

function formatDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function ProjectsInner() {
  const { openProject, refreshProjects } = useShell();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [sort, setSort] = useState<SortMode>("newest");
  const [sortOpen, setSortOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = async () => {
    try {
      setProjects(await backend.projects());
    } catch {
      setProjects([]);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = (projects ?? []).filter(
      (p) =>
        !q ||
        p.name.toLowerCase().includes(q) ||
        (p.instructions || "").toLowerCase().includes(q),
    );
    return [...rows].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      const at = a.created_at ?? "";
      const bt = b.created_at ?? "";
      return sort === "newest" ? (at < bt ? 1 : -1) : at < bt ? -1 : 1;
    });
  }, [projects, query, sort]);

  const openAndRefresh = (p: Project) => {
    openProject(p.id);
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 lg:px-10">
      <div className="flex items-center gap-2">
        <h1 className="font-serif text-[32px] tracking-tight text-[var(--text-1)]">Projects</h1>
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
            placeholder="Search projects"
            aria-label="Search projects"
            className="fluid h-9 w-52 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[13px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
          />
        ) : (
          <button
            type="button"
            aria-label="Search projects"
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
              aria-label="Sort projects"
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
          onClick={() => setCreating(true)}
          className="press h-9 rounded-xl bg-[var(--solid)] px-4 text-[13px] font-semibold text-[var(--on-solid)] hover:opacity-90"
        >
          New project
        </button>
      </div>

      {projects === null ? (
        <p className="mt-10 text-[13px] text-[var(--text-3)]">Loading projects…</p>
      ) : visible.length === 0 ? (
        <div className="mt-10 max-w-md">
          <p className="text-[15px] font-medium text-[var(--text-1)]">
            {projects.length === 0 ? "No projects yet" : "No matches"}
          </p>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--text-2)]">
            {projects.length === 0
              ? "Group related chats under one roof with shared instructions."
              : "Try a different search."}
          </p>
        </div>
      ) : (
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {visible.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => openAndRefresh(p)}
              className="press flex min-h-[132px] flex-col rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-4 text-left shadow-[var(--shadow-card)] hover:border-[var(--hairline-strong)]"
            >
              <span className="flex items-center gap-2">
                <ProjectIcon icon={p.icon} color={p.color} size={16} className="shrink-0" />
                <span className="min-w-0 flex-1 truncate text-[14.5px] font-semibold tracking-tight text-[var(--text-1)]">
                  {p.name}
                </span>
              </span>
              {p.instructions ? (
                <span className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-[var(--text-2)]">
                  {p.instructions}
                </span>
              ) : (
                <span className="mt-1 text-[13px] text-[var(--text-3)]">No goals set yet.</span>
              )}
              <span className="mt-auto flex items-center justify-between pt-3 text-[12px] text-[var(--text-3)]">
                <span>{formatDate(p.created_at)}</span>
                <span className="tabular">
                  {p.conversation_count} {p.conversation_count === 1 ? "chat" : "chats"}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}

      {creating && (
        <CreateProjectDialog
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false);
            void Promise.all([load(), refreshProjects()]).then(() => openAndRefresh(created));
          }}
        />
      )}
    </div>
  );
}

export default function ProjectsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <ProjectsInner />
      </AppShell>
    </AuthProvider>
  );
}
