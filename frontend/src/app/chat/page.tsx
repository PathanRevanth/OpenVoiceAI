"use client";

/**
 * Assistant page  -  voice-first chat.
 *
 * Voice: WebSocket to the backend. With a realtime provider key, mic PCM is
 * streamed to a speech-to-speech session; without one, browser
 * SpeechRecognition + orchestrator text turns + SpeechSynthesis provide the
 * same loop. Text: SSE streaming with tool activity and approval cards.
 *
 * Conversations persist backend-side; the sidebar (AppShell) opens them here
 * via the shell context.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { motion } from "motion/react";
import { AuthProvider, useAuthValue } from "@/components/auth-context";
import { AppShell, useShell } from "@/components/app-shell";
import { ChatComposer } from "@/components/chat-composer";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import {
  Plan,
  PlanAction,
  PlanContent,
  PlanDescription,
  PlanFooter,
  PlanHeader,
  PlanTitle,
  PlanTrigger,
} from "@/components/ai-elements/plan";
import {
  PlanStepRow,
  type PlanCardState,
  type PlanPhase,
  type PlanStep,
} from "@/components/ai-elements/plan-todo";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";
import { ArtifactPanel } from "@/components/artifact-panel";
import { parseSegments, type Artifact } from "@/lib/artifacts";
import { notify } from "@/lib/notify";
import { VoiceOrb } from "@/components/voice-orb";
import {
  Approval,
  ChatAttachmentView,
  ChatSource,
  RunEvent,
  TurnUsage,
  backend,
  connectVoice,
  exportConversation,
  streamChat,
  streamResume,
  type ReasoningEffort,
  type ChatAttachmentInput,
} from "@/lib/backend";

interface Turn {
  role: "user" | "assistant" | "system" | "plan";
  text: string;
  streaming?: boolean;
  plan?: PlanCardState;
  sources?: ChatSource[];
  followups?: string[];
  usage?: TurnUsage;
  runId?: string;
  model?: string;
  feedback?: "up" | "down" | null;
  attachments?: ChatAttachmentView[];
  thinking?: string;
  thinkingStreaming?: boolean;
  /** True while a live voice caption is still being updated. */
  live?: boolean;
  createdAt?: string;
}

type Block = (
  | { kind: "turn"; turn: Turn; idx: number }
  | { kind: "activity"; items: { text: string; idx: number }[] }
)[];

interface VoiceState {
  mode: "idle" | "listening" | "thinking" | "speaking" | "fallback";
  info?: string;
}

