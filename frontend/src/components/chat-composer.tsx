"use client";

/**
 * ChatComposer  -  the glassmorphism message input.
 *
 * Features:
 * - frosted-glass shell (deep backdrop blur, sheen, inner highlight)
 * - agent/model-style selector: talk to the orchestrator ("Auto") or address a
 *   specialist directly, exactly like picking a model
 * - attachments (images / PDF / text, max 4) with thumbnail chips + paste support
 * - voice orb, auto-growing textarea, Enter-to-send
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence } from "motion/react";
import { Icon } from "@/components/icons";
import { ProviderLogo, AgentLogo, agentMark } from "@/components/provider-logo";
import type { OrbMode } from "@/components/voice-orb";
import {
  ModelSelectorContent,
  ModelSelectorEffort,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorRoot,
  ModelSelectorTrigger,
  type ModelOption,
} from "@/components/model-selector";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { PlanTodoPanel, type PlanCardState } from "@/components/ai-elements/plan-todo";
import {
  backend,
  type AgentInfo,
  type ChatAttachmentInput,
  type ModelCatalog,
  type PromptTemplate,
  type ReasoningEffort,
  type VoiceCatalog,
} from "@/lib/backend";

const EFFORT_LABEL: Record<ReasoningEffort, string> = {
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
};

const MAX_ATTACHMENTS = 4;
const MAX_FILE_BYTES = 3.5 * 1024 * 1024;
const ACCEPTED = /^(image\/|application\/pdf|text\/)/;

const AGENT_ICONS: Record<string, Parameters<typeof Icon>[0]["name"]> = {
  orchestrator: "bot",
  github: "wrench",
  slack: "chat",
  mongodb: "panel",
  research: "spark",
  marketing: "chart",
};

type ToolsView = "menu" | "instructions" | "prompts" | "voice";

/** Short display name for the model trigger ("Claude Haiku 4.5" -> "Haiku 4.5"). */
function shortModelName(name: string): string {
  const short = name.replace(/^(Claude|Anthropic|Gemini|Google|GPT|OpenAI|ChatGPT)\s+/i, "").trim();
  return short || name;
}

interface ComposerAttachment extends ChatAttachmentInput {
  previewUrl?: string;
}

function agentIcon(slug: string | null): Parameters<typeof Icon>[0]["name"] {
  return AGENT_ICONS[slug ?? "orchestrator"] ?? "bot";
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

async function fileToAttachment(file: File): Promise<ComposerAttachment | string> {
  if (!ACCEPTED.test(file.type)) {
    return `${file.name}: unsupported type (images, PDF, or text only)`;
  }
  if (file.size > MAX_FILE_BYTES) {
    return `${file.name}: too large (max ${formatBytes(MAX_FILE_BYTES)})`;
  }
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("read failed"));
    reader.readAsDataURL(file);
  });
  const base64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
  return {
    name: file.name,
    media_type: file.type || "text/plain",
    data: base64,
    previewUrl: file.type.startsWith("image/") ? dataUrl : undefined,
  };
}

