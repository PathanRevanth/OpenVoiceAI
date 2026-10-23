"use client";

/**
 * Project detail, like the reference: big colored icon + name, Share pill,
 * "..." menu (Project settings, Pin project), a "New chat in ..." entry,
 * and Chats / Sources tabs listing the project's conversations and the
 * artifacts produced inside them.
 */

import { use, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthProvider } from "@/components/auth-context";
import { AppShell, useShell, type ConversationSummary } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { backend, type ArtifactSource, type Project } from "@/lib/backend";
import { notify } from "@/lib/notify";
import { toggleProjectPin, usePinnedProjectIds } from "@/lib/project-pins";
import { IconColorPicker, ProjectIcon } from "@/components/project-picker";

interface ChatRow extends ConversationSummary {
  snippet: string;
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function ProjectDetailInner({ projectId }: { projectId: string }) {
  const router = useRouter();
  const {
    openConversation,
    setActiveId,
    setActiveProjectId,
    refreshConversations,
    refreshProjects,
  } = useShell();
  const [project, setProject] = useState<Project | null | undefined>(undefined);
  const [chats, setChats] = useState<ChatRow[] | null>(null);
  const [sources, setSources] = useState<ArtifactSource[] | null>(null);
  const [tab, setTab] = useState<"chats" | "sources">("chats");
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const pinned = usePinnedProjectIds().includes(projectId);

  const load = useCallback(async () => {
    try {
      const all = await backend.projects();
      const found = all.find((p) => p.id === projectId) ?? null;
      setProject(found);
      if (!found) {
        setChats([]);
        setSources([]);
        return;
      }
      const convs = (await backend.conversations()).filter(
        (c) => c.project_id === projectId && !c.archived,
      );
      const details = await Promise.allSettled(
        convs.slice(0, 20).map(async (c): Promise<ChatRow> => {
          let snippet = "";
          try {
            const full = await backend.conversation(c.id);
            const firstUser = full.messages.find(
              (m) => m.role === "user" && m.content?.text?.trim(),
            );
            snippet = (firstUser?.content.text ?? "").slice(0, 140);
          } catch {
            /* snippet is best-effort */
          }
          return { ...c, snippet };
        }),
      );
      // Keep backend recency order for fulfilled rows; extras without snippets last.
      const rows: ChatRow[] = [];
      const byId = new Map(
        details
          .filter((r): r is PromiseFulfilledResult<ChatRow> => r.status === "fulfilled")
          .map((r) => [r.value.id, r.value.snippet] as const),
      );
      for (const c of convs) rows.push({ ...c, snippet: byId.get(c.id) ?? "" });
      setChats(rows);
      try {
        const idx = await backend.artifactsIndex(200);
        setSources(idx.filter((a) => a.project_id === projectId));
      } catch {
        setSources([]);
      }
    } catch {
      setProject(null);
      setChats([]);
      setSources([]);
    }
  }, [projectId]);

  useEffect(() => {
    setActiveProjectId(projectId);
    void load();
  }, [projectId, setActiveProjectId, load]);

  const newChatInProject = useCallback(() => {
    setActiveProjectId(projectId);
    setActiveId(null);
    router.push("/chat");
  }, [projectId, router, setActiveId, setActiveProjectId]);

  const openChat = useCallback(
    (id: string) => {
      setActiveProjectId(projectId);
      openConversation(id);
    },
    [openConversation, projectId, setActiveProjectId],
  );

  const shareProject = useCallback(async () => {
    setMenuOpen(false);
    try {
      await navigator.clipboard?.writeText(`${window.location.origin}/projects/${projectId}`);
      notify("Link copied to clipboard.");
    } catch {
      notify("Could not copy the link.");
    }
  }, [projectId]);

  const togglePin = useCallback(() => {
    setMenuOpen(false);
    toggleProjectPin(projectId);
    notify(pinned ? "Project unpinned." : "Project pinned.");
  }, [pinned, projectId]);

  const chatCount = useMemo(() => chats?.length ?? 0, [chats]);

  if (project === undefined || chats === null) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-[13px] text-[var(--text-3)]">Loading project…</p>
      </div>
    );
  }

  if (project === null) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-[16px] font-medium text-[var(--text-1)]">Project not found</p>
        <p className="mt-2 text-[13.5px] text-[var(--text-2)]">
          It may have been deleted.
        </p>
        <button
          type="button"
          onClick={() => router.push("/projects")}
          className="press mt-4 rounded-xl border border-[var(--hairline)] px-4 py-2 text-[13px] font-medium text-[var(--text-1)] hover:border-[var(--hairline-strong)]"
        >
          Back to projects
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <div className="flex items-center gap-3">
        <ProjectIcon icon={project.icon} color={project.color} size={26} className="shrink-0" />
        <h1 className="min-w-0 flex-1 truncate text-[26px] font-semibold tracking-tight text-[var(--text-1)]">
          {project.name}
        </h1>
        <button
          type="button"
          onClick={() => void shareProject()}
          className="press flex shrink-0 items-center gap-1.5 rounded-full border border-[var(--hairline)] px-4 py-2 text-[13.5px] font-medium text-[var(--text-1)] hover:border-[var(--hairline-strong)]"
        >
          <Icon name="share" size={14} /> Share
        </button>
        <span className="relative shrink-0">
          <button
            type="button"
            aria-label="Project options"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
            className="press grid h-10 w-10 place-items-center rounded-full border border-[var(--hairline)] text-[var(--text-1)] hover:border-[var(--hairline-strong)]"
          >
            <Icon name="more" size={16} />
          </button>
          {menuOpen && (
            <>
              <button
                aria-label="Close menu"
                className="fixed inset-0 z-10 cursor-default"
                onClick={() => setMenuOpen(false)}
              />
              <span
                role="menu"
                className="absolute right-0 top-full z-20 mt-1 w-48 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 shadow-[var(--shadow-pop)]"
              >
                <button
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setSettingsOpen(true);
                  }}
                  className="press fluid flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13.5px] text-[var(--text-1)] hover:bg-[var(--panel-hover)]"
                >
                  <Icon name="gear" size={15} /> Project settings
                </button>
                <button
                  role="menuitem"
                  onClick={() => void togglePin()}
                  className="press fluid flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13.5px] text-[var(--text-1)] hover:bg-[var(--panel-hover)]"
                >
                  <Icon name="pin" size={15} /> {pinned ? "Unpin project" : "Pin project"}
                </button>
              </span>
            </>
          )}
        </span>
      </div>

      <button
        type="button"
        onClick={newChatInProject}
        className="press fluid mt-6 flex w-full items-center gap-2.5 rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-4 py-3 text-left text-[14px] text-[var(--text-3)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
      >
        <Icon name="plus" size={16} className="shrink-0 text-[var(--text-1)]" />
        <span className="min-w-0 flex-1 truncate">New chat in {project.name}</span>
      </button>

      <div className="mt-5 flex gap-1">
        {(["chats", "sources"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            aria-pressed={tab === t}
            className={`press rounded-full px-4 py-1.5 text-[13.5px] font-medium capitalize ${
              tab === t
                ? "bg-[var(--panel-hover)] text-[var(--text-1)]"
                : "text-[var(--text-3)] hover:text-[var(--text-1)]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "chats" ? (
        chatCount === 0 ? (
          <p className="mt-8 text-[13.5px] text-[var(--text-3)]">
            No chats yet. Start one above and it will appear here.
          </p>
        ) : (
          <div className="mt-4">
            {chats.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => openChat(c.id)}
                className="press fluid flex w-full items-start gap-3 py-3.5 text-left"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14.5px] font-semibold text-[var(--text-1)]">
                    {c.title}
                  </span>
                  {c.snippet && (
                    <span className="mt-0.5 block truncate text-[13px] text-[var(--text-3)]">
                      {c.snippet}
                    </span>
                  )}
                </span>
                <span className="shrink-0 pt-0.5 text-[12.5px] text-[var(--text-3)]">
                  {shortDate(c.updated_at)}
                </span>
              </button>
            ))}
          </div>
        )
      ) : sources === null ? (
        <p className="mt-8 text-[13px] text-[var(--text-3)]">Loading sources…</p>
      ) : sources.length === 0 ? (
        <p className="mt-8 text-[13.5px] text-[var(--text-3)]">
          No sources yet. Artifacts created in this project's chats will appear here.
        </p>
      ) : (
        <div className="mt-4">
          {sources.map((s) => (
            <button
              key={`${s.conversation_id}:${s.message_id}`}
              type="button"
              onClick={() => openChat(s.conversation_id)}
              className="press fluid flex w-full items-start gap-3 py-3.5 text-left"
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--panel-hover)] text-[var(--text-2)]">
                <Icon name="file" size={14} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14px] font-medium text-[var(--text-1)]">
                  {s.conversation_title}
                </span>
                <span className="mt-0.5 block truncate text-[13px] text-[var(--text-3)]">
                  {s.text.slice(0, 140)}
                </span>
              </span>
              <span className="shrink-0 pt-0.5 text-[12.5px] text-[var(--text-3)]">
                {s.created_at ? shortDate(s.created_at) : ""}
              </span>
            </button>
          ))}
        </div>
      )}

      {settingsOpen && (
        <ProjectSettingsDialog
          project={project}
          onClose={() => setSettingsOpen(false)}
          onSaved={(updated) => {
            setSettingsOpen(false);
            setProject(updated);
            void Promise.all([refreshProjects(), refreshConversations()]);
            notify("Project settings saved.");
          }}
        />
      )}
    </div>
  );
}

/** Rename, recolor, and set instructions without leaving the project page. */
function ProjectSettingsDialog({
  project,
  onClose,
  onSaved,
}: {
  project: Project;
  onClose: () => void;
  onSaved: (project: Project) => void;
}) {
  const [name, setName] = useState(project.name);
  const [icon, setIcon] = useState(project.icon || "folder");
  const [color, setColor] = useState(project.color || "slate");
  const [instructions, setInstructions] = useState(project.instructions || "");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const title = name.trim();
    if (!title || saving) return;
    setSaving(true);
    try {
      const updated = await backend.updateProject(project.id, {
        name: title,
        icon,
        color,
        instructions: instructions.trim(),
      });
      onSaved(updated);
    } catch {
      /* keep the dialog open on failure */
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <button aria-label="Close settings" className="fixed inset-0 z-50 cursor-default bg-[var(--scrim)]" onClick={onClose} />
      <div
        role="dialog"
        aria-label="Project settings"
        className="fixed left-1/2 top-1/2 z-50 max-h-[88dvh] w-[min(480px,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-6 shadow-[var(--shadow-pop)]"
      >
        <span className="flex items-start justify-between">
          <h2 className="text-[20px] font-medium tracking-tight text-[var(--text-1)]">Project settings</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="press rounded-lg p-1.5 text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
          >
            <Icon name="x" size={16} />
          </button>
        </span>
        <label className="mt-5 block text-[14px] text-[var(--text-1)]" htmlFor="project-settings-name">
          Project name
        </label>
        <span className="relative mt-1.5 block">
          <span className="flex h-11 items-center rounded-xl border border-[var(--hairline-strong)] bg-[var(--bg)] focus-within:border-[var(--accent-border)]">
            <button
              type="button"
              aria-label="Choose project icon and color"
              onClick={() => setPickerOpen((v) => !v)}
              aria-expanded={pickerOpen}
              className="press grid h-full w-11 shrink-0 place-items-center rounded-l-xl bg-[var(--panel-hover)]/60"
            >
              <ProjectIcon icon={icon} color={color} size={17} />
            </button>
            <input
              id="project-settings-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={128}
              className="fluid h-full min-w-0 flex-1 bg-transparent px-3 text-[14px] outline-none placeholder:text-[var(--text-3)]"
            />
          </span>
          {pickerOpen && (
            <span className="absolute inset-x-0 top-full z-10 mt-1 block">
              <IconColorPicker icon={icon} color={color} onIcon={setIcon} onColor={setColor} />
            </span>
          )}
        </span>
        <label className="mt-4 block text-[14px] text-[var(--text-1)]" htmlFor="project-settings-instructions">
          Project instructions
        </label>
        <textarea
          id="project-settings-instructions"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          rows={4}
          maxLength={4000}
          placeholder="Instructions applied to every run in this project"
          className="mt-1.5 w-full resize-y rounded-xl border border-[var(--hairline)] bg-[var(--bg)] p-3 text-[13.5px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
        />
        <span className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="press rounded-xl border border-[var(--hairline)] px-4 py-2 text-[13.5px] font-medium text-[var(--text-2)] hover:text-[var(--text-1)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!name.trim() || saving}
            className="press rounded-xl bg-[var(--solid)] px-4 py-2 text-[13.5px] font-semibold text-[var(--on-solid)] hover:opacity-90 disabled:opacity-40"
          >
            Save changes
          </button>
        </span>
      </div>
    </>
  );
}

export default function ProjectDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <AuthProvider>
      <AppShell>
        <ProjectDetailInner projectId={id} />
      </AppShell>
    </AuthProvider>
  );
}