function AssistantInner() {
  const { user } = useAuthValue();
  const { activeId, setActiveId, refreshConversations } = useShell();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [voice, setVoice] = useState<VoiceState>({ mode: "idle" });
  const [pendingApprovals, setPendingApprovals] = useState<Approval[]>([]);
  const [plan, setPlan] = useState<PlanCardState | null>(null);
  // Composer selections: chat model (pydantic-ai string) + realtime voice id.
  const [model, setModel] = useState<string | null>(null);
  const [effort, setEffort] = useState<ReasoningEffort | null>(null);
  const [selectedVoice, setSelectedVoice] = useState<string | null>(null);
  const [voiceModel, setVoiceModel] = useState<string | null>(null);
  // Artifacts workspace: derived from assistant turns; the panel auto-opens
  // when a new artifact streams in during a run or voice session.
  const [artifactsOpen, setArtifactsOpen] = useState(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  /** Background voice/research runs still streaming an answer. */
  const [backgroundRuns, setBackgroundRuns] = useState(0);

  const artifacts = useMemo<Artifact[]>(
    () =>
      turns.flatMap((turn, i) =>
        turn.role === "assistant"
          ? parseSegments(turn.text, `t${i}`, !turn.streaming).flatMap((s) =>
              s.type === "artifact" ? [s.artifact] : [],
            )
          : [],
      ),
    [turns],
  );
  const artifactCount = useRef(0);

  useEffect(() => {
    const grew = artifacts.length > artifactCount.current;
    artifactCount.current = artifacts.length;
    if (grew && (busy || voice.mode !== "idle" || backgroundRuns > 0)) {
      setSelectedArtifactId(artifacts[artifacts.length - 1].id);
      setArtifactsOpen(true);
    }
  }, [artifacts, busy, voice.mode, backgroundRuns]);

  const openArtifact = useCallback((id: string) => {
    setSelectedArtifactId(id);
    setArtifactsOpen(true);
  }, []);

  // Sidebar Artifacts row: open the canvas on the latest artifact, if any.
  const artifactsRef = useRef(artifacts);
  artifactsRef.current = artifacts;
  useEffect(() => {
    const onOpen = () => {
      const list = artifactsRef.current;
      if (list.length) {
        setSelectedArtifactId(list[list.length - 1].id);
        setArtifactsOpen(true);
      }
    };
    window.addEventListener("vf:open-artifacts", onOpen);
    return () => window.removeEventListener("vf:open-artifacts", onOpen);
  }, []);

  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const mediaRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const playbackCtxRef = useRef<AudioContext | null>(null);
  const playerNodeRef = useRef<AudioWorkletNode | null>(null);
  const turnDoneRef = useRef(false);
  const playerIdleRef = useRef(true);
  /** Transcript index currently rendered as the live caption, per speaker. */
  const liveIdxRef = useRef<{ user: number | null; assistant: number | null }>({ user: null, assistant: null });
  /** Highest finalized transcript index per speaker (cancelled tails are dropped). */
  const finalizedIdxRef = useRef<{ user: number; assistant: number }>({ user: -1, assistant: -1 });
  const processorRef = useRef<AudioWorkletNode | null>(null);
  const recogRef = useRef<{ stop: () => void } | null>(null);
  const voiceModeRef = useRef<"idle" | "listening" | "thinking" | "speaking" | "fallback">("idle");
  const pausedRunRef = useRef<string | null>(null);
  const lastRunIdRef = useRef<string | null>(null);
  const pendingModelRef = useRef<string | null>(null);

  const addTurn = useCallback((turn: Turn) => {
    setTurns((prev) => {
      // Background artifact deltas can arrive while a live voice caption is
      // the newest row. Append to the streaming answer wherever it is, rather
      // than fragmenting the artifact across multiple assistant turns.
      if (turn.role === "assistant" && turn.streaming) {
        for (let i = prev.length - 1; i >= 0; i--) {
          if (prev[i].role === "assistant" && prev[i].streaming) {
            const next = [...prev];
            next[i] = { ...next[i], text: next[i].text + turn.text };
            return next;
          }
        }
      }
      return [...prev, turn];
    });
  }, []);

  /** Update (or open) the live caption turn for a speaker. */
  const upsertLiveTurn = useCallback((role: "user" | "assistant", text: string, index?: number | null) => {
    const idx = typeof index === "number" ? index : null;
    // After a turn is finalized (reply done or barged in), later deltas with the
    // same index are the provider's cancelled tail — they were never heard.
    if (idx !== null && idx <= finalizedIdxRef.current[role]) return;
    const startsNew = idx !== null ? idx !== liveIdxRef.current[role] : liveIdxRef.current[role] === null;
    liveIdxRef.current[role] = idx;
    setTurns((prev) => {
      const next = prev.map((t) => (t.live && t.role !== role ? { ...t, live: false } : t));
      if (startsNew) {
        next.push({ role, text, live: true });
        return next;
      }
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].live && next[i].role === role) {
          next[i] = { ...next[i], text };
          return next;
        }
      }
      next.push({ role, text, live: true });
      return next;
    });
  }, []);

  const finalizeLiveTurns = useCallback(() => {
    const { user, assistant } = liveIdxRef.current;
    if (user !== null) finalizedIdxRef.current.user = Math.max(finalizedIdxRef.current.user, user);
    if (assistant !== null) finalizedIdxRef.current.assistant = Math.max(finalizedIdxRef.current.assistant, assistant);
    liveIdxRef.current = { user: null, assistant: null };
    setTurns((prev) => (prev.some((t) => t.live) ? prev.map((t) => (t.live ? { ...t, live: false } : t)) : prev));
  }, []);

  // --- live plan: official Plan card in the thread + to-do panel at the composer ---
  const onPlanProposed = useCallback((items: PlanStep[]) => {
    if (!items.length) return;
    setPlan({ items, phase: "streaming", approvalId: undefined });
  }, []);

  const onPlanTask = useCallback((taskId: unknown, status: unknown) => {
    if (typeof taskId !== "string" || typeof status !== "string") return;
    const valid = new Set(["pending", "in_progress", "completed", "cancelled", "blocked"]);
    if (!valid.has(status)) return;
    setPlan((prev) => {
      if (!prev) return prev;
      let items = prev.items;
      const known = items.some((it) => it.id === taskId);
      if (!known) {
        // The model omitted ids for its items, so the harness generated its own.
        // Bind each unseen id to the first placeholder item in plan order.
        const bindIdx = items.findIndex(
          (it) => it.id.startsWith("step-") && it.status !== "completed" && it.status !== "cancelled",
        );
        if (bindIdx !== -1) {
          items = items.map((it, i) => (i === bindIdx ? { ...it, id: taskId } : it));
        } else {
          return prev;
        }
      }
      return {
        ...prev,
        items: items.map((it) =>
          it.id === taskId ? { ...it, status: status as PlanStep["status"] } : it,
        ),
      };
    });
  }, []);

  /** Insert or update the Plan card in the message thread. */
  const upsertPlanTurn = useCallback((next: PlanCardState) => {
    setTurns((prev) => {
      const idx = [...prev].reverse().findIndex((t) => t.role === "plan");
      const at = idx === -1 ? -1 : prev.length - 1 - idx;
      if (at === -1) {
        return [...prev, { role: "plan", text: "", plan: next }];
      }
      const merged: PlanCardState = {
        items: next.items.length ? next.items : prev[at].plan?.items ?? [],
        phase: next.phase,
        approvalId: next.approvalId ?? prev[at].plan?.approvalId,
      };
      return [...prev.slice(0, at), { ...prev[at], plan: merged }, ...prev.slice(at + 1)];
    });
  }, []);

  const finalizeTurn = useCallback((text?: string) => {
    setTurns((prev) => {
      // Same as addTurn: a live voice caption may be the newest row, so close
      // the streaming assistant turn wherever it sits. Otherwise its artifact
      // stays "generating" forever and the canvas never switches to preview.
      for (let i = prev.length - 1; i >= 0; i--) {
        if (prev[i].role === "assistant" && prev[i].streaming) {
          const next = [...prev];
          next[i] = { ...next[i], text: text ?? next[i].text, streaming: false };
          return next;
        }
      }
      return prev;
    });
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  // --- conversation loading (sidebar â†” page) ---
  useEffect(() => {
    if (activeId && activeId !== conversationId) {
      backend
        .conversation(activeId)
        .then((conv) => {
          setConversationId(conv.id);
          setTurns(
            conv.messages
              .filter((m) => m.content?.text)
              .map((m) => ({
                role: m.role === "user" ? "user" : m.role === "assistant" ? "assistant" : "system",
                text: m.content.text ?? "",
                sources: m.content.sources ?? undefined,
                followups: m.content.followups ?? undefined,
                model: m.content.model ?? undefined,
                attachments: m.content.attachments ?? undefined,
                thinking: m.content.thinking ?? undefined,
                createdAt: m.created_at,
              })),
          );
        })
        .catch(() => setActiveId(null));
    } else if (activeId === null && conversationId !== null) {
      setConversationId(null);
      setTurns([]);
      setVoice({ mode: "idle" });
      setPlan(null);
      setArtifactsOpen(false);
      setSelectedArtifactId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  // --- daily spend meter + elapsed run timer (declared before the event pump) ---
  const [todaySpend, setTodaySpend] = useState<number | null>(null);
  const [spendLimit, setSpendLimit] = useState<number | null>(null);
  const [demoMode, setDemoMode] = useState(false);
  const refreshBudget = useCallback(() => {
    backend
      .usage(1)
      .then((u) => setTodaySpend(u.cost_usd))
      .catch(() => {});
    backend
      .platform()
      .then((p) => {
        setSpendLimit(p.policies.daily_spend_limit_usd);
        setDemoMode(p.demo_mode && !p.connections?.agents);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    refreshBudget();
  }, [refreshBudget]);

  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const t = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  // --- run-event pump (chat + voice text turns) ---
  const applyRunEvent = useCallback(
    (ev: RunEvent, options?: { background?: boolean }) => {
        const background = options?.background === true;
        switch (ev.type) {
          case "run.started":
            if (ev.data.conversation_id) {
              const id = String(ev.data.conversation_id);
              setConversationId(id);
              setActiveId(id);
            }
            if (ev.data.run_id) lastRunIdRef.current = String(ev.data.run_id);
            if (ev.data.agent && ev.data.agent !== "orchestrator") {
              addTurn({ role: "system", text: `specialist: ${ev.data.agent}` });
            }
            if (background) setBackgroundRuns((n) => n + 1);
            if (!background) {
              setBusy(true);
              setVoice((v) => (v.mode === "idle" ? v : { ...v, mode: "thinking", info: v.info }));
            }
            break;
          case "text.delta":
            addTurn({ role: "assistant", text: String(ev.data.delta ?? ""), streaming: true });
            break;
          case "thinking.delta": {
            const delta = String(ev.data.delta ?? "");
            setTurns((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "assistant" && last.streaming) {
                return [...prev.slice(0, -1), { ...last, thinking: (last.thinking ?? "") + delta, thinkingStreaming: true }];
              }
              return [...prev, { role: "assistant" as const, text: "", streaming: true, thinking: delta, thinkingStreaming: true }];
            });
            break;
          }
          case "thinking.done": {
            const thinking = String(ev.data.thinking ?? "");
            setTurns((prev) => {
              for (let i = prev.length - 1; i >= 0; i--) {
                if (prev[i].role === "assistant") {
                  const next = [...prev];
                  next[i] = { ...next[i], thinking, thinkingStreaming: false };
                  return next;
                }
              }
              return prev;
            });
            break;
          }
          case "text.done": {
            const finalText = String(ev.data.text ?? "");
            finalizeTurn(finalText || undefined);
            const runId = lastRunIdRef.current;
            const modelLabel = pendingModelRef.current;
            const now = new Date().toISOString();
            if (runId || modelLabel) {
              setTurns((prev) => {
                for (let i = prev.length - 1; i >= 0; i--) {
                  // Skip a live voice caption so run metadata lands on the
                  // finalized artifact answer, not the spoken transcript.
                  if (prev[i].role === "assistant" && !prev[i].live) {
                    const next = [...prev];
                    next[i] = {
                      ...next[i],
                      runId: runId ?? next[i].runId,
                      model: modelLabel ?? next[i].model,
                      createdAt: next[i].createdAt ?? now,
                    };
                    return next;
                  }
                }
                return prev;
              });
            }
            break;
          }
          case "sources": {
            const sources = ev.data.sources as ChatSource[] | undefined;
            if (Array.isArray(sources) && sources.length) {
              setTurns((prev) => {
                for (let i = prev.length - 1; i >= 0; i--) {
                  if (prev[i].role === "assistant") {
                    const next = [...prev];
                    next[i] = { ...next[i], sources };
                    return next;
                  }
                }
                return prev;
              });
            }
            break;
          }
          case "followups": {
            const followups = ev.data.followups as string[] | undefined;
            if (Array.isArray(followups) && followups.length) {
              setTurns((prev) => {
                for (let i = prev.length - 1; i >= 0; i--) {
                  if (prev[i].role === "assistant") {
                    const next = [...prev];
                    next[i] = { ...next[i], followups };
                    return next;
                  }
                }
                return prev;
              });
            }
            break;
          }
          case "usage": {
            const u = ev.data as Record<string, unknown>;
            const usage: TurnUsage = {
              input_tokens: Number(u.input_tokens ?? 0),
              output_tokens: Number(u.output_tokens ?? 0),
              total_tokens: Number(u.total_tokens ?? 0),
              cost_usd: Number(u.cost_usd ?? 0),
            };
            setTurns((prev) => {
              for (let i = prev.length - 1; i >= 0; i--) {
                if (prev[i].role === "assistant") {
                  const next = [...prev];
                  next[i] = { ...next[i], usage };
                  return next;
                }
              }
              return prev;
            });
            break;
          }
          case "tool.started":
            // The plan card represents write_plan visually; skip the raw line.
            if (ev.data.tool === "write_plan") break;
            // The model-stream and execution events both announce a call now;
            // collapse consecutive repeats into one line.
            const toolLine = `tool: ${ev.data.tool}`;
            setTurns((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "system" && last.text === toolLine) return prev;
              return [...prev, { role: "system" as const, text: toolLine }];
            });
            // Background runs must not drive the orb: the caller may still be
            // talking, and a missed tool.finished would pin it on "Thinking…".
            if (!background) setVoice((v) => ({ ...v, mode: "thinking" }));
            break;
          case "tool.finished":
            if (!background) {
              setVoice((v) =>
                v.mode === "idle" ? v : { ...v, mode: "listening", info: v.info },
              );
            }
            break;
          case "plan.proposed":
            onPlanProposed(normalizePlanItems(ev.data.items));
            break;
          case "plan.task":
            onPlanTask(ev.data.task_id, ev.data.status);
            break;
          case "approval.requested": {
            // SSE payloads carry `approval_id`; normalize it for the Approval shape.
            const raw = (ev.data.approvals ?? []) as (Approval & { approval_id?: string })[];
            const approvals = raw.map((a) => ({ ...a, id: a.id ?? a.approval_id ?? "" }));
            const planApproval = approvals.find((a) => a.tool_name === "write_plan");
            const others = approvals.filter((a) => a.tool_name !== "write_plan");
            if (planApproval) {
              setPlan({
                items: normalizePlanItems(
                  (planApproval.args as { items?: unknown[] } | undefined)?.items,
                ),
                phase: "awaiting",
                approvalId: planApproval.id,
              });
              addTurn({ role: "system", text: "awaiting approval: execution plan" });
            }
            if (others.length > 0) {
              setPendingApprovals((prev) => [...prev, ...others]);
              addTurn({
                role: "system",
                text: `awaiting approval: ${others.map((a) => a.tool_name).join(", ") || "tool call"}`,
              });
            }
            break;
          }
          case "run.finished":
            pausedRunRef.current =
              ev.data.status === "awaiting_approval" && ev.data.run_id
                ? String(ev.data.run_id)
                : null;
            if (background) setBackgroundRuns((n) => Math.max(0, n - 1));
            if (!background) {
              setBusy(false);
              setVoice((v) => (v.mode === "idle" ? v : { ...v, mode: "listening", info: v.info }));
            } else {
              // A background run that flips the orb to "thinking" must hand it
              // back; never clobber an in-progress spoken reply.
              setVoice((v) => (v.mode === "thinking" ? { ...v, mode: "listening" } : v));
            }
            if (ev.data.status === "completed") {
              // The orchestrator concluded the task; close out any steps it
              // finished without a final status update.
              setPlan((prev) =>
                prev
                  ? {
                      ...prev,
                      phase: "completed",
                      items: prev.items.map((it) =>
                        it.status === "completed" || it.status === "cancelled"
                          ? it
                          : { ...it, status: "completed" as PlanStep["status"] },
                      ),
                    }
                  : prev,
              );
            }
            break;
          case "run.failed":
            addTurn({ role: "system", text: `failed: ${ev.data.error}` });
            if (background) {
              setBackgroundRuns((n) => Math.max(0, n - 1));
              setVoice((v) => (v.mode === "thinking" ? { ...v, mode: "listening" } : v));
            }
            break;
        }
    },
    [addTurn, finalizeTurn, setActiveId, onPlanProposed, onPlanTask],
  );

  const handleEvents = useCallback(
    async (events: AsyncGenerator<RunEvent>) => {
      for await (const ev of events) applyRunEvent(ev);
    },
    [applyRunEvent],
  );

  /** Mirror the live plan into the thread as an in-message Plan card. */
  useEffect(() => {
    if (plan) upsertPlanTurn(plan);
  }, [plan, upsertPlanTurn]);

  const sendText = useCallback(
    async (
      message: string,
      attachments?: ChatAttachmentInput[],
      agent?: string | null,
      modelOverride?: string | null,
    ) => {
      if (!message.trim() || busy) return;
      setBusy(true);
      setVoice((v) => ({ ...v, mode: "thinking" }));
      pendingModelRef.current = modelOverride ?? model ?? null;
      addTurn({
        role: "user",
        text: message,
        createdAt: new Date().toISOString(),
        attachments: (attachments ?? []).map((a) => ({
          name: a.name,
          media_type: a.media_type,
          ...(a.previewUrl ? { preview: a.previewUrl } : {}),
        })),
      });
      try {
        await handleEvents(
          streamChat(message, conversationId, attachments, agent ?? null, modelOverride ?? null, effort),
        );
        refreshConversations();
        refreshBudget();
      } catch (err) {
        addTurn({ role: "system", text: `failed: ${err instanceof Error ? err.message : err}` });
      } finally {
        setBusy(false);
        setVoice((v) => ({ ...v, mode: "idle" }));
      }
    },
    [busy, conversationId, addTurn, handleEvents, refreshConversations, refreshBudget, effort, model],
  );

  const stopRun = useCallback(async () => {
    const runId = pausedRunRef.current ?? lastRunIdRef.current;
    if (!runId) return;
    try {
      await backend.cancelRun(runId);
    } catch {
      /* run may have just finished */
    }
  }, []);

  const copyMessage = useCallback((text: string) => {
    navigator.clipboard?.writeText(text).then(() => notify("Copied to clipboard.")).catch(() => {});
  }, []);

  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  // ? opens the shortcut reference anywhere outside a text field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
      if (!typing && e.key === "?") setShortcutsOpen(true);
      if (e.key === "Escape") setShortcutsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const startEdit = useCallback((idx: number, text: string) => {
    setEditingIdx(idx);
    setEditDraft(text);
  }, []);

  const saveEdit = useCallback(async () => {
    if (editingIdx === null || busy) return;
    const text = editDraft.trim();
    if (!text) return;
    setTurns((prev) => prev.slice(0, editingIdx));
    setEditingIdx(null);
    setEditDraft("");
    await sendText(text);
  }, [editingIdx, editDraft, busy, sendText]);

  const rateMessage = useCallback(async (idx: number, rating: "up" | "down") => {
    const turn = turns[idx];
    const next = turn.feedback === rating ? null : rating;
    setTurns((prev) => prev.map((t, i) => (i === idx ? { ...t, feedback: next } : t)));
    if (next && turn.runId) {
      try {
        await backend.submitFeedback(turn.runId, next);
      } catch {
        /* feedback is best-effort; the local state already reflects the vote */
      }
    }
  }, [turns]);

  const regenerate = useCallback(async () => {
    if (busy) return;
    // Find the last user turn and remove everything after it, then re-send.
    const lastUser = [...turns].reverse().find((t) => t.role === "user");
    if (!lastUser) return;
    const idx = turns.lastIndexOf(lastUser);
    setTurns(turns.slice(0, idx));
    await sendText(lastUser.text);
  }, [turns, busy, sendText]);

  const speak = useCallback((text: string) => {
    if (typeof speechSynthesis === "undefined") return;
    const utter = new SpeechSynthesisUtterance(text.slice(0, 600));
    utter.rate = 1.05;
    speechSynthesis.speak(utter);
  }, []);

  const decide = useCallback(
    async (approval: Approval, approved: boolean) => {
      try {
        await backend.decideApproval(approval.id, approved);
        setPendingApprovals((prev) => prev.filter((a) => a.id !== approval.id));
        addTurn({ role: "system", text: `${approved ? "approved" : "denied"}: ${approval.tool_name}` });
        const runId = pausedRunRef.current;
        if (runId) {
          setBusy(true);
          try {
            await handleEvents(streamResume(runId, model));
          } finally {
            setBusy(false);
            pausedRunRef.current = null;
          }
        }
      } catch (err) {
        addTurn({ role: "system", text: `failed: ${err instanceof Error ? err.message : err}` });
      }
    },
    [addTurn, handleEvents, model],
  );

  /** Approve/decline the orchestrator's plan from the composer-attached panel. */
  const decidePlan = useCallback(
    async (approved: boolean) => {
      const approvalId = plan?.approvalId;
      if (!approvalId) return;
      setPlan((prev) => (prev ? { ...prev, phase: approved ? "approved" : "declined" } : prev));
      try {
        await backend.decideApproval(approvalId, approved);
        addTurn({
          role: "system",
          text: approved ? "approved: execution plan" : "declined: execution plan",
        });
        const runId = pausedRunRef.current;
        if (runId) {
          setBusy(true);
          try {
            await handleEvents(streamResume(runId, model));
          } finally {
            setBusy(false);
            pausedRunRef.current = null;
          }
        }
      } catch (err) {
        addTurn({ role: "system", text: `failed: ${err instanceof Error ? err.message : err}` });
      }
    },
    [plan, addTurn, handleEvents, model],
  );

  // --- voice: WebSocket mic streaming + browser fallbacks ---
  const stopVoice = useCallback(() => {
    processorRef.current?.port.close();
    processorRef.current?.disconnect();
    processorRef.current = null;
    mediaRef.current?.getTracks().forEach((t) => t.stop());
    mediaRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    playerNodeRef.current?.port.postMessage("clear");
    playerNodeRef.current?.disconnect();
    playerNodeRef.current = null;
    playbackCtxRef.current?.close().catch(() => {});
    playbackCtxRef.current = null;
    turnDoneRef.current = false;
    playerIdleRef.current = true;
    liveIdxRef.current = { user: null, assistant: null };
    finalizedIdxRef.current = { user: -1, assistant: -1 };
    recogRef.current?.stop();
    recogRef.current = null;
    try {
      wsRef.current?.send(JSON.stringify({ type: "stop" }));
    } catch {
      /* socket already closed */
    }
    voiceModeRef.current = "idle";
    finalizeLiveTurns();
    setVoice({ mode: "idle" });
  }, [finalizeLiveTurns]);

  const startVoice = useCallback(async () => {
    try {
      const ws = await connectVoice();
      wsRef.current = ws;
      let outputRate = 24000;
      const startWaiters: Array<(frame: { type: string; data: Record<string, unknown> }) => void> = [];
      ws.onmessage = (msg) => {
        const frame = JSON.parse(msg.data as string) as {
          type: string;
          data: Record<string, unknown>;
        };
        if (
          frame.type.startsWith("run.") ||
          frame.type.startsWith("text.") ||
          frame.type.startsWith("tool.") ||
          frame.type.startsWith("plan.") ||
          frame.type === "thinking.delta" ||
          frame.type === "thinking.done" ||
          frame.type === "sources" ||
          frame.type === "followups" ||
          frame.type === "usage" ||
          frame.type === "approval.requested"
        ) {
          applyRunEvent(frame as RunEvent, { background: true });
          return;
        }
        if (frame.type === "conversation" && frame.data.id) {
          const id = String(frame.data.id);
          setConversationId(id);
          setActiveId(id);
          return;
        }
        if (frame.type === "state") {
          const sessionReady =
            Boolean(frame.data.session_id) ||
            frame.data.mode === "browser-fallback" ||
            Boolean(frame.data.error);
          if (sessionReady) startWaiters.splice(0).forEach((resolve) => resolve(frame));
          const mode = frame.data.mode as string | undefined;
          if (mode === "realtime") {
            voiceModeRef.current = "listening";
            const modelName = String(frame.data.model ?? "Gemini Live");
            setVoice({ mode: "listening", info: `Gemini Live · ${modelName}` });
            if (typeof frame.data.output_rate === "number") outputRate = frame.data.output_rate as number;
          } else if (mode === "browser-fallback") {
            voiceModeRef.current = "fallback";
            setVoice({ mode: "fallback", info: String(frame.data.info ?? "Browser speech mode") });
          } else if (mode === "idle") {
            voiceModeRef.current = "idle";
            setVoice({ mode: "idle" });
          }
          if (frame.data.error) {
            addTurn({ role: "system", text: `voice: ${String(frame.data.error)}` });
          }
        } else if (frame.type === "transcript") {
          const speaker = frame.data.speaker;
          const text = String(frame.data.text ?? "");
          const isFinal = frame.data.final === true;
          if (text) {
            const role = speaker === "user" ? "user" : "assistant";
            upsertLiveTurn(role, text, typeof frame.data.index === "number" ? frame.data.index : null);
            if (isFinal) finalizeLiveTurns();
            if (speaker === "user") {
              turnDoneRef.current = false;
              voiceModeRef.current = "listening";
              setVoice((v) => ({ ...v, mode: "listening", info: v.info }));
            } else if (isFinal && voiceModeRef.current === "fallback") {
              speak(text);
            }
          }
        } else if (frame.type === "speech") {
          const player = playerNodeRef.current;
          if (player) {
            playerIdleRef.current = false;
            player.port.postMessage(pcm16Base64ToFloat32(String(frame.data)));
            if (voiceModeRef.current !== "speaking") {
              voiceModeRef.current = "speaking";
              setVoice((v) => ({ ...v, mode: "speaking", info: v.info }));
            }
          }
        } else if (frame.type === "interrupted") {
          // Barge-in: drop everything still queued for playback this instant.
          playerNodeRef.current?.port.postMessage("clear");
          playerIdleRef.current = true;
          turnDoneRef.current = false;
          finalizeLiveTurns();
          voiceModeRef.current = "listening";
          setVoice((v) => ({ ...v, mode: "listening", info: v.info }));
        } else if (frame.type === "turn") {
          const state = String(frame.data.state ?? "");
          if (state === "speaking") {
            turnDoneRef.current = false;
            voiceModeRef.current = "speaking";
            setVoice((v) => ({ ...v, mode: "speaking", info: v.info }));
          } else if (state === "done") {
            turnDoneRef.current = true;
            finalizeLiveTurns();
            refreshConversations();
            if (playerIdleRef.current) {
              turnDoneRef.current = false;
              voiceModeRef.current = "listening";
              setVoice((v) => ({ ...v, mode: "listening", info: v.info }));
            }
          } else if (state === "listening") {
            turnDoneRef.current = false;
            playerIdleRef.current = true;
            finalizeLiveTurns();
            voiceModeRef.current = "listening";
            setVoice((v) => ({ ...v, mode: "listening", info: v.info }));
          }
        }
      };

      const startData: Record<string, string> = {};
      if (voiceModel) startData.model = voiceModel;
      if (selectedVoice) startData.voice = selectedVoice;
      if (model) startData.chat_model = model;
      if (conversationId) startData.conversation_id = conversationId;
      ws.send(JSON.stringify({ type: "start", data: startData }));

      const started = await new Promise<{ type: string; data: Record<string, unknown> }>((resolve, reject) => {
        const timer = window.setTimeout(() => reject(new Error("voice session timed out")), 12_000);
        startWaiters.push((frame) => {
          window.clearTimeout(timer);
          resolve(frame);
        });
      });

      const mode = started.data.mode as string | undefined;
      if (started.data.error && mode !== "realtime") {
        throw new Error(String(started.data.error));
      }
      if (mode === "realtime") {
        const inputRate = typeof started.data.input_rate === "number" ? (started.data.input_rate as number) : 16000;
        outputRate = typeof started.data.output_rate === "number" ? (started.data.output_rate as number) : 24000;

        // Playback first: a gapless AudioWorklet queue, created while the click
        // is still the active user gesture. "clear" is the barge-in flush.
        const playbackCtx = new AudioContext({ sampleRate: outputRate, latencyHint: "interactive" });
        playbackCtxRef.current = playbackCtx;
        await playbackCtx.resume();
        await playbackCtx.audioWorklet.addModule("/pcm-player-processor.js");
        const player = new AudioWorkletNode(playbackCtx, "pcm-player-processor");
        player.port.onmessage = (event: MessageEvent) => {
          if (event.data === "idle") {
            playerIdleRef.current = true;
            if (turnDoneRef.current) {
              turnDoneRef.current = false;
              voiceModeRef.current = "listening";
              setVoice((v) => ({ ...v, mode: "listening", info: v.info }));
            }
          }
        };
        player.connect(playbackCtx.destination);
        playerNodeRef.current = player;
        // Transcript indexes restart with each provider session.
        liveIdxRef.current = { user: null, assistant: null };
        finalizedIdxRef.current = { user: -1, assistant: -1 };

        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        });
        mediaRef.current = stream;
        // Capture natively at 16 kHz so every worklet frame is exactly one
        // 8 ms Gemini frame — no per-chunk resampling and 3x fewer WS messages.
        let audioCtx: AudioContext;
        try {
          audioCtx = new AudioContext({ sampleRate: inputRate, latencyHint: "interactive" });
        } catch {
          audioCtx = new AudioContext({ latencyHint: "interactive" });
        }
        audioCtxRef.current = audioCtx;
        await audioCtx.resume();
        await audioCtx.audioWorklet.addModule("/pcm-worklet.js");
        const worklet = new AudioWorkletNode(audioCtx, "pcm-capture");
        processorRef.current = worklet;
        const fromRate = audioCtx.sampleRate;
        worklet.port.onmessage = (e: MessageEvent) => {
          if (wsRef.current?.readyState === WebSocket.OPEN && e.data instanceof Float32Array) {
            const pcm = resampleToPcm16(e.data, fromRate, inputRate);
            wsRef.current.send(
              JSON.stringify({ type: "pcm", data: arrayBufferToBase64(pcm.buffer as ArrayBuffer) }),
            );
          }
        };
        audioCtx.createMediaStreamSource(stream).connect(worklet);
        voiceModeRef.current = "listening";
        setVoice({ mode: "listening", info: "Gemini Live speech-to-speech" });
        return;
      }

      const SR =
        (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike }).SpeechRecognition ??
        (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionLike })
          .webkitSpeechRecognition;
      if (SR) {
        const recog = new SR();
        recog.lang = "en-US";
        recog.continuous = true;
        recog.interimResults = false;
        recog.onresult = (event: SpeechRecognitionResultLike) => {
          const last = event.results.length - 1;
          const text = last >= 0 ? event.results[last]?.[0]?.transcript : undefined;
          if (text && wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: "text", data: text }));
          }
        };
        recog.start();
        recogRef.current = recog;
      }
      voiceModeRef.current = "fallback";
      setVoice({ mode: "fallback", info: String(started.data.info ?? "Browser speech mode") });
    } catch (err) {
      addTurn({ role: "system", text: `voice unavailable: ${err instanceof Error ? err.message : err}` });
      stopVoice();
    }
  }, [addTurn, speak, stopVoice, refreshConversations, upsertLiveTurn, finalizeLiveTurns, selectedVoice, voiceModel, model, conversationId, applyRunEvent, setActiveId]);

  const role = user?.role ?? "viewer";
  const isEmpty = turns.length === 0;

  // Collapse consecutive tool/specialist system lines into activity blocks.
  const blocks = useMemo<Block>(() => {
    const out: Block = [];
    let pending: { text: string; idx: number }[] = [];
    const flush = () => {
      if (pending.length) {
        out.push({ kind: "activity", items: pending });
        pending = [];
      }
    };
    turns.forEach((turn, idx) => {
      const groupable =
        turn.role === "system" &&
        (turn.text.startsWith("tool:") || turn.text.startsWith("specialist:"));
      if (groupable) {
        pending.push({ text: turn.text, idx });
      } else {
        flush();
        out.push({ kind: "turn", turn, idx });
      }
    });
    flush();
    return out;
  }, [turns]);

  const convoTotals = useMemo(() => {
    let answers = 0;
    let tokens = 0;
    let cost = 0;
    for (const t of turns) {
      if (t.role === "assistant" && t.usage) {
        answers += 1;
        tokens += t.usage.total_tokens;
        cost += t.usage.cost_usd;
      }
    }
    return { answers, tokens, cost };
  }, [turns]);

  return (
    <div className="flex h-[calc(100dvh-3.5rem)]">
    <div className="flex min-w-0 flex-1 flex-col">
      {/* ----------------------------------------------- thread / empty state */}
      <div className="relative min-h-0 flex-1">
        <div className="absolute right-6 top-3 z-[1] flex items-center gap-2">
          {artifacts.length > 0 && (
            <button
              onClick={() => setArtifactsOpen((open) => !open)}
              title="Artifacts"
              className={`press fluid flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium ${
                artifactsOpen
                  ? "border-[var(--accent-border)] bg-[var(--accent-dim)] text-[var(--accent)]"
                  : "border-[var(--hairline)] bg-[var(--bg-raised)] text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
              }`}
            >
              <Icon name="artifact" size={12} /> Artifacts
              <span className="tabular rounded-md bg-[var(--panel-hover)] px-1 text-[10px] text-[var(--text-2)]">
                {artifacts.length}
              </span>
            </button>
          )}
          {conversationId && (
            <button
              onClick={() => window.print()}
              title="Print conversation"
              className="press fluid flex items-center gap-1.5 rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
            >
              <Icon name="print" size={12} /> Print
            </button>
          )}
          {conversationId && (
            <button
              onClick={() => exportConversation(conversationId).catch(() => notify("Export failed."))}
              title="Export conversation"
              className="press fluid flex items-center gap-1.5 rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
            >
              <Icon name="scroll" size={12} /> Export
            </button>
          )}
          <button
            onClick={() => setShortcutsOpen(true)}
            title="Keyboard shortcuts (?)"
            className="press fluid grid h-[30px] w-[30px] place-items-center rounded-lg border border-[var(--hairline)] bg-[var(--bg-raised)] font-mono text-[12px] text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
          >
            ?
          </button>
        </div>
        <div ref={scrollRef} id="assistant-thread" className="h-full overflow-y-auto">
        {isEmpty ? (
          <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6 pb-24">
            <VoiceOrb
              mode={voice.mode}
              size={96}
              onClick={() => (voice.mode === "idle" ? void startVoice() : stopVoice())}
            />
            <h2 className="mt-5 text-center font-serif text-[34px] leading-tight tracking-tight text-[var(--text-1)]">
              {voice.mode === "idle"
                ? "What do you want to know?"
                : voice.mode === "speaking"
                  ? "Go ahead — interrupt me anytime"
                  : voice.mode === "thinking"
                    ? "Thinking…"
                    : "Listening — speak like you would to a colleague"}
            </h2>
            {demoMode && (
              <a
                href="/connections"
                className="press mt-6 flex items-center gap-2.5 rounded-2xl border border-[var(--accent-border)] bg-[var(--accent-dim)] px-4 py-3 text-left"
              >
                <Icon name="plug" size={15} className="shrink-0 text-[var(--accent)]" />
                <span>
                  <span className="block text-[13px] font-medium text-[var(--text-1)]">
                    Connect your AI keys to go live
                  </span>
                  <span className="block text-[12px] text-[var(--text-2)]">
                    Demo answers for now - add a provider key under LLM Connections.
                  </span>
                </span>
              </a>
            )}
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-5 px-6 py-8">
            {voice.mode !== "idle" && (
              <div className="flex justify-center pb-2">
                <VoiceOrb mode={voice.mode} size={64} onClick={stopVoice} />
              </div>
            )}
            {blocks.map((block) =>
              block.kind === "activity" ? (
                <ActivityBlock key={`act-${block.items[0].idx}`} items={block.items} />
              ) : block.turn.role === "system" ? (
                <div key={block.idx} className="flex justify-center py-1">
                  <span className="flex items-center gap-1.5 rounded-full bg-[var(--panel-hover)] px-3 py-1 text-[12px] text-[var(--text-3)]">
                    {block.turn.text.startsWith("awaiting") && <Icon name="shield" size={11} />}
                    {block.turn.text}
                  </span>
                </div>
              ) : block.turn.role === "plan" && block.turn.plan ? (
                <motion.div
                  key={block.idx}
                  initial={{ opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ type: "spring", stiffness: 320, damping: 28 }}
                  className="my-1 max-w-[560px]"
                >
                  <PlanCardMessage
                    plan={block.turn.plan}
                    disabled={busy}
                    onDecide={(approved) => void decidePlan(approved)}
                  />
                </motion.div>
              ) : (
                <MessageBlock
                  key={block.idx}
                  idx={block.idx}
                  turn={block.turn}
                  busy={busy}
                  editing={editingIdx === block.idx}
                  editDraft={editDraft}
                  onEditDraft={setEditDraft}
                  onStartEdit={startEdit}
                  onCancelEdit={() => { setEditingIdx(null); setEditDraft(""); }}
                  onSaveEdit={() => void saveEdit()}
                  onCopy={copyMessage}
                  onSpeak={speak}
                  onRate={rateMessage}
                  onRetry={() => void regenerate()}
                  onFollowup={(q) => void sendText(q)}
                  onOpenArtifact={openArtifact}
                />
              ),
            )}
            {convoTotals.answers > 0 && (
              <p className="pt-2 text-center font-mono text-[10.5px] tabular text-[var(--text-3)]">
                {convoTotals.answers} {convoTotals.answers === 1 ? "answer" : "answers"} -{" "}
                {convoTotals.tokens.toLocaleString()} tokens
                {convoTotals.cost > 0 ? ` - $${convoTotals.cost.toFixed(4)}` : ""} this conversation
              </p>
            )}
          </div>
        )}
      </div>
      </div>

      {/* ----------------------------------------------- approvals */}
      {pendingApprovals.length > 0 && role !== "viewer" && (
        <div className="mx-auto w-full max-w-3xl px-6 pb-3">
          <div className="rounded-2xl border border-[rgba(230,180,85,0.25)] bg-[var(--warn-dim)] p-1.5">
            <p className="flex items-center gap-1.5 px-2.5 pb-1 pt-1.5 text-[12px] font-semibold font-medium text-[var(--warn)]">
              <Icon name="shield" size={11} /> Human approval required
            </p>
            {pendingApprovals.map((a) => (
              <div
                key={a.id}
                className="fluid flex items-center gap-3 rounded-xl bg-[var(--bg-raised)]/90 px-3.5 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-[12px] font-medium">{a.tool_name}</p>
                  <p className="truncate font-mono text-[10.5px] text-[var(--text-3)]">
                    {JSON.stringify(a.args).slice(0, 110)}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1.5">
                  <button
                    onClick={() => decide(a, true)}
                    className="press fluid flex items-center gap-1 rounded-lg bg-[var(--accent)] px-2.5 py-1.5 text-[11.5px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)]"
                  >
                    <Icon name="check" size={12} strokeWidth={2} /> Approve
                  </button>
                  <button
                    onClick={() => decide(a, false)}
                    className="press fluid flex items-center gap-1 rounded-lg border border-[var(--hairline-strong)] px-2.5 py-1.5 text-[11.5px] font-semibold text-[var(--danger)] hover:border-[var(--danger)]"
                  >
                    <Icon name="x" size={12} strokeWidth={2} /> Deny
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ----------------------------------------------- composer */}
      <div className="mx-auto w-full max-w-3xl px-6">
        {busy && (
          <div className="mb-2 flex justify-center">
            <button
              onClick={() => void stopRun()}
              className="press fluid flex items-center gap-2 rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-4 py-1.5 text-[11.5px] font-medium text-[var(--text-2)] hover:border-[var(--danger)] hover:text-[var(--danger)]"
            >
              <span className="h-2 w-2 rounded-sm bg-current" /> Stop generating
              <span className="font-mono text-[10.5px] tabular text-[var(--text-3)]">{elapsed}s</span>
            </button>
          </div>
        )}
        {!busy && turns.length > 0 && turns[turns.length - 1]?.role === "assistant" && (
          <div className="mb-2 flex justify-center">
            <button
              onClick={() => void regenerate()}
              className="press fluid flex items-center gap-1.5 rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-1.5 text-[11px] font-medium text-[var(--text-3)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
            >
              <Icon name="refresh" size={11} /> Regenerate
            </button>
          </div>
        )}
        {todaySpend !== null && spendLimit !== null && spendLimit > 0 && (
          <p className="mb-1.5 flex items-center justify-center gap-1.5 font-mono text-[10.5px] tabular text-[var(--text-3)]">
            <Icon name="coins" size={11} />
            Today ${todaySpend.toFixed(4)} of ${spendLimit.toFixed(2)}
            {todaySpend >= spendLimit * 0.8 ? " - near the daily limit" : ""}
          </p>
        )}
      </div>
      <ChatComposer
        busy={busy}
        voiceMode={voice.mode}
        onSend={(message, attachments, agent) =>
          void sendText(message, attachments, agent, model)
        }
        onVoiceToggle={() => (voice.mode === "idle" ? void startVoice() : stopVoice())}
        plan={plan}
        model={model}
        onModelChange={setModel}
        effort={effort}
        onEffortChange={setEffort}
        voice={selectedVoice}
        onVoiceChange={setSelectedVoice}
        voiceModel={voiceModel}
        onVoiceModelChange={setVoiceModel}
        draftKey={conversationId}
      />
    </div>
    {artifactsOpen && artifacts.length > 0 && (
      <ArtifactPanel
        artifacts={artifacts}
        activeId={selectedArtifactId}
        onSelect={setSelectedArtifactId}
        onClose={() => setArtifactsOpen(false)}
      />
    )}
    {shortcutsOpen && <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />}
    </div>
  );
}

/** Keyboard shortcut reference (? to open, Esc to close). */
function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ["Enter", "Send message"],
    ["Shift + Enter", "New line"],
    ["?", "This reference"],
    ["Esc", "Close dialogs"],
    ["/", "Slash commands"],
    ["Ctrl + K", "Command palette"],
    ["Ctrl + Shift + O", "New conversation"],
    ["Shift + Ctrl + U", "Voice mode"],
  ];
  return (
    <>
      <button aria-label="Close shortcuts" className="fixed inset-0 z-50 cursor-default bg-[var(--scrim)]" onClick={onClose} />
      <div role="dialog" aria-label="Keyboard shortcuts" className="fixed left-1/2 top-1/2 z-50 w-[min(380px,90vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-4 shadow-[var(--shadow-pop)]">
        <p className="px-1 pb-2 text-[13px] font-semibold text-[var(--text-1)]">Keyboard shortcuts</p>
        {rows.map(([keys, what]) => (
          <div key={keys} className="flex items-center justify-between rounded-lg px-2 py-1.5 hover:bg-[var(--panel-hover)]">
            <span className="text-[12.5px] text-[var(--text-2)]">{what}</span>
            <span className="rounded-md bg-[var(--panel-hover)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-1)]">{keys}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// --- helpers ---

/** Collapsed agent-activity feed: tool calls grouped out of the thread. */
function ActivityBlock({ items }: { items: { text: string; idx: number }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex justify-center py-0.5">
      <span className="max-w-full rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)]">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="press fluid flex items-center gap-1.5 px-3 py-1 text-[11.5px] text-[var(--text-3)] hover:text-[var(--text-1)]"
        >
          <Icon name="timer" size={11} />
          Agent activity - {items.length} {items.length === 1 ? "step" : "steps"}
          <Icon name="chevron" size={11} className={`fluid transition-transform ${open ? "rotate-90" : "-rotate-90"}`} />
        </button>
        {open && (
          <span className="block max-w-[420px] px-3 pb-2">
            {items.map((it) => (
              <span key={it.idx} className="block truncate py-0.5 font-mono text-[10.5px] text-[var(--text-3)]">
                {it.text}
              </span>
            ))}
          </span>
        )}
      </span>
    </div>
  );
}

/** Collapsible reasoning trace, shown only when the model streamed thinking. */
function ThinkingBlock({ thinking, streaming }: { thinking: string; streaming?: boolean }) {
  const [open, setOpen] = useState(false);
  const expanded = open || !!streaming;
  return (
    <div className="mb-1.5 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)]">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="press fluid flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-[12px] text-[var(--text-3)] hover:text-[var(--text-1)]"
      >
        <Icon name={streaming ? "timer" : "spark"} size={12} className={streaming ? "animate-pulse text-[var(--accent)]" : ""} />
        {streaming ? "Thinking…" : "Thought process"}
        <Icon name="chevron" size={11} className={`fluid ml-auto transition-transform ${expanded ? "rotate-90" : "-rotate-90"}`} />
      </button>
      {expanded && (
        <p className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words px-3 pb-2.5 font-mono text-[11.5px] leading-relaxed text-[var(--text-2)]">
          {thinking}
        </p>
      )}
    </div>
  );
}

/** One user or assistant message with its toolbar, sources, and follow-ups. */
function MessageBlock({
  idx,
  turn,
  busy,
  editing,
  editDraft,
  onEditDraft,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onCopy,
  onSpeak,
  onRate,
  onRetry,
  onFollowup,
  onOpenArtifact,
}: {
  idx: number;
  turn: Turn;
  busy: boolean;
  editing: boolean;
  editDraft: string;
  onEditDraft: (v: string) => void;
  onStartEdit: (idx: number, text: string) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onCopy: (text: string) => void;
  onSpeak: (text: string) => void;
  onRate: (idx: number, rating: "up" | "down") => void;
  onRetry: () => void;
  onFollowup: (q: string) => void;
  onOpenArtifact: (id: string) => void;
}) {
  const [zoom, setZoom] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    onCopy(turn.text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [onCopy, turn.text]);
  return (
    <div className={`group flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}>
      {zoom && (
        <>
          <button aria-label="Close preview" className="fixed inset-0 z-50 cursor-zoom-out bg-[var(--scrim)]" onClick={() => setZoom(null)} />
          {/* eslint-disable-next-line @next/next/no-img-element -- data URL preview */}
          <img src={zoom} alt="Attachment preview" className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] max-w-[90vw] -translate-x-1/2 -translate-y-1/2 rounded-2xl shadow-[var(--shadow-pop)]" />
        </>
      )}
      <div
        className={`relative max-w-[82%] text-[13.5px] leading-relaxed ${
          turn.role === "user"
            ? "rounded-2xl rounded-br-md bg-[var(--panel-hover)] px-4 py-2.5 text-[var(--text-1)]"
            : `w-full pt-0.5 text-[var(--text-1)] ${turn.streaming ? "caret" : ""}`
        }`}
      >
        {turn.role === "assistant" && turn.thinking ? (
          <ThinkingBlock thinking={turn.thinking} streaming={turn.thinkingStreaming} />
        ) : null}
        {turn.role === "assistant" ? (
          <div className="[&_code]:rounded [&_code]:bg-[var(--panel-hover)] [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-[var(--hairline)] [&_pre]:bg-[var(--bg-raised)] [&_pre]:p-3 [&_strong]:font-semibold">
            <AssistantContent
              text={turn.text}
              keyPrefix={`t${idx}`}
              final={!turn.streaming}
              onOpen={onOpenArtifact}
            />
          </div>
        ) : editing ? (
          <div>
            <textarea
              value={editDraft}
              onChange={(e) => onEditDraft(e.target.value)}
              rows={3}
              className="w-full min-w-[220px] resize-y rounded-lg border border-[var(--hairline-strong)] bg-[var(--bg-raised)] p-2 text-[13px] outline-none focus:border-[var(--accent-border)]"
            />
            <div className="mt-1.5 flex justify-end gap-1.5">
              <button
                onClick={onCancelEdit}
                className="press rounded-lg px-2.5 py-1 text-[11.5px] font-medium text-[var(--text-3)] hover:text-[var(--text-1)]"
              >
                Cancel
              </button>
              <button
                onClick={onSaveEdit}
                disabled={busy || !editDraft.trim()}
                className="press rounded-lg bg-[var(--accent)] px-2.5 py-1 text-[11.5px] font-semibold text-[var(--accent-contrast)] disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </div>
        ) : (
          <>
            {turn.role === "user" && turn.attachments && turn.attachments.length > 0 && (
              <span className="mb-1.5 flex flex-wrap justify-end gap-1.5">
                {turn.attachments.map((a, k) =>
                  a.preview ? (
                    <button key={k} type="button" onClick={() => setZoom(a.preview ?? null)} title={a.name} className="press overflow-hidden rounded-xl border border-[var(--hairline-strong)]">
                      {/* eslint-disable-next-line @next/next/no-img-element -- data URL preview */}
                      <img src={a.preview} alt={a.name} className="h-20 w-20 object-cover" />
                    </button>
                  ) : (
                    <span key={k} title={a.name} className="flex max-w-[160px] items-center gap-1.5 rounded-lg bg-[var(--bg-raised)] px-2 py-1 text-[11px] text-[var(--text-2)]">
                      <Icon name="file" size={12} className="shrink-0 text-[var(--text-3)]" />
                      <span className="truncate">{a.name}</span>
                    </span>
                  ),
                )}
              </span>
            )}
            {turn.text}
          </>
        )}
        {turn.role === "user" && !editing && (
          <span className="absolute right-0 -bottom-1 flex translate-y-full items-center gap-0.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
            {turn.createdAt && (
              <MsgTip label={formatFullDate(turn.createdAt)}>
                <span className="mr-1 cursor-default text-[11px] text-[var(--text-3)]">
                  {formatShortDate(turn.createdAt)}
                </span>
              </MsgTip>
            )}
            <IconBtn label="Retry" icon="refresh" onClick={() => onFollowup(turn.text)} />
            <IconBtn label="Edit" icon="pencil" onClick={() => onStartEdit(idx, turn.text)} />
            <IconBtn
              label={copied ? "Copied" : "Copy"}
              icon={copied ? "check" : "copy"}
              success={copied}
              onClick={handleCopy}
            />
          </span>
        )}
        {turn.role === "assistant" && !turn.streaming && (
          <div className="mt-1.5">
            <span className="flex items-center gap-0.5">
              <IconBtn
                label={copied ? "Copied" : "Copy"}
                icon={copied ? "check" : "copy"}
                success={copied}
                onClick={handleCopy}
              />
              <IconBtn label="Read aloud" icon="volume" onClick={() => onSpeak(turn.text)} />
              <IconBtn
                label="Good response"
                icon="thumbsUp"
                active={turn.feedback === "up"}
                onClick={() => onRate(idx, "up")}
              />
              <IconBtn
                label="Bad response"
                icon="thumbsDown"
                active={turn.feedback === "down"}
                onClick={() => onRate(idx, "down")}
              />
              <IconBtn label="Retry" icon="refresh" onClick={onRetry} />
              {turn.createdAt && (
                <MsgTip label={formatFullDate(turn.createdAt)}>
                  <span className="ml-2 cursor-default text-[11px] text-[var(--text-3)]">
                    {formatShortDate(turn.createdAt)}
                  </span>
                </MsgTip>
              )}
            </span>
            {(turn.usage || turn.model) && (
              <p className="mt-1 font-mono text-[10px] tabular text-[var(--text-3)]">
                {[turn.model?.replace(/^(openai|anthropic|google):/, ""), turn.usage ? `${turn.usage.total_tokens.toLocaleString()} tokens` : null, turn.usage && turn.usage.cost_usd > 0 ? `$${turn.usage.cost_usd.toFixed(4)}` : null].filter(Boolean).join(" · ")}
              </p>
            )}
          </div>
        )}
        {turn.role === "assistant" && turn.sources && turn.sources.length > 0 && (
          <div className="mt-2.5 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-2.5">
            <p className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-3)]">
              Sources
            </p>
            <div className="flex flex-col gap-1">
              {turn.sources.map((s) => (
                <a
                  key={s.id}
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  className="press fluid flex items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-[var(--panel-hover)]"
                >
                  <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[var(--accent-dim)] font-mono text-[10px] font-bold text-[var(--accent)]">
                    {s.id}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-[var(--text-1)]">
                    {s.title}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-[var(--text-3)]">
                    {s.url.replace(/^https?:\/\//, "").split("/")[0]}
                  </span>
                </a>
              ))}
            </div>
          </div>
        )}
        {turn.role === "assistant" && turn.followups && turn.followups.length > 0 && !turn.streaming && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {turn.followups.map((q) => (
              <button
                key={q}
                onClick={() => onFollowup(q)}
                disabled={busy}
                className="press fluid rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 py-1 text-[11.5px] text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)] disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Assistant prose with artifact declarations replaced by openable cards. */
function AssistantContent({
  text,
  keyPrefix,
  final,
  onOpen,
}: {
  text: string;
  keyPrefix: string;
  final: boolean;
  onOpen: (id: string) => void;
}) {
  const segments = useMemo(
    () => parseSegments(text || "…", keyPrefix, final),
    [text, keyPrefix, final],
  );
  return (
    <>
      {segments.map((seg, idx) =>
        seg.type === "text" ? (
          <Streamdown key={idx}>{seg.text}</Streamdown>
        ) : (
          <ArtifactCard key={seg.artifact.id} artifact={seg.artifact} onOpen={onOpen} />
        ),
      )}
    </>
  );
}

/** Claude-grade artifact card: open in canvas, copy source, download file. */
function ArtifactCard({
  artifact,
  onOpen,
}: {
  artifact: Artifact;
  onOpen: (id: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = (e: ReactMouseEvent) => {
    e.stopPropagation();
    navigator.clipboard?.writeText(artifact.content).then(() => setCopied(true)).catch(() => {});
    window.setTimeout(() => setCopied(false), 1500);
  };
  const download = (e: ReactMouseEvent) => {
    e.stopPropagation();
    void import("@/lib/artifacts").then(({ artifactBytes, artifactFilename, artifactMime }) => {
      const blob = new Blob([artifactBytes(artifact) as BlobPart], {
        type: `${artifactMime(artifact)};charset=utf-8`,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = artifactFilename(artifact);
      link.click();
      URL.revokeObjectURL(url);
    });
  };
  return (
    <span className="my-2 flex w-full max-w-[460px] items-center gap-3 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-2.5">
      <button
        onClick={() => onOpen(artifact.id)}
        className="press flex min-w-0 flex-1 items-center gap-3 text-left"
        title="Open in canvas"
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--accent-dim)] text-[var(--accent)]">
          <Icon name={artifact.kind === "code" ? "code" : "artifact"} size={14} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12.5px] font-medium text-[var(--text-1)]">
            {artifact.title}
          </span>
          <span className="block truncate font-mono text-[10.5px] text-[var(--text-3)]">
            {artifact.kind} · {artifact.content.split("\n").length} lines
            {!artifact.complete ? " · generating…" : ""}
          </span>
        </span>
        <span className="shrink-0 text-[11px] font-medium text-[var(--accent)]">Open</span>
      </button>
      <span className="flex shrink-0 items-center gap-0.5 border-l border-[var(--hairline)] pl-1.5">
        <IconBtn label={copied ? "Copied" : "Copy source"} icon={copied ? "check" : "copy"} success={copied} onClick={copy} />
        <IconBtn label="Download file" icon="download" onClick={download} />
      </span>
    </span>
  );
}

/** Black hover tooltip for message actions, exactly like the reference. */
function MsgTip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="group/tip relative inline-flex shrink-0 items-center">
      {children}
      <span className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-2 hidden -translate-x-1/2 whitespace-nowrap rounded-lg bg-[var(--solid)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--on-solid)] shadow-[var(--shadow-pop)] group-hover/tip:block">
        {label}
      </span>
    </span>
  );
}

function formatShortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatFullDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Small ghost icon button for message toolbars. */
function IconBtn({
  label,
  icon,
  onClick,
  active,
  success,
}: {
  label: string;
  icon: Parameters<typeof Icon>[0]["name"];
  onClick: (e: ReactMouseEvent) => void;
  active?: boolean;
  success?: boolean;
}) {
  return (
    <MsgTip label={label}>
      <button
        onClick={onClick}
        aria-label={label}
        className={`press fluid rounded-md p-1.5 ${
          success
            ? "text-[var(--ok)]"
            : active
              ? "text-[var(--accent)]"
              : "text-[var(--text-3)] hover:text-[var(--text-1)]"
        }`}
      >
        <Icon name={icon} size={13} />
      </button>
    </MsgTip>
  );
}

const VALID_STEP_STATUSES = new Set(["pending", "in_progress", "completed", "cancelled", "blocked"]);

function normalizePlanItems(raw: unknown): PlanStep[] {
  if (!Array.isArray(raw)) return [];
  const steps: PlanStep[] = [];
  raw.forEach((item, i) => {
    const it = item as { id?: unknown; content?: unknown; status?: unknown; active_form?: unknown };
    if (typeof it?.content !== "string" || !it.content.trim()) return;
    steps.push({
      id: typeof it.id === "string" && it.id ? it.id : `step-${i}`,
      content: it.content,
      status: (typeof it.status === "string" && VALID_STEP_STATUSES.has(it.status)
        ? it.status
        : "pending") as PlanStep["status"],
      active_form: typeof it.active_form === "string" ? it.active_form : undefined,
    });
  });
  return steps;
}

const CARD_PHASE_META: Record<PlanPhase, { label: string; tone: string }> = {
  streaming: { label: "Drafting the plan…", tone: "text-[var(--text-3)]" },
  awaiting: { label: "Review the plan, then approve to start execution.", tone: "text-[var(--warn)]" },
  approved: { label: "Approved  -  delegating to specialists.", tone: "text-[var(--ok)]" },
  declined: { label: "Declined  -  the orchestrator is revising.", tone: "text-[var(--danger)]" },
  completed: { label: "Completed.", tone: "text-[var(--ok)]" },
};

/**
 * The official AI Elements `Plan` card, rendered in the message thread while
 * the orchestrator's plan is drafted, approved, and executed.
 */
function PlanCardMessage({
  plan,
  disabled,
  onDecide,
}: {
  plan: PlanCardState;
  disabled: boolean;
  onDecide: (approved: boolean) => void;
}) {
  // "streaming" with steps present = post-approval execution.
  const executing = plan.phase === "streaming" && plan.items.length > 0;
  const meta = executing
    ? { label: "Executing  -  delegated to specialists.", tone: "text-[var(--accent)]" }
    : CARD_PHASE_META[plan.phase];
  const pending = plan.phase === "awaiting";
  return (
    <Plan
      isStreaming={plan.phase === "streaming"}
      defaultOpen
      className="gap-0 border-[var(--hairline)] bg-[var(--bg-raised)] py-4"
    >
      <PlanHeader className="px-4">
        <div className="min-w-0">
          <PlanTitle>Execution plan</PlanTitle>
          <PlanDescription className={meta.tone}>{meta.label}</PlanDescription>
        </div>
        <PlanAction>
          <PlanTrigger />
        </PlanAction>
      </PlanHeader>
      <PlanContent className="px-4">
        {plan.items.length > 0 ? (
          <ol className="-space-y-0.5 py-1">
            {plan.items.map((step, i) => (
              <PlanStepRow key={step.id || `step-${i}`} step={step} />
            ))}
          </ol>
        ) : (
          <p className="py-1 text-[12.5px] text-[var(--text-3)]">Preparing steps…</p>
        )}
      </PlanContent>
      {pending && (
        <PlanFooter className="border-t border-[var(--hairline)] px-4 pb-1 pt-3">
          <span className="flex items-center gap-1.5 text-[12px] font-semibold font-medium text-[var(--warn)]">
            <Icon name="shield" size={11} /> Approval required
          </span>
          <PlanAction className="flex items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => onDecide(false)}
            >
              Decline
            </Button>
            <Button size="sm" disabled={disabled} onClick={() => onDecide(true)}>
              Approve plan
            </Button>
          </PlanAction>
        </PlanFooter>
      )}
    </Plan>
  );
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionResultLike) => void) | null;
}

interface SpeechRecognitionResultLike {
  results: {
    length: number;
    [index: number]: { [index: number]: { transcript: string } };
  };
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function resampleToPcm16(input: Float32Array, fromRate: number, toRate: number): Int16Array {
  if (!input.length) return new Int16Array(0);
  if (fromRate === toRate) {
    const pcm = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, input[i] * 32767));
    return pcm;
  }
  const ratio = fromRate / toRate;
  const outLen = Math.max(1, Math.round(input.length / ratio));
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const src = i * ratio;
    const i0 = Math.floor(src);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const sample = input[i0] * (1 - (src - i0)) + input[i1] * (src - i0);
    pcm[i] = Math.max(-32768, Math.min(32767, sample * 32767));
  }
  return pcm;
}

function pcm16Base64ToFloat32(b64: string): Float32Array {
  const raw = atob(b64);
  const length = raw.length >> 1;
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const lo = raw.charCodeAt(2 * i);
    const hi = raw.charCodeAt(2 * i + 1);
    let sample = (hi << 8) | lo;
    if (sample >= 0x8000) sample -= 0x10000;
    out[i] = sample / 32768;
  }
  return out;
}

export default function AssistantPage() {
  return (
    <AuthProvider>
      <AppShell>
        <AssistantInner />
      </AppShell>
    </AuthProvider>
  );
}