export function ChatComposer({
  busy,
  voiceMode,
  onSend,
  onVoiceToggle,
  plan,
  model,
  onModelChange,
  effort,
  onEffortChange,
  voice,
  onVoiceChange,
  draftKey,
}: {
  busy: boolean;
  voiceMode: OrbMode;
  onSend: (message: string, attachments: ChatAttachmentInput[], agent: string | null) => void;
  onVoiceToggle: () => void;
  plan: PlanCardState | null;
  /** Selected chat model as a pydantic-ai model string; null = platform default. */
  model: string | null;
  onModelChange: (model: string | null) => void;
  /** Reasoning effort for the selected model; null = the provider's default. */
  effort: ReasoningEffort | null;
  onEffortChange: (effort: ReasoningEffort | null) => void;
  /** Selected realtime voice id for the active provider; null = provider default. */
  voice: string | null;
  onVoiceChange: (voice: string | null) => void;
  /** Conversation the composer is writing to (null = new chat); scopes drafts. */
  draftKey: string | null;
}) {
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [agentSlug, setAgentSlug] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [webSearch, setWebSearch] = useState(false);
  const [plusOpen, setPlusOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [toolsView, setToolsView] = useState<ToolsView>("menu");
  const [instructions, setInstructions] = useState("");
  const [instrDraft, setInstrDraft] = useState("");
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newBody, setNewBody] = useState("");

  const openTools = useCallback((view: ToolsView) => {
    setToolsView(view);
    setPlusOpen(true);
    setSearchOpen(false);
  }, []);

  const openSearchTools = useCallback((view: ToolsView) => {
    setToolsView(view);
    setSearchOpen(true);
    setPlusOpen(false);
  }, []);

  const closeTools = useCallback(() => {
    setPlusOpen(false);
    setSearchOpen(false);
  }, []);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Unsent input follows the conversation: switching chats stashes the draft
  // under the old key and restores the new one's.
  const inputRef = useRef(input);
  inputRef.current = input;
  const prevDraftKeyRef = useRef<string | null>(null);
  useEffect(() => {
    const key = draftKey ?? "new";
    const prev = prevDraftKeyRef.current;
    if (prev === null) {
      prevDraftKeyRef.current = key;
      return;
    }
    if (prev !== key) {
      try {
        const cur = inputRef.current;
        if (cur.trim()) window.localStorage.setItem(`vf:draft:${prev}`, cur);
        else window.localStorage.removeItem(`vf:draft:${prev}`);
      } catch {
        /* storage unavailable */
      }
      let restored = "";
      try {
        restored = window.localStorage.getItem(`vf:draft:${key}`) ?? "";
      } catch {
        /* storage unavailable */
      }
      prevDraftKeyRef.current = key;
      setInput(restored);
      window.setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
          textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
        }
      }, 30);
    }
  }, [draftKey]);

  const clearDraft = useCallback(() => {
    const key = draftKey ?? "new";
    prevDraftKeyRef.current = key;
    try {
      window.localStorage.removeItem(`vf:draft:${key}`);
    } catch {
      /* storage unavailable */
    }
  }, [draftKey]);

  // Recently used models, pinned to the top of the picker.
  const [recentIds, setRecentIds] = useState<string[]>(() => {
    try {
      const parsed: unknown = JSON.parse(window.localStorage.getItem("vf:recent-models") ?? "[]");
      return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, 4) : [];
    } catch {
      return [];
    }
  });
  const rememberModel = useCallback((id: string) => {
    setRecentIds((prev) => {
      const next = [id, ...prev.filter((x) => x !== id)].slice(0, 4);
      try {
        window.localStorage.setItem("vf:recent-models", JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  }, []);
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);

  useEffect(() => {
    let alive = true;
    backend
      .agents()
      .then((list) => {
        if (alive) setAgents(list.filter((a) => a.status === "enabled" && a.kind === "specialist"));
      })
      .catch(() => {});
    backend
      .voiceCatalog()
      .then((c) => {
        if (alive) setCatalog(c);
      })
      .catch(() => {});
    backend
      .getInstructions()
      .then((r) => {
        if (alive) {
          setInstructions(r.instructions);
          setInstrDraft(r.instructions);
        }
      })
      .catch(() => {});
    backend
      .prompts()
      .then((rows) => {
        if (alive) setTemplates(rows);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    // Template cards on the Artifacts page prefill the composer this way.
    try {
      const draft = window.sessionStorage.getItem("vf:composer-draft");
      if (draft) {
        window.sessionStorage.removeItem("vf:composer-draft");
        setInput(draft);
        window.setTimeout(() => {
          if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
            textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
            textareaRef.current.focus();
          }
        }, 60);
      }
    } catch {
      /* storage unavailable */
    }
  }, []);

  // --- model + voice selection (voices follow the model's provider) ---

  const effectiveModel = model ?? catalog?.default_chat_model ?? "";
  const modelProvider = effectiveModel.split(":", 1)[0] || "openai";
  const providerMeta = catalog?.providers[modelProvider] ?? null;

  // Live model list from each provider's own API (refreshed every minute and
  // whenever the picker opens); providers that cannot be reached say why.
  const [live, setLive] = useState<ModelCatalog | null>(null);
  const refreshLive = useCallback((refresh: boolean) => {
    backend
      .modelCatalog(refresh)
      .then(setLive)
      .catch(() => {});
  }, []);
  useEffect(() => {
    refreshLive(false);
    const timer = setInterval(() => refreshLive(false), 60_000);
    return () => clearInterval(timer);
  }, [refreshLive]);

  const liveProviders = useMemo(() => Object.values(live?.providers ?? {}), [live]);

  const modelOptions: ModelOption[] = useMemo(() => {
    const out: ModelOption[] = [];
    for (const p of liveProviders) {
      if (p.status !== "ok") continue;
      for (const m of p.models) {
        out.push({
          id: m.id,
          name: m.name,
          description: m.efforts.length ? `${p.label} · Thinking` : p.label,
          keywords: [p.label, p.provider],
          efforts: m.efforts.length
            ? m.efforts.map((e) => ({ id: e, name: EFFORT_LABEL[e] }))
            : undefined,
        });
      }
    }
    return out;
  }, [liveProviders]);

  // A configured alias (for example `claude-haiku-4-5`) resolves to the dated id
  // the provider lists (`claude-haiku-4-5-20251001`), so the picker shows it.
  const pickerValue =
    modelOptions.find((o) => o.id === effectiveModel)?.id ??
    modelOptions.find((o) => o.id.startsWith(`${effectiveModel}-`))?.id ??
    effectiveModel;
  const selectedOption = modelOptions.find((o) => o.id === pickerValue) ?? null;
  const supportsEffort = Array.isArray(selectedOption?.efforts);
  const recentOptions = useMemo(
    () =>
      recentIds
        .map((id) => modelOptions.find((o) => o.id === id))
        .filter((o): o is ModelOption => Boolean(o)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [recentIds, liveProviders],
  );
  const modelShort = selectedOption ? shortModelName(selectedOption.name) : "Model";
  const effortShort = supportsEffort && effort ? EFFORT_LABEL[effort] : null;

  // Shift+Ctrl+U toggles voice mode from anywhere in the composer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && !e.metaKey && e.key.toLowerCase() === "u") {
        e.preventDefault();
        onVoiceToggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onVoiceToggle]);

  const voiceChoices = providerMeta?.voices ?? [];
  const voiceCapable = providerMeta?.voice_capable ?? false;
  const voiceAvailable = providerMeta?.available ?? false;
  const voiceLabel = voice
    ? (voiceChoices.find((v) => v.id === voice)?.label ?? voice)
    : "Default voice";

  const selectModel = useCallback(
    (value: string) => {
      onModelChange(value === catalog?.default_chat_model ? null : value);
      rememberModel(value);
      // Reasoning effort only carries over to a model that offers the same level.
      const next = modelOptions.find((o) => o.id === value);
      const levels = Array.isArray(next?.efforts) ? next.efforts.map((e) => e.id) : [];
      if (effort && !levels.includes(effort)) onEffortChange(null);
      // Voices belong to the model's provider; reset when it changes.
      const provider = value.split(":", 1)[0];
      setPlusOpen(false);
      setSearchOpen(false);
      if (voice && !(catalog?.providers[provider]?.voices ?? []).some((v) => v.id === voice)) {
        onVoiceChange(null);
      }
    },
    [catalog, effort, modelOptions, onEffortChange, onModelChange, onVoiceChange, rememberModel, voice],
  );

  const selected = agents.find((a) => a.slug === agentSlug) ?? null;

  const SLASH_COMMANDS = useMemo(
    () => [
      { cmd: "/research", desc: "Search the web via the Research agent", hint: "web" },
      { cmd: "/plan", desc: "Ask for an execution plan before acting", hint: "plan" },
      { cmd: "/summarize", desc: "Summarize this conversation so far", hint: "text" },
      { cmd: "/code", desc: "Answer with starter code as an artifact", hint: "code" },
    ],
    [],
  );
  const showSlash = input.startsWith("/") && !input.includes(" ");
  const slashMatches = useMemo(
    () => SLASH_COMMANDS.filter((c) => c.cmd.startsWith(input.toLowerCase())),
    [SLASH_COMMANDS, input],
  );

  const applySlash = useCallback(
    (cmd: string) => {
      if (cmd === "/research") {
        setAgentSlug("research");
        setWebSearch(true);
        setInput("");
      } else if (cmd === "/plan") {
        setInput("Create an execution plan for: ");
      } else if (cmd === "/summarize") {
        setInput("Summarize our conversation so far, with key decisions and open items.");
      } else if (cmd === "/code") {
        setInput("Write starter code for the following. Return it as an artifact code fence: ");
      }
      textareaRef.current?.focus();
    },
    [],
  );

  const saveInstructions = useCallback(() => {
    const value = instrDraft.trim();
    backend
      .setInstructions(value)
      .then((r) => {
        setInstructions(r.instructions);
        setPlusOpen(false);
        setSearchOpen(false);
      })
      .catch(() => setNotice("Could not save instructions."));
  }, [instrDraft]);

  const clearInstructions = useCallback(() => {
    backend
      .setInstructions("")
      .then((r) => {
        setInstructions(r.instructions);
        setInstrDraft("");
        setPlusOpen(false);
        setSearchOpen(false);
      })
      .catch(() => setNotice("Could not clear instructions."));
  }, []);

  const saveTemplate = useCallback(() => {
    const title = newTitle.trim();
    const body = newBody.trim();
    if (!title || !body) return;
    backend
      .createPrompt(title, body)
      .then((row) => {
        setTemplates((prev) => [...prev, row]);
        setNewTitle("");
        setNewBody("");
      })
      .catch(() => setNotice("Could not save this prompt."));
  }, [newTitle, newBody]);

  const removeTemplate = useCallback((id: string) => {
    setTemplates((prev) => prev.filter((t) => t.id !== id));
    backend.deletePrompt(id).catch(() => {});
  }, []);

  const voiceHint = !voiceCapable
    ? (providerMeta?.reason ?? "Voice unavailable for this provider")
    : !voiceAvailable
      ? "No realtime key - browser speech will be used"
      : `${providerMeta?.label ?? modelProvider} realtime voices`;

  const menuCtx: ToolsMenuCtx = {
    view: toolsView,
    setView: setToolsView,
    close: closeTools,
    webSearch,
    onToggleSearch: () => setWebSearch((v) => !v),
    uploadDisabled: attachments.length >= MAX_ATTACHMENTS,
    onUpload: () => {
      closeTools();
      fileInputRef.current?.click();
    },
    agents,
    agentSlug,
    onAgent: (slug) => {
      // Picking Research turns on web search with cited sources.
      setAgentSlug(slug);
      setWebSearch(slug === "research");
      closeTools();
      textareaRef.current?.focus();
    },
    voiceCapable,
    voiceHint,
    voices: voiceChoices,
    voice,
    voiceLabel,
    onVoice: (id) => {
      onVoiceChange(id);
      setToolsView("menu");
    },
    instructionsOn: instructions !== "",
    instrDraft,
    setInstrDraft,
    onSaveInstructions: saveInstructions,
    onClearInstructions: clearInstructions,
    starters: SLASH_COMMANDS,
    onStarter: (cmd) => {
      applySlash(cmd);
      closeTools();
    },
    templates,
    onInsertTemplate: (body) => {
      setInput(body);
      closeTools();
      textareaRef.current?.focus();
    },
    onDeleteTemplate: removeTemplate,
    newTitle,
    setNewTitle,
    newBody,
    setNewBody,
    onSaveTemplate: saveTemplate,
  };

  const submit = useCallback(() => {
    const text = input.trim();
    if (!text || busy) return;
    if (text.startsWith("/")) {
      const match = SLASH_COMMANDS.find((c) => c.cmd === text.toLowerCase());
      if (match) {
        applySlash(match.cmd);
        return;
      }
    }
    const effectiveAgent = agentSlug ?? (webSearch ? "research" : null);
    onSend(text, attachments, effectiveAgent);
    setInput("");
    setAttachments([]);
    clearDraft();
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }, [input, busy, attachments, agentSlug, webSearch, onSend, clearDraft, SLASH_COMMANDS, applySlash]);

  const addFiles = useCallback(async (files: FileList | File[] | null) => {
    if (!files) return;
    const incoming = Array.from(files);
    const results = await Promise.all(incoming.map(fileToAttachment));
    const next: ComposerAttachment[] = [];
    const errors: string[] = [];
    for (const r of results) {
      if (typeof r === "string") errors.push(r);
      else next.push(r);
    }
    setAttachments((prev) => {
      const room = MAX_ATTACHMENTS - prev.length;
      if (next.length > room) errors.push(`max ${MAX_ATTACHMENTS} attachments`);
      return [...prev, ...next.slice(0, room)];
    });
    setNotice(errors.length ? errors[0] : null);
  }, []);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files ?? []);
    if (files.length) {
      e.preventDefault();
      void addFiles(files);
    }
  };

  const voiceActive = voiceMode !== "idle";
  // The live to-do panel rides on the composer for the whole execution
  // lifecycle (approval itself happens on the Plan card in the thread).
  const planPanel =
    plan !== null &&
    plan.phase !== "awaiting" &&
    plan.phase !== "declined" &&
    !(plan.phase === "streaming" && plan.items.length === 0);

  return (
    <div className="mx-auto w-full max-w-3xl px-6 pb-6 pt-2">
      {/* stop / regenerate chips live above; render nothing here when idle-error-free */}
      {notice && (
        <p className="mb-2 text-center text-[11px] font-medium text-[var(--danger)]">{notice}</p>
      )}

      <AnimatePresence initial={false}>
        {planPanel && <PlanTodoPanel key="plan-todo" plan={plan} />}
      </AnimatePresence>

      <ModelSelectorRoot
        models={modelOptions}
        value={pickerValue}
        onValueChange={selectModel}
        effort={effort ?? undefined}
        onEffortChange={(next) => onEffortChange?.(next as ReasoningEffort)}
        onOpenChange={(open) => {
          if (open) refreshLive(false);
        }}
      >
      <div
        className={
          "border border-[var(--hairline)] bg-[var(--sidebar)] p-2 shadow-[var(--shadow-card)] " +
          (planPanel
            ? "rounded-b-[26px] rounded-t-none border-t-0"
            : "rounded-[28px]")
        }
      >
        {/* active-state chips (agent selection lives in the pill) + attachment chips */}
        {(instructions || attachments.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5 px-2.5 pb-2 pt-1.5">
          {instructions && (
            <button
              type="button"
              onClick={() => openTools("instructions")}
              title="Custom instructions are on for every run"
              className="press fluid flex items-center gap-1.5 rounded-full border border-[var(--accent-border)] bg-[var(--accent-dim)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-1)]"
            >
              <Icon name="sliders" size={12} className="text-[var(--accent)]" />
              Instructions on
            </button>
          )}
          {/* model trigger + list live in the bottom row, inside this Root */}


          {/* voice + agent pickers live in the + menu now */}


          {/* attachment chips */}
          {attachments.map((a, i) => (
            <span
              key={`${a.name}-${i}`}
              className="fluid flex items-center gap-1.5 rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)]/60 py-1 pl-1.5 pr-1 text-[11px] text-[var(--text-2)]"
            >
              {a.previewUrl ? (
                <img src={a.previewUrl} alt="" className="h-5 w-5 rounded-full object-cover" />
              ) : (
                <Icon name="scroll" size={12} className="text-[var(--text-3)]" />
              )}
              <span className="max-w-[110px] truncate">{a.name}</span>
              <button
                type="button"
                aria-label={`Remove ${a.name}`}
                onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                className="press fluid grid h-4 w-4 place-items-center rounded-full text-[var(--text-3)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
              >
                <Icon name="x" size={9} strokeWidth={2} />
              </button>
            </span>
          ))}
        </div>
        )}

        {/* white input card */}
        <div className="rounded-[22px] border border-[var(--hairline)] bg-[var(--bg-raised)] px-4 pb-2.5 pt-3.5">
        {/* bottom row: plus · search · model · mic · send */}
        <div className="relative">
          {showSlash && slashMatches.length > 0 && (
            <div className="composer-pop absolute bottom-full left-10 z-20 mb-2 w-80 rounded-2xl p-1.5">
              <p className="px-2.5 pb-1 pt-1.5 text-[12px] font-semibold font-medium text-[var(--text-3)]">
                Commands
              </p>
              {slashMatches.map((c) => (
                <button
                  key={c.cmd}
                  type="button"
                  onClick={() => applySlash(c.cmd)}
                  className="press fluid flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-[var(--panel-hover)]"
                >
                  <span className="rounded bg-[var(--panel-hover)] px-1.5 py-0.5 font-mono text-[11px] font-medium text-[var(--text-1)]">
                    {c.cmd}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-[var(--text-3)]">{c.desc}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <textarea
          ref={textareaRef}
          value={input}
          rows={1}
          onChange={(e) => {
            setInput(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={busy ? "Working…" : voiceActive ? "Listening…" : "Ask anything..."}
          className="max-h-40 min-h-[44px] w-full resize-none bg-transparent px-1.5 py-2 text-[15px] leading-relaxed outline-none placeholder:text-[var(--text-3)]"
        />
        <div className="mt-1 flex items-center gap-1">
          {/* + menu (becomes x while the mode menu is open) */}
          {searchOpen ? (
            <button
              type="button"
              aria-label="Close"
              onClick={() => setSearchOpen(false)}
              className="press fluid grid h-9 w-9 shrink-0 place-items-center rounded-full text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
            >
              <Icon name="x" size={16} />
            </button>
          ) : (
          <Popover
            open={plusOpen}
            onOpenChange={(open) => {
              setPlusOpen(open);
              if (open) {
                setToolsView("menu");
                setSearchOpen(false);
              }
            }}
          >
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label="Tools"
                className="press fluid grid h-9 w-9 shrink-0 place-items-center rounded-full text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
              >
                <Icon name="plus" size={17} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              side="top"
              align="start"
              className="max-h-[380px] w-60 overflow-y-auto border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 text-[var(--text-1)]"
            >
              <ToolsMenuBody variant="plus" menu={menuCtx} />
            </PopoverContent>
          </Popover>
          )}
          {/* Agent mode pill: Orchestrator by default, specialist when picked */}
          <Popover
            open={searchOpen}
            onOpenChange={(open) => {
              setSearchOpen(open);
              if (open) {
                setToolsView("menu");
                setPlusOpen(false);
              }
            }}
          >
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label="Select agent"
                aria-expanded={searchOpen}
                className={`press fluid flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[13px] ${
                  agentSlug
                    ? "border-[var(--accent-border)] bg-[var(--accent-dim)] font-medium text-[var(--text-1)]"
                    : "border-[var(--hairline)] text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
                }`}
              >
                {agentMark(agentSlug) ? (
                  <AgentLogo slug={agentSlug ?? ""} size={15} />
                ) : (
                  <Icon name={agentIcon(agentSlug)} size={13} className={agentSlug ? "text-[var(--accent)]" : ""} />
                )}
                <span>{agentSlug ? (selected?.name ?? agentSlug) : "Orchestrator"}</span>
                <Icon
                  name="chevron"
                  size={12}
                  className={`fluid text-[var(--text-3)] transition-transform ${searchOpen ? "-rotate-90" : "rotate-90"}`}
                />
              </button>
            </PopoverTrigger>
            <PopoverContent
              side="top"
              align="start"
              className="max-h-[380px] w-60 overflow-y-auto border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 text-[var(--text-1)]"
            >
              <ToolsMenuBody variant="search" menu={menuCtx} />
            </PopoverContent>
          </Popover>
          <span className="min-w-0 flex-1" />
          {/* Model */}
          <ModelSelectorTrigger
            variant="ghost"
            className="h-8 shrink-0 gap-1.5 px-2 text-[13px] font-normal text-[var(--text-2)] hover:text-[var(--text-1)]"
          >
            <ProviderLogo provider={modelProvider} size={14} fallbackTile={providerMeta?.tile} />
            <span className="max-w-[150px] truncate">{modelShort}</span>
            {effortShort && <span className="shrink-0 text-[var(--text-3)]">{effortShort}</span>}
          </ModelSelectorTrigger>
          <ModelSelectorContent
            side="top"
            align="end"
            className="w-80 border-[var(--hairline)] bg-[var(--bg-raised)] p-1.5 text-[var(--text-1)]"
          >
            <a
              href="/connections"
              className="press fluid mb-1 flex items-center justify-between rounded-xl bg-[var(--accent-dim)] px-3 py-2 text-[13px] font-medium text-[var(--text-1)] hover:bg-[var(--panel-hover)]"
            >
              Access the top AI models
              <Icon name="chevron" size={14} />
            </a>
            <ModelSelectorList className="max-h-72 overflow-y-auto">
              {recentOptions.length > 0 && (
                <ModelSelectorGroup heading="Recent">
                  {recentOptions.map((o) => (
                    <ModelSelectorItem key={o.id} model={o}>
                      <span className="flex min-w-0 items-center gap-2.5">
                        <ProviderLogo provider={o.id.split(":", 1)[0]} size={15} className="shrink-0" />
                        <span className="truncate text-[13.5px] font-medium text-[var(--text-1)]">
                          {o.name}
                        </span>
                      </span>
                    </ModelSelectorItem>
                  ))}
                </ModelSelectorGroup>
              )}
              {liveProviders.map((p) => {
                const options = modelOptions.filter((o) => o.id.startsWith(`${p.provider}:`));
                if (p.status !== "ok") {
                  return (
                    <ModelSelectorGroup key={p.provider} heading={p.label}>
                      <span
                        aria-disabled
                        className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 opacity-55"
                        title={p.message ?? "Models are unavailable right now."}
                      >
                        <ProviderLogo provider={p.provider} size={15} className="shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text-2)]">
                          {p.label}
                        </span>
                        <Icon name="lock" size={13} className="shrink-0 text-[var(--text-3)]" />
                      </span>
                    </ModelSelectorGroup>
                  );
                }
                if (!options.length) return null;
                return (
                  <ModelSelectorGroup key={p.provider} heading={p.label}>
                    {options.map((o) => (
                      <ModelSelectorItem key={o.id} model={o}>
                        <span className="flex min-w-0 items-center gap-2.5">
                          <ProviderLogo provider={p.provider} size={15} className="shrink-0" />
                          <span className="truncate text-[13.5px] font-medium text-[var(--text-1)]">
                            {o.name}
                          </span>
                        </span>
                      </ModelSelectorItem>
                    ))}
                  </ModelSelectorGroup>
                );
              })}
              <ModelSelectorEmpty>No models match.</ModelSelectorEmpty>
            </ModelSelectorList>
            {supportsEffort && <ModelSelectorEffort />}
          </ModelSelectorContent>
          {/* voice */}
          <Tip label="Use voice mode" hint="Shift+Ctrl+U" align="end">
            <button
              type="button"
              aria-label="Use voice mode"
              onClick={onVoiceToggle}
              className={`press fluid relative grid h-9 w-9 shrink-0 place-items-center rounded-full hover:bg-[var(--panel-hover)] ${
                voiceActive ? "text-[var(--accent)]" : "text-[var(--text-2)] hover:text-[var(--text-1)]"
              }`}
            >
              <Icon name="waveform" size={16} />
              {voiceActive && (
                <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
              )}
            </button>
          </Tip>
          {/* send */}
          <Tip label="Submit" align="end">
            <button
              type="button"
              aria-label="Send"
              onClick={submit}
              disabled={busy || !input.trim()}
              className="press fluid grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--solid)] text-[var(--on-solid)] shadow-[var(--shadow-card)] hover:opacity-85 disabled:opacity-30"
            >
              <Icon name="send" size={15} strokeWidth={2} className="rotate-90" />
            </button>
          </Tip>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,application/pdf,text/*"
          className="hidden"
          onChange={(e) => {
            void addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        </div>
      </div>
      </ModelSelectorRoot>

      <p className="mt-2 text-center text-[10.5px] text-[var(--text-3)]">
        Enter to send, Shift+Enter for a new line
      </p>
    </div>
  );
}

/** Dark hover tooltip, like the reference composer (Submit / Use voice mode). */
function Tip({
  label,
  hint,
  align = "center",
  children,
}: {
  label: string;
  hint?: string;
  align?: "center" | "end";
  children: ReactNode;
}) {
  return (
    <span className="group/tip relative inline-flex shrink-0">
      {children}
      <span
        className={`pointer-events-none absolute bottom-full z-30 mb-2 hidden whitespace-nowrap rounded-lg bg-[var(--solid)] px-2.5 py-1.5 text-left shadow-[var(--shadow-pop)] group-hover/tip:block ${
          align === "end" ? "right-0" : "left-1/2 -translate-x-1/2"
        }`}
      >
        <span className="block text-[12px] font-medium text-[var(--on-solid)]">{label}</span>
        {hint && (
          <span className="block font-mono text-[10.5px] text-[var(--on-solid)] opacity-60">{hint}</span>
        )}
      </span>
    </span>
  );
}

interface ToolsMenuCtx {
  view: ToolsView;
  setView: (v: ToolsView) => void;
  close: () => void;
  webSearch: boolean;
  onToggleSearch: () => void;
  uploadDisabled: boolean;
  onUpload: () => void;
  agents: AgentInfo[];
  agentSlug: string | null;
  onAgent: (slug: string | null) => void;
  voiceCapable: boolean;
  voiceHint: string;
  voices: { id: string; label: string }[];
  voice: string | null;
  voiceLabel: string;
  onVoice: (id: string) => void;
  instructionsOn: boolean;
  instrDraft: string;
  setInstrDraft: (v: string) => void;
  onSaveInstructions: () => void;
  onClearInstructions: () => void;
  starters: { cmd: string; desc: string }[];
  onStarter: (cmd: string) => void;
  templates: PromptTemplate[];
  onInsertTemplate: (body: string) => void;
  onDeleteTemplate: (id: string) => void;
  newTitle: string;
  setNewTitle: (v: string) => void;
  newBody: string;
  setNewBody: (v: string) => void;
  onSaveTemplate: () => void;
}

/** Shared menu body for the + button and the Search pill. */
function ToolsMenuBody({ variant, menu }: { variant: "plus" | "search"; menu: ToolsMenuCtx }) {
  const backLabel = variant === "search" ? "Search options" : "All tools";
  if (menu.view === "instructions") {
    return (
      <span className="block p-1.5">
        <MenuBack label={backLabel} onBack={() => menu.setView("menu")} />
        <p className="px-2 text-[12.5px] font-semibold text-[var(--text-1)]">Custom instructions</p>
        <p className="mt-0.5 px-2 text-[11.5px] leading-snug text-[var(--text-3)]">
          Applied to every run. The model sees them, the thread does not show them.
        </p>
        <textarea
          value={menu.instrDraft}
          onChange={(e) => menu.setInstrDraft(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Example: Be concise. Always show sources."
          className="mt-2 w-full resize-y rounded-lg border border-[var(--hairline)] bg-[var(--bg)] p-2 text-[12.5px] text-[var(--text-1)] outline-none focus:border-[var(--accent-border)]"
        />
        <span className="mt-2 flex justify-end gap-1.5">
          {menu.instructionsOn && (
            <button
              type="button"
              onClick={menu.onClearInstructions}
              className="press rounded-lg px-2.5 py-1.5 text-[11.5px] font-medium text-[var(--danger)] hover:bg-[var(--panel-hover)]"
            >
              Clear
            </button>
          )}
          <button
            type="button"
            onClick={menu.onSaveInstructions}
            className="press rounded-lg bg-[var(--accent)] px-3 py-1.5 text-[11.5px] font-semibold text-[var(--accent-contrast)]"
          >
            Save
          </button>
        </span>
      </span>
    );
  }
  if (menu.view === "prompts") {
    return (
      <span className="block">
        <MenuBack label={backLabel} onBack={() => menu.setView("menu")} />
        <p className="px-2.5 pb-1 text-[12px] font-semibold font-medium text-[var(--text-3)]">
          Starters
        </p>
        {menu.starters.map((c) => (
          <button
            key={c.cmd}
            type="button"
            onClick={() => menu.onStarter(c.cmd)}
            className="press fluid flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-[var(--panel-hover)]"
          >
            <span className="rounded bg-[var(--panel-hover)] px-1.5 py-0.5 font-mono text-[11px] font-medium text-[var(--text-1)]">
              {c.cmd}
            </span>
            <span className="min-w-0 flex-1 truncate text-[11.5px] text-[var(--text-3)]">{c.desc}</span>
          </button>
        ))}
        <p className="px-2.5 pb-1 pt-2 text-[12px] font-semibold font-medium text-[var(--text-3)]">
          Saved
        </p>
        {menu.templates.length === 0 && (
          <p className="px-2.5 pb-1 text-[11.5px] text-[var(--text-3)]">
            Nothing saved yet. Add one below.
          </p>
        )}
        {menu.templates.map((t) => (
          <span
            key={t.id}
            className="group/tpl flex w-full items-center gap-1 rounded-xl px-1.5 py-1 hover:bg-[var(--panel-hover)]"
          >
            <button
              type="button"
              onClick={() => menu.onInsertTemplate(t.body)}
              className="min-w-0 flex-1 rounded-lg px-1 py-1 text-left"
              title={t.body}
            >
              <span className="block truncate text-[12px] font-medium text-[var(--text-1)]">{t.title}</span>
              <span className="block truncate text-[11px] text-[var(--text-3)]">{t.body}</span>
            </button>
            <button
              type="button"
              aria-label={`Delete ${t.title}`}
              onClick={() => menu.onDeleteTemplate(t.id)}
              className="press hidden rounded-md p-1 text-[var(--text-3)] hover:text-[var(--danger)] group-hover/tpl:block"
            >
              <Icon name="x" size={11} />
            </button>
          </span>
        ))}
        <span className="mt-1 block border-t border-[var(--hairline)] p-2">
          <input
            value={menu.newTitle}
            onChange={(e) => menu.setNewTitle(e.target.value)}
            maxLength={128}
            placeholder="New prompt title"
            className="h-8 w-full rounded-lg border border-[var(--hairline)] bg-[var(--bg)] px-2 text-[12px] text-[var(--text-1)] outline-none focus:border-[var(--accent-border)]"
          />
          <textarea
            value={menu.newBody}
            onChange={(e) => menu.setNewBody(e.target.value)}
            rows={2}
            maxLength={8000}
            placeholder="Prompt text to insert"
            className="mt-1.5 w-full resize-y rounded-lg border border-[var(--hairline)] bg-[var(--bg)] p-2 text-[12px] text-[var(--text-1)] outline-none focus:border-[var(--accent-border)]"
          />
          <button
            type="button"
            onClick={menu.onSaveTemplate}
            disabled={!menu.newTitle.trim() || !menu.newBody.trim()}
            className="press mt-1.5 w-full rounded-lg bg-[var(--accent)] py-1.5 text-[11.5px] font-semibold text-[var(--accent-contrast)] disabled:opacity-40"
          >
            Save prompt
          </button>
        </span>
      </span>
    );
  }
  if (menu.view === "voice") {
    return (
      <span className="block p-1.5">
        <MenuBack label={backLabel} onBack={() => menu.setView("menu")} />
        {!menu.voiceCapable || !menu.voices.length ? (
          <p className="px-2.5 py-2 text-[12px] leading-5 text-[var(--text-3)]">{menu.voiceHint}</p>
        ) : (
          menu.voices.map((v) => (
            <button
              key={v.id}
              type="button"
              onClick={() => menu.onVoice(v.id)}
              className="press fluid flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-[var(--panel-hover)]"
            >
              <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text-1)]">{v.label}</span>
              {menu.voice === v.id && <Icon name="check" size={14} className="shrink-0 text-[var(--accent)]" />}
            </button>
          ))
        )}
      </span>
    );
  }
  // Agent mode menu (the pill's own dropdown): 1) Orchestrator,
  // 2) Specialized agents. Rows carry the dark hover tooltip.
  if (variant === "search") {
    return (
      <>
        <p className="px-2.5 pb-1 pt-1.5 text-[12px] font-medium text-[var(--text-3)]">
          Orchestrator
        </p>
        <ModeRow
          icon="bot"
          name="Orchestrator"
          desc="Auto  -  plans, delegates to specialists"
          active={menu.agentSlug === null}
          tipBelow
          onSelect={() => menu.onAgent(null)}
        />
        <p className="px-2.5 pb-1 pt-2.5 text-[12px] font-medium text-[var(--text-3)]">
          Specialized agents
        </p>
        {menu.agents.map((a) => (
          <ModeRow
            key={a.slug}
            icon={agentIcon(a.slug)}
            logo={agentMark(a.slug) ? <AgentLogo slug={a.slug} size={15} /> : undefined}
            name={a.name}
            desc={
              a.slug === "research"
                ? "Web search with cited sources"
                : a.description || `Talk to ${a.name} directly`
            }
            active={menu.agentSlug === a.slug}
            onSelect={() => menu.onAgent(a.slug)}
          />
        ))}
      </>
    );
  }
  return (
    <>
      <ToolsItem
        icon="plus"
        label="Add files or photos"
        hint={menu.uploadDisabled ? "Max reached" : undefined}
        onClick={menu.onUpload}
      />
      <ToolsItem
        icon="waveform"
        label={menu.voiceCapable ? `Voice: ${menu.voiceLabel}` : "Voice"}
        hint={menu.voiceCapable ? undefined : "Unavailable"}
        chevron={menu.voiceCapable}
        onClick={() => {
          if (menu.voiceCapable) menu.setView("voice");
        }}
      />
      <ToolsItem
        icon="sliders"
        label="Custom instructions"
        hint={menu.instructionsOn ? "On" : undefined}
        chevron
        onClick={() => menu.setView("instructions")}
      />
      <ToolsItem
        icon="bookmark"
        label="Prompt library"
        hint={menu.templates.length ? String(menu.templates.length) : undefined}
        chevron
        onClick={() => menu.setView("prompts")}
      />
    </>
  );
}

/** Agent mode row with the dark hover tooltip (title + description). */
function ModeRow({
  icon,
  logo,
  name,
  desc,
  active,
  tipBelow,
  onSelect,
}: {
  icon: Parameters<typeof Icon>[0]["name"];
  logo?: ReactNode;
  name: string;
  desc: string;
  active: boolean;
  tipBelow?: boolean;
  onSelect: () => void;
}) {
  return (
    <span className="group/row relative block">
      <button
        type="button"
        role="menuitemradio"
        aria-checked={active}
        onClick={onSelect}
        className="press fluid flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-[var(--panel-hover)]"
      >
        {logo ?? (
          <Icon name={icon} size={14} className={`shrink-0 ${active ? "text-[var(--accent)]" : "text-[var(--text-2)]"}`} />
        )}
        <span className={`min-w-0 flex-1 truncate text-[13.5px] ${active ? "font-medium text-[var(--text-1)]" : "text-[var(--text-2)]"}`}>
          {name}
        </span>
        {active ? (
          <Icon name="check" size={14} className="shrink-0 text-[var(--accent)]" />
        ) : (
          <span className="h-3.5 w-3.5 shrink-0" />
        )}
      </button>
      <span className={`pointer-events-none absolute left-8 z-30 mb-1 hidden max-w-[220px] rounded-lg bg-[var(--solid)] px-2.5 py-1.5 shadow-[var(--shadow-pop)] group-hover/row:block ${
        tipBelow ? "top-full mt-1" : "bottom-full"
      }`}>
        <span className="block text-[12px] font-medium text-[var(--on-solid)]">{name}</span>
        <span className="block text-[11.5px] leading-snug text-[var(--on-solid)] opacity-60">{desc}</span>
      </span>
    </span>
  );
}

function MenuBack({ label, onBack }: { label: string; onBack: () => void }) {
  return (
    <button
      type="button"
      onClick={onBack}
      className="press fluid mb-1 flex items-center gap-1 rounded-lg px-2 py-1 text-[11.5px] font-medium text-[var(--text-3)] hover:text-[var(--text-1)]"
    >
      <Icon name="chevron" size={11} className="rotate-180" /> {label}
    </button>
  );
}

function ToolsItem({
  icon,
  label,
  hint,
  checked,
  chevron,
  onClick,
}: {
  icon: Parameters<typeof Icon>[0]["name"];
  label: string;
  hint?: string;
  checked?: boolean;
  chevron?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      aria-checked={checked}
      onClick={onClick}
      className="press fluid flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-[var(--panel-hover)]"
    >
      <Icon name={icon} size={14} className="shrink-0 text-[var(--text-2)]" />
      <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text-1)]">{label}</span>
      {hint && (
        <span className="shrink-0 text-[11px] text-[var(--text-3)]">{hint}</span>
      )}
      {checked !== undefined &&
        (checked ? (
          <Icon name="check" size={14} className="shrink-0 text-[var(--accent)]" />
        ) : (
          <span className="h-3.5 w-3.5 shrink-0" />
        ))}
      {chevron && <Icon name="chevron" size={12} className="shrink-0 text-[var(--text-3)]" />}
    </button>
  );
}

