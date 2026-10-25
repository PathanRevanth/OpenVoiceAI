"use client";

/**
 * AppShell  -  platform chrome: light sidebar (workspace + platform nav,
 * approvals badge, recent conversations), top bar (model chip, demo badge,
 * user menu), shared context for conversation open/new.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthValue } from "@/components/auth-context";
import { Icon, type IconName } from "@/components/icons";
import { ThemeToggle } from "@/components/theme-toggle";
import { backend, connectEventStream, type PlatformInfo, type Project } from "@/lib/backend";
import { notify, subscribeToasts } from "@/lib/notify";
import { toggleProjectPin, usePinnedProjectIds } from "@/lib/project-pins";
import { CreateProjectDialog, ProjectIcon } from "@/components/project-picker";
import { CommandPalette, type PaletteNavItem } from "@/components/command-palette";
import { groupConversations, togglePin, usePinnedIds } from "@/lib/conversations";

export interface ConversationSummary {
  id: string;
  title: string;
  channel: string;
  project_id: string | null;
  archived: boolean;
  updated_at: string;
}

interface ShellContextValue {
  activeId: string | null;
  setActiveId: (id: string | null) => void;
  openConversation: (id: string) => void;
  newConversation: () => void;
  refreshConversations: () => void;
  deleteConversation: (id: string) => void;
  projects: Project[];
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
  refreshProjects: () => void;
  openProject: (id: string) => void;
}

const ShellContext = createContext<ShellContextValue>({
  activeId: null,
  setActiveId: () => {},
  openConversation: () => {},
  newConversation: () => {},
  refreshConversations: () => {},
  deleteConversation: () => {},
  projects: [],
  activeProjectId: null,
  setActiveProjectId: () => {},
  refreshProjects: () => {},
  openProject: () => {},
});

export const useShell = () => useContext(ShellContext);

const PLATFORM_NAV: {
  href: string;
  label: string;
  icon: IconName;
  badge?: "approvals";
}[] = [
  { href: "/observability", label: "Observability", icon: "pulse" },
  { href: "/agents", label: "Agents", icon: "bot" },
  { href: "/triggers", label: "Triggers", icon: "clock" },
  { href: "/connections", label: "LLM Connections", icon: "plug" },
  { href: "/approvals", label: "Approvals", icon: "shield", badge: "approvals" },
  { href: "/usage", label: "Usage & spend", icon: "coins" },
  { href: "/audit", label: "Audit log", icon: "scroll" },
  { href: "/settings", label: "Settings", icon: "gear" },
];

const PALETTE_NAV: PaletteNavItem[] = [
  { href: "/chat", label: "Assistant", icon: "spark" },
  { href: "/chats", label: "Chats and tasks", icon: "chat" },
  { href: "/artifacts", label: "Artifacts", icon: "duplicate" },
  { href: "/projects", label: "Projects", icon: "artifact" },
  ...PLATFORM_NAV,
];

const TITLES: Record<string, string> = {
  "/chat": "Assistant",
  "/chats": "Chats and tasks",
  "/artifacts": "Artifacts",
  "/projects": "Projects",
  "/observability": "Observability",
  "/agents": "Agents",
  "/triggers": "Triggers",
  "/connections": "LLM Connections",
  "/approvals": "Approvals",
  "/usage": "Usage & spend",
  "/audit": "Audit log",
  "/settings": "Settings",
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, loading, signOut } = useAuthValue();

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [pending, setPending] = useState(0);
  const [platform, setPlatform] = useState<PlatformInfo | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [budgetAlert, setBudgetAlert] = useState<string | null>(null);
  const [recentQuery, setRecentQuery] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menuConvId, setMenuConvId] = useState<string | null>(null);
  const [moveExpanded, setMoveExpanded] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [rowNotice, setRowNotice] = useState<string | null>(null);
  const [msgMatches, setMsgMatches] = useState<{ id: string; title: string; snippet: string }[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [projectMenuId, setProjectMenuId] = useState<string | null>(null);
  const [renamingProjectId, setRenamingProjectId] = useState<string | null>(null);
  const [projectNameDraft, setProjectNameDraft] = useState("");
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [projectInstrDraft, setProjectInstrDraft] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const toggleSelected = useCallback((id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }, []);

  const cancelSelecting = useCallback(() => {
    setSelecting(false);
    setSelectedIds([]);
  }, []);

  useEffect(() => {
    const q = recentQuery.trim();
    if (q.length < 2) {
      setMsgMatches([]);
      return;
    }
    let alive = true;
    const t = window.setTimeout(() => {
      backend
        .searchConversations(q, 5)
        .then((rows) => {
          if (alive) setMsgMatches(rows.map((r) => ({ id: r.id, title: r.title, snippet: r.snippet })));
        })
        .catch(() => {});
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [recentQuery]);
  const pinnedIds = usePinnedIds();
  const pinnedProjectIds = usePinnedProjectIds();
  const orderedProjects = useMemo(() => {
    const pinned = new Set(pinnedProjectIds);
    return [...projects.filter((p) => pinned.has(p.id)), ...projects.filter((p) => !pinned.has(p.id))];
  }, [projects, pinnedProjectIds]);
  const visibleConversations = useMemo(
    () =>
      conversations.filter(
        (c) =>
          !c.archived &&
          (!activeProjectId || c.project_id === activeProjectId) &&
          (!recentQuery || c.title.toLowerCase().includes(recentQuery.toLowerCase())),
      ),
    [conversations, activeProjectId, recentQuery],
  );
  // Sidebar shows only the 10 latest; the full list lives on /chats.
  const SIDEBAR_LIMIT = 10;
  const sidebarPreview = useMemo(
    () => visibleConversations.slice(0, SIDEBAR_LIMIT),
    [visibleConversations],
  );
  const hasMoreChats = visibleConversations.length > SIDEBAR_LIMIT;
  const archivedConversations = useMemo(
    () =>
      conversations.filter(
        (c) =>
          c.archived &&
          (!activeProjectId || c.project_id === activeProjectId) &&
          (!recentQuery || c.title.toLowerCase().includes(recentQuery.toLowerCase())),
      ),
    [conversations, activeProjectId, recentQuery],
  );
  const recentGroups = useMemo(
    () => groupConversations(sidebarPreview, pinnedIds),
    [sidebarPreview, pinnedIds],
  );
  const closePalette = useCallback(() => setPaletteOpen(false), []);

  useEffect(() => {
    if (window.localStorage.getItem("vf:sidebar") === "1") setCollapsed(true);
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      window.localStorage.setItem("vf:sidebar", c ? "0" : "1");
      return !c;
    });
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await backend.conversations());
    } catch {
      /* signed out */
    }
  }, []);

  const refreshPending = useCallback(async () => {
    try {
      setPending((await backend.approvals()).length);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await backend.projects());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    refreshConversations();
    refreshPending();
    refreshProjects();
    backend.platform().then(setPlatform).catch(() => {});
    const t = setInterval(() => {
      refreshPending();
      refreshConversations();
    }, 12_000);
    const stopEvents = connectEventStream((ev) => {
      if (ev.type === "budget.warning") {
        const payload = ev.payload as { level?: string; spend_today?: number; daily_limit?: number };
        setBudgetAlert(
          `Spend ${payload.level === "exceeded" ? "exceeded" : "approaching"} the daily limit: ${payload.spend_today} of ${payload.daily_limit}`,
        );
      }
    });
    return () => {
      clearInterval(t);
      stopEvents();
    };
  }, [user, refreshConversations, refreshPending, refreshProjects]);

  const openConversation = useCallback(
    (id: string) => {
      setActiveId(id);
      setMobileOpen(false);
      if (pathname !== "/chat") router.push("/chat");
    },
    [pathname, router],
  );

  const newConversation = useCallback(() => {
    setActiveId(null);
    setMobileOpen(false);
    if (pathname !== "/chat") router.push("/chat");
  }, [pathname, router]);

  const openProject = useCallback(
    (id: string) => {
      setProjectMenuId(null);
      setActiveProjectId(id);
      setMobileOpen(false);
      router.push(`/projects/${id}`);
    },
    [router],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      } else if (e.shiftKey && e.key.toLowerCase() === "o") {
        e.preventDefault();
        newConversation();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newConversation]);

  const deleteConversation = useCallback(
    (id: string) => {
      // Optimistic: drop it from the sidebar immediately; clear if it was open.
      setConversations((prev) => prev.filter((c) => c.id !== id));
      setActiveId((current) => (current === id ? null : current));
      backend.deleteConversation(id).catch(() => refreshConversations());
    },
    [refreshConversations],
  );

  const archiveSelected = useCallback(async () => {
    const ids = selectedIds;
    cancelSelecting();
    for (const id of ids) {
      try {
        await backend.archiveConversation(id);
      } catch {
        /* per-item best effort */
      }
    }
    refreshConversations();
    notify(ids.length === 1 ? "Conversation archived." : `${ids.length} conversations archived.`);
  }, [selectedIds, cancelSelecting, refreshConversations]);

  const deleteSelected = useCallback(async () => {
    const ids = selectedIds;
    if (ids.length === 0) return;
    if (!window.confirm(`Delete ${ids.length} conversation${ids.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    cancelSelecting();
    setActiveId((current) => (current && ids.includes(current) ? null : current));
    for (const id of ids) {
      try {
        await backend.deleteConversation(id);
      } catch {
        /* per-item best effort */
      }
    }
    refreshConversations();
    notify(ids.length === 1 ? "Conversation deleted." : `${ids.length} conversations deleted.`);
  }, [selectedIds, cancelSelecting, refreshConversations]);

  const moveConversation = useCallback(
    async (id: string, projectId: string | null) => {
      setMenuConvId(null);
      setMoveExpanded(false);
      setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, project_id: projectId } : c)));
      try {
        await backend.moveConversation(id, projectId);
        refreshProjects();
      } catch {
        refreshConversations();
      }
    },
    [refreshConversations, refreshProjects],
  );

  const archiveConversation = useCallback(
    async (id: string, archived: boolean) => {
      setMenuConvId(null);
      setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, archived } : c)));
      try {
        if (archived) await backend.archiveConversation(id);
        else await backend.unarchiveConversation(id);
      } catch {
        refreshConversations();
      }
    },
    [refreshConversations],
  );

  const saveProjectName = useCallback(
    async (id: string) => {
      const title = projectNameDraft.trim();
      if (!title) {
        setRenamingProjectId(null);
        return;
      }
      setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, name: title } : p)));
      setRenamingProjectId(null);
      try {
        await backend.updateProject(id, { name: title });
      } catch {
        refreshProjects();
      }
    },
    [projectNameDraft, refreshProjects],
  );

  const removeProject = useCallback(
    async (id: string) => {
      try {
        await backend.deleteProject(id);
        if (activeProjectId === id) setActiveProjectId(null);
        if (pathname === `/projects/${id}`) router.push("/projects");
        await Promise.all([refreshProjects(), refreshConversations()]);
        notify("Project deleted.");
      } catch {
        setRowNotice("Could not delete this project.");
      }
    },
    [activeProjectId, pathname, refreshConversations, refreshProjects, router],
  );

  const saveProjectInstructions = useCallback(async () => {
    if (!editingProjectId) return;
    const value = projectInstrDraft.trim();
    setEditingProjectId(null);
    try {
      await backend.updateProject(editingProjectId, { instructions: value });
      refreshProjects();
    } catch {
      setRowNotice("Could not save project instructions.");
    }
  }, [editingProjectId, projectInstrDraft, refreshProjects]);

  const saveRename = useCallback(
    async (id: string) => {
      const title = renameDraft.trim();
      if (!title) {
        setRenamingId(null);
        return;
      }
      setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
      setRenamingId(null);
      try {
        await backend.renameConversation(id, title);
      } catch {
        refreshConversations();
      }
    },
    [renameDraft, refreshConversations],
  );

  const duplicateConversation = useCallback(
    async (id: string) => {
      setMenuConvId(null);
      try {
        const copy = await backend.duplicateConversation(id);
        await refreshConversations();
        setActiveId(copy.id);
        notify("Conversation duplicated.");
        if (pathname !== "/chat") router.push("/chat");
      } catch {
        setRowNotice("Could not duplicate this conversation.");
        notify("Could not duplicate this conversation.");
      }
    },
    [pathname, refreshConversations, router],
  );

  const copyShareLink = useCallback(async (id: string) => {
    setMenuConvId(null);
    try {
      const { share_token } = await backend.shareConversation(id);
      const url = `${window.location.origin}/shared/${share_token}`;
      await navigator.clipboard?.writeText(url);
      notify("Share link copied to clipboard.");
    } catch {
      setRowNotice("Could not create a share link.");
      notify("Could not create a share link.");
    }
  }, []);

  useEffect(() => {
    if (!rowNotice) return;
    const t = window.setTimeout(() => setRowNotice(null), 3500);
    return () => window.clearTimeout(t);
  }, [rowNotice]);

  const value = useMemo<ShellContextValue>(
    () => ({
      activeId,
      setActiveId,
      openConversation,
      newConversation,
      refreshConversations,
      deleteConversation,
      projects,
      activeProjectId,
      setActiveProjectId,
      refreshProjects,
      openProject,
    }),
    [activeId, openConversation, newConversation, refreshConversations, deleteConversation, projects, activeProjectId, refreshProjects, openProject],
  );

  if (loading) {
    return (
      <div className="relative z-[2] grid min-h-dvh place-items-center">
        <div className="h-9 w-9 animate-breathe rounded-full bg-[var(--accent-dim)] ring-1 ring-[var(--accent-border)]" />
      </div>
    );
  }
  if (!user) return null;

  const active = "/" + (pathname.split("/")[1] ?? "chat");
  const role = user.role ?? "viewer";

  return (
    <ShellContext.Provider value={value}>
      <div className="relative z-[2] flex min-h-dvh">
        {/* ------------------------------------------------ sidebar */}
        <aside
          className={`fixed inset-y-0 left-0 z-30 flex flex-col border-r border-[var(--glass-border)] bg-[var(--glass-bg)] backdrop-blur-2xl transition-[width,transform] duration-300 ease-[var(--ease)] lg:sticky lg:top-0 lg:h-dvh lg:translate-x-0 ${
            collapsed ? "w-[68px]" : "w-[264px]"
          } ${
            // Off-canvas on small screens: `invisible` keeps the closed drawer
            // out of the tab order and the accessibility tree, so its buttons
            // can't be activated while they're parked off-screen.
            mobileOpen ? "visible translate-x-0" : "invisible -translate-x-full lg:visible"
          }`}
        >
          {/* brand */}
          <div className="flex h-14 items-center gap-2.5 px-4">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-[9px] bg-[var(--accent)]">
              <Icon name="waveform" size={14} strokeWidth={2} className="text-[var(--accent-contrast)]" />
            </span>
            {!collapsed && (
              <span className="flex min-w-0 items-baseline gap-2">
                <span className="truncate text-[13px] font-semibold tracking-tight text-[var(--text-1)]">
                  Voice-First
                </span>
                <span className="text-[12px] font-medium text-[var(--text-3)]">AI</span>
              </span>
            )}
          </div>

          {/* claude-style search at the top */}
          <div className="px-3 pb-2">
            <span className="relative block">
              <Icon name="search" size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-3)]" />
              <input
                value={recentQuery}
                onChange={(e) => setRecentQuery(e.target.value)}
                placeholder="Search"
                aria-label="Search chats and messages"
                className="fluid h-9 w-full rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] pl-8 pr-2.5 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              />
            </span>
          </div>

          {/* new chat */}
          <div className="px-3 pb-1">
            <button
              onClick={newConversation}
              title="New chat"
              className={`press fluid flex h-9 w-full items-center gap-2 rounded-xl bg-[var(--panel-hover)] text-[13px] font-medium text-[var(--text-1)] hover:bg-[var(--hairline-strong)] ${
                collapsed ? "justify-center px-0" : "px-3"
              }`}
            >
              <Icon name="plus" size={14} strokeWidth={2} />
              {!collapsed && <span>New Chat</span>}
            </button>
          </div>

          {/* claude-style shortcut rows */}
          <div className="flex flex-col gap-0.5 px-3 pb-1">
            <button
              onClick={() => router.push("/projects")}
              title="Projects"
              className={`fluid flex h-9 items-center gap-2.5 rounded-xl text-[13px] ${
                collapsed ? "justify-center px-0" : "px-3"
              } ${
                pathname === "/projects"
                  ? "bg-[var(--bg-raised)] font-medium text-[var(--text-1)]"
                  : "text-[var(--text-2)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
              }`}
            >
              <Icon name="artifact" size={16} />
              {!collapsed && <span className="flex-1 text-left">Projects</span>}
            </button>
            <button
              onClick={() => router.push("/artifacts")}
              title="Artifacts"
              className={`fluid flex h-9 items-center gap-2.5 rounded-xl text-[13px] ${
                collapsed ? "justify-center px-0" : "px-3"
              } ${
                pathname === "/artifacts"
                  ? "bg-[var(--bg-raised)] font-medium text-[var(--text-1)]"
                  : "text-[var(--text-2)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
              }`}
            >
              <Icon name="duplicate" size={16} />
              {!collapsed && <span className="flex-1 text-left">Artifacts</span>}
            </button>
          </div>

          {/* projects - claude-style: colored icons, hover menu, show more */}
          {!collapsed && (
            <div className="px-3 pt-4">
              <span className="flex items-center justify-between px-0.5 pb-1">
                <p className="text-[11px] font-medium text-[var(--text-3)]">Projects</p>
                <button
                  aria-label="New project"
                  title="New project"
                  onClick={() => setCreatingProject(true)}
                  className="press rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                >
                  <Icon name="plus" size={12} />
                </button>
              </span>
              <div className="space-y-0.5">
                {activeProjectId !== null && (
                  <button
                    onClick={() => setActiveProjectId(null)}
                    className="fluid flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] text-[var(--text-3)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
                  >
                    <Icon name="x" size={11} /> All conversations
                  </button>
                )}
                {orderedProjects.slice(0, showAllProjects ? orderedProjects.length : 5).map((p) => {
                  const isActive = activeProjectId === p.id;
                  const menuOpen = projectMenuId === p.id;
                  const renaming = renamingProjectId === p.id;
                  const editing = editingProjectId === p.id;
                  const pinned = pinnedProjectIds.includes(p.id);
                  return (
                    <div key={p.id} className="group/proj relative">
                      {renaming ? (
                        <span className="flex w-full items-center gap-1.5 rounded-lg bg-[var(--bg-raised)] py-1.5 pl-2.5 pr-2">
                          <input
                            autoFocus
                            value={projectNameDraft}
                            onChange={(e) => setProjectNameDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") void saveProjectName(p.id);
                              if (e.key === "Escape") setRenamingProjectId(null);
                            }}
                            maxLength={128}
                            aria-label="Rename project"
                            className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none"
                          />
                          <button
                            aria-label="Save name"
                            title="Save"
                            onClick={() => void saveProjectName(p.id)}
                            className="press rounded-md p-1 text-[var(--accent)] hover:bg-[var(--panel-hover)]"
                          >
                            <Icon name="check" size={12} />
                          </button>
                          <button
                            aria-label="Cancel rename"
                            title="Cancel"
                            onClick={() => setRenamingProjectId(null)}
                            className="press rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)]"
                          >
                            <Icon name="x" size={12} />
                          </button>
                        </span>
                      ) : (
                        <>
                          <button
                            onClick={() => openProject(p.id)}
                            title={p.name}
                            className={`fluid flex w-full items-center gap-2 rounded-lg py-2 pl-2.5 pr-8 text-left text-[12.5px] ${
                              isActive
                                ? "bg-[var(--bg-raised)] font-medium text-[var(--text-1)] shadow-[0_1px_2px_rgba(22,23,28,0.05)]"
                                : "text-[var(--text-2)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
                            }`}
                          >
                            <ProjectIcon icon={p.icon} color={p.color} size={14} className="shrink-0" />
                            <span className="min-w-0 flex-1 truncate">{p.name}</span>
                            {p.conversation_count > 0 ? (
                              <span className="tabular shrink-0 text-[10.5px] text-[var(--text-3)]">{p.conversation_count}</span>
                            ) : (
                              <span className="shrink-0 text-[10.5px] text-[var(--text-3)]">No chats</span>
                            )}
                          </button>
                          <button
                            aria-label={`More actions for ${p.name}`}
                            title="Project options"
                            aria-haspopup="menu"
                            aria-expanded={menuOpen}
                            onClick={(e) => {
                              e.stopPropagation();
                              setProjectMenuId(menuOpen ? null : p.id);
                            }}
                            className="press absolute right-1.5 top-1/2 hidden -translate-y-1/2 rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)] group-hover/proj:block"
                          >
                            <Icon name="more" size={13} />
                          </button>
                          {menuOpen && (
                            <>
                              <button
                                aria-label="Close menu"
                                className="fixed inset-0 z-10 cursor-default"
                                onClick={() => setProjectMenuId(null)}
                              />
                              <span
                                role="menu"
                                className="absolute right-0 top-full z-20 mt-1 w-48 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-1 shadow-[var(--shadow-pop)]"
                              >
                                <MenuItem icon="chat" label="Open project" onClick={() => openProject(p.id)} />
                                <MenuItem
                                  icon="pencil"
                                  label="Rename"
                                  onClick={() => {
                                    setProjectMenuId(null);
                                    setRenamingProjectId(p.id);
                                    setProjectNameDraft(p.name);
                                  }}
                                />
                                <MenuItem
                                  icon="gear"
                                  label="Project settings"
                                  onClick={() => openProject(p.id)}
                                />
                                <MenuItem
                                  icon="pin"
                                  label={pinned ? "Unpin project" : "Pin project"}
                                  onClick={() => {
                                    setProjectMenuId(null);
                                    toggleProjectPin(p.id);
                                    notify(pinned ? "Project unpinned." : "Project pinned.");
                                  }}
                                />
                                <MenuItem
                                  icon="x"
                                  label="Delete"
                                  danger
                                  onClick={() => {
                                    setProjectMenuId(null);
                                    void removeProject(p.id);
                                  }}
                                />
                              </span>
                            </>
                          )}
                        </>
                      )}
                      {editing && (
                        <span className="mb-1 block rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] p-2">
                          <textarea
                            value={projectInstrDraft}
                            onChange={(e) => setProjectInstrDraft(e.target.value)}
                            rows={2}
                            maxLength={4000}
                            placeholder="Instructions for every run in this project"
                            className="w-full resize-y rounded-md bg-transparent text-[12px] outline-none placeholder:text-[var(--text-3)]"
                          />
                          <span className="mt-1 flex justify-end">
                            <button
                              onClick={() => void saveProjectInstructions()}
                              className="press rounded-md bg-[var(--accent)] px-2.5 py-1 text-[11px] font-semibold text-[var(--accent-contrast)]"
                            >
                              Save
                            </button>
                          </span>
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
              {orderedProjects.length > 5 && (
                <button
                  onClick={() => setShowAllProjects((v) => !v)}
                  className="press fluid mt-1 w-full rounded-lg px-2.5 py-1.5 text-left text-[12px] text-[var(--text-3)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
                >
                  {showAllProjects ? "Show less" : "Show more"}
                </button>
              )}
              {creatingProject && (
                <CreateProjectDialog
                  onClose={() => setCreatingProject(false)}
                  onCreated={(created) => {
                    setCreatingProject(false);
                    void refreshProjects().then(() => openProject(created.id));
                    notify("Project created.");
                  }}
                />
              )}
            </div>
          )}

          {/* chats section: latest 10 + bulk management lives here, not on top */}
          {!collapsed && conversations.length > 0 && (
            <>
              <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3 pt-2">
                <div className="flex items-center gap-1 px-2.5 pb-1">
                  <p className="flex-1 text-[12px] font-medium text-[var(--text-3)]">Chats</p>
                  <button
                    type="button"
                    aria-label={selecting ? "Done selecting" : "Select conversations"}
                    onClick={() => (selecting ? cancelSelecting() : setSelecting(true))}
                    className={`press rounded-md px-1.5 py-1 text-[11.5px] font-medium ${
                      selecting
                        ? "text-[var(--accent)]"
                        : "text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                    }`}
                  >
                    {selecting ? "Done" : "Select"}
                  </button>
                </div>
                {selecting && (
                  <div className="flex items-center gap-1.5 px-2.5 pb-2">
                    <span className="tabular flex-1 text-[11.5px] text-[var(--text-3)]">
                      {selectedIds.length} selected
                    </span>
                    <button
                      type="button"
                      onClick={() => void archiveSelected()}
                      disabled={selectedIds.length === 0}
                      className="press rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11.5px] font-medium text-[var(--text-2)] hover:text-[var(--text-1)] disabled:opacity-40"
                    >
                      Archive
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteSelected()}
                      disabled={selectedIds.length === 0}
                      className="press rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11.5px] font-medium text-[var(--danger)] disabled:opacity-40"
                    >
                      Delete
                    </button>
                  </div>
                )}
                {recentGroups.map((group) => (
                  <div key={group.label} className="mt-3 first:mt-1">
                    <p className="flex items-center gap-1.5 px-2.5 pb-1 text-[12px] font-medium font-medium text-[var(--text-3)]">
                      {group.label === "Pinned" && <Icon name="pin" size={10} />}
                      {group.label}
                    </p>
                    <div className="space-y-0.5">
                      {group.items.map((c) => {
                        const isPinned = pinnedIds.includes(c.id);
                        const menuOpen = menuConvId === c.id;
                        const renaming = renamingId === c.id;
                        return (
                          <div key={c.id} className="group/conv relative">
                            {renaming ? (
                              <span className="flex w-full items-center gap-1.5 rounded-lg bg-[var(--bg-raised)] py-1.5 pl-2.5 pr-2">
                                <input
                                  autoFocus
                                  value={renameDraft}
                                  onChange={(e) => setRenameDraft(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter") void saveRename(c.id);
                                    if (e.key === "Escape") setRenamingId(null);
                                  }}
                                  maxLength={256}
                                  aria-label="Rename conversation"
                                  className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none"
                                />
                                <button
                                  aria-label="Save name"
                                  title="Save"
                                  onClick={() => void saveRename(c.id)}
                                  className="press rounded-md p-1 text-[var(--accent)] hover:bg-[var(--panel-hover)]"
                                >
                                  <Icon name="check" size={12} />
                                </button>
                                <button
                                  aria-label="Cancel rename"
                                  title="Cancel"
                                  onClick={() => setRenamingId(null)}
                                  className="press rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)]"
                                >
                                  <Icon name="x" size={12} />
                                </button>
                              </span>
                            ) : (
                              <>
                                <button
                                  onClick={() => (selecting ? toggleSelected(c.id) : openConversation(c.id))}
                                  aria-pressed={selecting ? selectedIds.includes(c.id) : undefined}
                                  className={`fluid flex w-full items-center gap-2 rounded-lg py-2 pl-2.5 pr-16 text-left text-[12.5px] ${
                                    activeId === c.id && !selecting
                                      ? "bg-[var(--bg-raised)] font-medium text-[var(--text-1)] shadow-[0_1px_2px_rgba(22,23,28,0.05)]"
                                      : "text-[var(--text-2)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
                                  }`}
                                >
                                  {selecting ? (
                                    <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-md border ${
                                      selectedIds.includes(c.id)
                                        ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-contrast)]"
                                        : "border-[var(--hairline-strong)] text-transparent"
                                    }`}>
                                      <Icon name="check" size={10} strokeWidth={3} />
                                    </span>
                                  ) : (
                                    <Icon name={c.channel === "voice" ? "waveform" : "chat"} size={13} className="shrink-0 text-[var(--text-3)]" />
                                  )}
                                  <span className="min-w-0 flex-1 truncate">{c.title}</span>
                                </button>
                                {!selecting && (
                                <button
                                  aria-label={isPinned ? `Unpin ${c.title}` : `Pin ${c.title}`}
                                  title={isPinned ? "Unpin" : "Pin"}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    togglePin(c.id);
                                  }}
                                  className={`press absolute right-9 top-1/2 -translate-y-1/2 rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)] ${
                                    isPinned ? "block" : "hidden group-hover/conv:block"
                                  }`}
                                >
                                  <Icon name="pin" size={11} className={isPinned ? "text-[var(--accent)]" : ""} />
                                </button>
                                )}
                                {!selecting && (
                                <button
                                  aria-label={`More actions for ${c.title}`}
                                  title="Rename, share, duplicate"
                                  aria-haspopup="menu"
                                  aria-expanded={menuOpen}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setMenuConvId(menuOpen ? null : c.id);
                                  }}
                                  className="press absolute right-1.5 top-1/2 hidden -translate-y-1/2 rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)] group-hover/conv:block"
                                >
                                  <Icon name="chevron" size={12} className="rotate-90" />
                                </button>
                                )}
                                {menuOpen && (
                                  <>
                                    <button
                                      aria-label="Close menu"
                                      className="fixed inset-0 z-10 cursor-default"
                                      onClick={() => {
                                        setMenuConvId(null);
                                        setMoveExpanded(false);
                                      }}
                                    />
                                    <span
                                      role="menu"
                                      className="absolute right-0 top-full z-20 mt-1 w-44 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-1 shadow-[var(--shadow-pop)]"
                                    >
                                      <MenuItem
                                        icon="pencil"
                                        label="Rename"
                                        onClick={() => {
                                          setMenuConvId(null);
                                          setRenamingId(c.id);
                                          setRenameDraft(c.title);
                                        }}
                                      />
                                      <MenuItem icon="duplicate" label="Duplicate" onClick={() => void duplicateConversation(c.id)} />
                                      <MenuItem icon="share" label="Copy share link" onClick={() => void copyShareLink(c.id)} />
                                      <MenuItem
                                        icon="scroll"
                                        label="Move to project"
                                        chevron
                                        onClick={() => setMoveExpanded((v) => !v)}
                                      />
                                      {moveExpanded && (
                                        <span className="block rounded-lg bg-[var(--panel-hover)]/50 p-1">
                                          <MenuItem
                                            icon="x"
                                            label="No project"
                                            onClick={() => void moveConversation(c.id, null)}
                                          />
                                          {projects.map((p) => (
                                            <MenuItem
                                              key={p.id}
                                              icon="scroll"
                                              label={p.name}
                                              checked={c.project_id === p.id}
                                              onClick={() => void moveConversation(c.id, p.id)}
                                            />
                                          ))}
                                          {projects.length === 0 && (
                                            <p className="px-2.5 py-1 text-[11px] text-[var(--text-3)]">
                                              Create a project first.
                                            </p>
                                          )}
                                        </span>
                                      )}
                                      <MenuItem
                                        icon="clock"
                                        label="Archive"
                                        onClick={() => void archiveConversation(c.id, true)}
                                      />
                                      <MenuItem
                                        icon="x"
                                        label="Delete"
                                        danger
                                        onClick={() => {
                                          setMenuConvId(null);
                                          deleteConversation(c.id);
                                        }}
                                      />
                                    </span>
                                  </>
                                )}
                              </>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
                {recentGroups.length === 0 && (
                  <p className="px-2.5 py-3 text-[11px] text-[var(--text-3)]">No matches.</p>
                )}
                {(hasMoreChats || visibleConversations.length > 0) && !recentQuery && (
                  <button
                    type="button"
                    onClick={() => {
                      setMobileOpen(false);
                      router.push("/chats");
                    }}
                    className="press fluid mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--hairline)] py-2 text-[12px] font-medium text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
                  >
                    View all{hasMoreChats ? ` (${visibleConversations.length})` : ""}
                  </button>
                )}
                {msgMatches.length > 0 && (
                  <div className="mt-3">
                    <p className="flex items-center gap-1.5 px-2.5 pb-1 text-[12px] font-medium font-medium text-[var(--text-3)]">
                      <Icon name="search" size={10} /> In messages
                    </p>
                    <div className="space-y-0.5">
                      {msgMatches.map((m) => (
                        <button
                          key={m.id}
                          onClick={() => openConversation(m.id)}
                          className="fluid w-full rounded-lg px-2.5 py-2 text-left hover:bg-[var(--hover)]"
                        >
                          <span className="block truncate text-[12.5px] font-medium text-[var(--text-1)]">
                            {m.title}
                          </span>
                          <span className="mt-0.5 line-clamp-2 block text-[11.5px] leading-snug text-[var(--text-3)]">
                            {m.snippet}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {rowNotice && (
                  <p className="px-2.5 py-2 text-[11px] font-medium text-[var(--accent)]">{rowNotice}</p>
                )}
                {archivedConversations.length > 0 && (
                  <div className="mt-3">
                    <button
                      onClick={() => setShowArchived((v) => !v)}
                      aria-expanded={showArchived}
                      className="press fluid flex w-full items-center gap-1.5 px-2.5 pb-1 text-left text-[12px] font-medium font-medium text-[var(--text-3)] hover:text-[var(--text-1)]"
                    >
                      <Icon name="clock" size={10} />
                      Archived ({archivedConversations.length})
                      <Icon name="chevron" size={11} className={`fluid transition-transform ${showArchived ? "rotate-90" : "-rotate-90"}`} />
                    </button>
                    {showArchived && (
                      <div className="space-y-0.5">
                        {archivedConversations.map((c) => (
                          <div key={c.id} className="group/conv relative">
                            <button
                              onClick={() => openConversation(c.id)}
                              className="fluid flex w-full items-center gap-2 rounded-lg py-2 pl-2.5 pr-14 text-left text-[12.5px] text-[var(--text-3)] hover:bg-[var(--hover)] hover:text-[var(--text-1)]"
                            >
                              <Icon name="chat" size={13} className="shrink-0" />
                              <span className="min-w-0 flex-1 truncate">{c.title}</span>
                            </button>
                            <button
                              aria-label={`Restore ${c.title}`}
                              title="Restore to conversations"
                              onClick={() => void archiveConversation(c.id, false)}
                              className="press absolute right-7 top-1/2 hidden -translate-y-1/2 rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)] group-hover/conv:block"
                            >
                              <Icon name="refresh" size={11} />
                            </button>
                            <button
                              aria-label={`Delete ${c.title}`}
                              title="Delete conversation"
                              onClick={() => deleteConversation(c.id)}
                              className="press absolute right-1.5 top-1/2 hidden -translate-y-1/2 rounded-md p-1 text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--danger)] group-hover/conv:block"
                            >
                              <Icon name="x" size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
          {collapsed && <div className="flex-1" />}

          {/* user footer */}
          <div className="border-t border-[var(--hairline)] p-3">
            {collapsed ? (
              <button
                onClick={() => setMenuOpen(true)}
                title={`${user.name}, ${role}`}
                className="fluid mx-auto grid h-9 w-9 place-items-center rounded-xl bg-[var(--panel-hover)] text-[12px] font-semibold text-[var(--text-1)] hover:bg-[var(--hairline-strong)]"
              >
                {user.name.slice(0, 1).toUpperCase()}
              </button>
            ) : (
              <button
                onClick={() => setMenuOpen(true)}
                aria-expanded={menuOpen}
                className="fluid flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left hover:bg-[var(--hover)]"
              >
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[var(--accent-dim)] text-[11.5px] font-bold text-[var(--accent)]">
                  {user.name.slice(0, 1).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-medium leading-tight text-[var(--text-1)]">
                    {user.name}
                  </span>
                  <span className="block truncate text-[11px] leading-tight text-[var(--text-3)]">
                    {user.email} · {role}
                  </span>
                </span>
                <Icon name="chevron" size={12} className={`shrink-0 text-[var(--text-3)] transition-transform ${menuOpen ? "-rotate-90" : "rotate-90"}`} />
              </button>
            )}
          </div>
        </aside>

        {mobileOpen && (
          <button
            aria-label="Close menu"
            onClick={() => setMobileOpen(false)}
            className="fixed inset-0 z-20 bg-[var(--scrim)] backdrop-blur-[2px] lg:hidden"
          />
        )}

        {/* ------------------------------------------------ main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b border-[var(--hairline)] bg-[var(--glass-bg)] px-4 backdrop-blur-2xl lg:px-8">
            <button
              onClick={() => setMobileOpen((v) => !v)}
              className="press fluid grid h-8 w-8 place-items-center rounded-lg text-[var(--text-2)] hover:bg-[var(--hover)] hover:text-[var(--text-1)] lg:hidden"
              aria-label="Toggle menu"
            >
              <Icon name="panel" size={16} />
            </button>
            <button
              onClick={toggleCollapsed}
              className="press fluid hidden h-8 w-8 place-items-center rounded-lg text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)] lg:grid"
              aria-label="Toggle sidebar"
            >
              <Icon name="panel" size={16} />
            </button>

            <h1 className="text-[13.5px] font-semibold tracking-tight text-[var(--text-1)]">
              {TITLES[active] ?? "Voice-First AI"}
            </h1>

            <div className="ml-auto flex items-center gap-2">
              {platform?.demo_mode && !platform.connections?.agents && (
                <span className="flex items-center gap-1.5 rounded-full border border-[var(--warn-dim)] bg-[var(--warn-dim)] px-2.5 py-1 text-[10.5px] font-semibold text-[var(--warn)]">
                  <span className="h-1.5 w-1.5 rounded-full bg-[var(--warn)]" />
                  Demo mode
                </span>
              )}
              <ThemeToggle />
            </div>
          </header>

          {budgetAlert && (
            <div className="fluid flex items-center justify-between gap-3 border-b border-[var(--warn-dim)] bg-[var(--warn-dim)] px-6 py-2.5 text-[12px] text-[var(--warn)]">
              <span className="flex items-center gap-2 font-medium">
                <Icon name="coins" size={13} /> {budgetAlert}
              </span>
              <button onClick={() => setBudgetAlert(null)} aria-label="Dismiss" className="press fluid rounded p-1 hover:bg-[rgba(22,23,28,0.06)]">
                <Icon name="x" size={12} />
              </button>
            </div>
          )}
          <main className="min-h-0 flex-1">{children}</main>
        </div>

        {/* ------------------------------------------------ user menu */}
        {menuOpen && (
          <>
            <button
              aria-label="Close menu"
              className="fixed inset-0 z-40 cursor-default"
              onClick={() => setMenuOpen(false)}
            />
            <div className="fixed bottom-16 left-3 z-50 w-64 rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 shadow-[var(--shadow-pop)]">
              <div className="px-3 py-2.5">
                <p className="truncate text-[13px] font-medium text-[var(--text-1)]">{user.name}</p>
                <p className="truncate text-[11.5px] text-[var(--text-3)]">{user.email}</p>
              </div>
              <div className="my-1 h-px bg-[var(--hairline)]" />
              {PLATFORM_NAV.map((item) => (
                <button
                  key={item.href}
                  onClick={() => {
                    setMenuOpen(false);
                    setMobileOpen(false);
                    router.push(item.href);
                  }}
                  className="fluid flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
                >
                  <Icon name={item.icon} size={15} />
                  <span className="flex-1">{item.label}</span>
                  {item.badge === "approvals" && pending > 0 && (
                    <span className="tabular rounded-full bg-[var(--warn-dim)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--warn)]">
                      {pending}
                    </span>
                  )}
                </button>
              ))}
              <div className="my-1 h-px bg-[var(--hairline)]" />
              <button
                onClick={() => {
                  setMenuOpen(false);
                  backend.exportMine().catch(() => notify("Export failed."));
                }}
                className="fluid flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
              >
                <Icon name="download" size={15} />
                Export my data
              </button>
              <button
                onClick={() => {
                  setMenuOpen(false);
                  void signOut();
                }}
                className="fluid flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--danger)]"
              >
                <Icon name="logout" size={15} />
                Sign out
              </button>
            </div>
          </>
        )}
      </div>
        <CommandPalette
          open={paletteOpen}
          onClose={closePalette}
          conversations={conversations.map((c) => ({ id: c.id, title: c.title }))}
          navItems={PALETTE_NAV}
          onOpenConversation={openConversation}
          onNewConversation={newConversation}
          onNavigate={(href) => router.push(href)}
          onExportMine={() => {
            backend.exportMine().catch(() => notify("Export failed."));
          }}
        />
        <Toaster />
    </ShellContext.Provider>
  );
}

function Toaster() {
  const [toasts, setToasts] = useState<{ id: number; message: string }[]>([]);
  useEffect(
    () =>
      subscribeToasts((message) => {
        const id = Date.now() + Math.random();
        setToasts((prev) => [...prev.slice(-2), { id, message }]);
        window.setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, 3000);
      }),
    [],
  );
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-[60] flex -translate-x-1/2 flex-col items-center gap-1.5">
      {toasts.map((t) => (
        <p
          key={t.id}
          role="status"
          className="animate-fade-in rounded-full bg-[var(--solid)] px-4 py-2 text-[12.5px] font-medium text-[var(--on-solid)] shadow-[var(--shadow-pop)]"
        >
          {t.message}
        </p>
      ))}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
  chevron,
  checked,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
  danger?: boolean;
  chevron?: boolean;
  checked?: boolean;
}) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className={`press fluid flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] ${
        danger
          ? "text-[var(--danger)] hover:bg-[var(--panel-hover)]"
          : "text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
      }`}
    >
      <Icon name={icon} size={12} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {checked && <Icon name="check" size={12} className="shrink-0 text-[var(--accent)]" />}
      {chevron && <Icon name="chevron" size={11} className="shrink-0 text-[var(--text-3)]" />}
    </button>
  );
}
