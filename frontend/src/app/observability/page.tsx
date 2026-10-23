"use client";

/**
 * Observability  -  run traces with span waterfalls.
 *
 * Run table (status, latency, tokens, cost) with filters and a slide-over
 * detail drawer containing the span waterfall, usage breakdown, and the
 * conversation timeline.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { Chip, Drawer, PageHeader, StatStrip, timeAgo, fmtK } from "@/components/ui";
import { useCountUp } from "@/components/use-count-up";
import { Icon } from "@/components/icons";
import {
  backend,
  type RunSummary,
  type UsageSummary,
  type VoiceAnalytics,
  type VoiceSession,
} from "@/lib/backend";

type StatusFilter = "all" | "completed" | "failed" | "awaiting_approval";

const STATUS_TONE: Record<string, { tone: "ok" | "danger" | "amber" | "accent" | "neutral"; label: string }> = {
  completed: { tone: "ok", label: "completed" },
  failed: { tone: "danger", label: "failed" },
  awaiting_approval: { tone: "amber", label: "awaiting approval" },
  running: { tone: "accent", label: "running" },
  cancelled: { tone: "neutral", label: "cancelled" },
};

function StatusChip({ status }: { status: string }) {
  const s = STATUS_TONE[status] ?? { tone: "neutral" as const, label: status };
  return <Chip tone={s.tone} dot pulse={status === "running"}>{s.label}</Chip>;
}

function ObservabilityInner() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [selected, setSelected] = useState<RunSummary | null>(null);
  const [spans, setSpans] = useState<
    { name: string; kind: string; start_ms: number; end_ms: number; duration_ms: number }[]
  >([]);
  const [timeline, setTimeline] = useState<
    { id: string; role: string; agent: string | null; text: string; created_at: string }[]
  >([]);

  const [agentFilter, setAgentFilter] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");

  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [sinceHours, setSinceHours] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const refresh = useCallback(
    async (opts?: { append?: boolean }) => {
      const limit = 50;
      const off = opts?.append ? offset : 0;
      const [res, u] = await Promise.all([
        backend.runs({ limit, offset: off, since_hours: sinceHours ?? undefined }),
        backend.usage(30),
      ]);
      setRuns((prev) => {
        if (opts?.append) {
          const known = new Set(prev.map((x) => x.id));
          return [...prev, ...res.runs.filter((x) => !known.has(x.id))];
        }
        return res.runs;
      });
      setTotal(res.total);
      setHasMore(res.runs.length === limit);
      setOffset(off + limit);
      setUsage(u);
    },
    [offset, sinceHours],
  );

  useEffect(() => {
    refresh().catch(() => {});
    const t = setInterval(() => refresh().catch(() => {}), 8_000);
    return () => clearInterval(t);
  }, [refresh]);

  const clearAll = async () => {
    setClearing(true);
    try {
      await backend.clearRuns();
      await refresh();
    } finally {
      setClearing(false);
    }
  };

  const openRun = useCallback(async (run: RunSummary) => {
    setSelected(run);
    setSpans([]);
    setTimeline([]);
    if (run.conversation_id) {
      try {
        const conv = await backend.conversation(run.conversation_id);
        setTimeline(
          conv.messages
            .filter((m) => m.content?.text)
            .map((m) => ({
              id: m.id,
              role: m.role,
              agent: m.agent,
              text: m.content.text ?? "",
              created_at: m.created_at,
            })),
        );
      } catch {
        /* conversation gone */
      }
    }
    try {
      const detail = await backend.runDetail(run.id);
      setSpans(detail.spans ?? []);
    } catch {
      setSpans([]);
    }
  }, []);

  const runsAnimated = useCountUp(usage?.runs ?? 0, (usage?.runs ?? 0) > 0);
  const tokensAnimated = useCountUp(
    (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0),
    (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0) > 0,
  );
  const agentNames = useMemo(() => Array.from(new Set(runs.map((r) => r.agent))).sort(), [runs]);

  const filtered = runs.filter(
    (r) =>
      (filter === "all" || r.status === filter) &&
      (agentFilter === "all" || r.agent === agentFilter) &&
      (!searchQuery || r.id.includes(searchQuery) || r.agent.includes(searchQuery)),
  );

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Observability"
        description="Trace every step, not just the final answer: per-run span waterfalls with latency, token cost, and the conversation timeline behind each trace."
        actions={
          <div className="flex items-center gap-2">
            <select
              value={sinceHours ?? ""}
              onChange={(e) => setSinceHours(e.target.value ? Number(e.target.value) : null)}
              className="fluid h-9 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 text-[12px] text-[var(--text-2)] outline-none hover:border-[var(--hairline-strong)]"
            >
              <option value="">All time</option>
              <option value="1">Last hour</option>
              <option value="24">Last 24h</option>
              <option value="168">Last 7d</option>
            </select>
            <button
              onClick={() => void clearAll()}
              disabled={clearing}
              className="press fluid flex items-center gap-2 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-2 text-[12.5px] font-medium text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-40"
            >
              <Icon name="x" size={13} /> {clearing ? "Clearing" : "Clear runs"}
            </button>
            <button
              onClick={() => refresh().catch(() => {})}
              className="press fluid flex items-center gap-2 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-2 text-[12.5px] font-medium text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
            >
              <Icon name="refresh" size={14} /> Refresh
            </button>
          </div>
        }
      />

      {/* stats */}
      <div className=" mt-8">
        <StatStrip
          items={[
            { label: "Runs (30d)", value: fmtK(runsAnimated), sub: `${fmtK(usage?.requests ?? 0)} model requests` },
            { label: "Tokens (30d)", value: fmtK(tokensAnimated), sub: `${fmtK(usage?.tool_calls ?? 0)} tool calls` },
            { label: "Cost (30d)", value: `${(usage?.cost_usd ?? 0).toFixed(2)}`, sub: "across all agents" },
          ]}
        />
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-[1fr_360px]">
        {/* ------------------------------------------------ runs table */}
        <VoicePanel />

        <section className=" mt-10 min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {(["all", "completed", "failed", "awaiting_approval"] as StatusFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`fluid rounded-full px-3 py-1.5 text-[11.5px] font-medium capitalize ${
                  filter === f
                    ? "bg-[var(--solid)] text-[var(--on-solid)]"
                    : "border border-[var(--hairline)] bg-[var(--bg-raised)] text-[var(--text-2)] hover:border-[var(--hairline-strong)]"
                }`}
              >
                {f === "all" ? "All runs" : STATUS_TONE[f]?.label ?? f}
              </button>
            ))}
            {agentNames.length > 1 && (
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="fluid ml-1 h-8 rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 text-[11.5px] text-[var(--text-2)] outline-none hover:border-[var(--hairline-strong)]"
              >
                <option value="all">All agents</option>
                {agentNames.map((a: string) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            )}
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search run id…"
              className="fluid ml-auto h-8 w-40 rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[11.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            />
          </div>

          <div className="board overflow-hidden rounded-2xl">
            <div className="grid grid-cols-[1.4fr_1fr_1fr_.7fr_.8fr_.8fr] gap-2 border-b border-[var(--hairline)] px-4 py-2.5">
              {["Run", "Agent", "Status", "Latency", "Tokens", "Cost"].map((h) => (
                <span key={h} className="text-[11px] font-medium text-[var(--text-3)]">{h}</span>
              ))}
            </div>
            {filtered.length === 0 && (
              <p className="px-4 py-10 text-center text-[12.5px] text-[var(--text-3)]">
                No runs yet. Start a conversation and traces appear here in real time.
              </p>
            )}
            {filtered.slice(0, 60).map((r) => (
              <button
                key={r.id}
                onClick={() => void openRun(r)}
                className="fluid grid w-full grid-cols-[1.4fr_1fr_1fr_.7fr_.8fr_.8fr] items-center gap-2 border-b border-[var(--hairline)] px-4 py-3 text-left last:border-0 hover:bg-[var(--panel-hover)]"
              >
                <span className="truncate font-mono text-[11px] text-[var(--text-2)]">{r.id}</span>
                <span className="truncate text-[12.5px] text-[var(--text-1)]">{r.agent}</span>
                <span><StatusChip status={r.status} /></span>
                <span className="tabular text-[12px] text-[var(--text-2)]">
                  {r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)}s` : "n/a"}
                </span>
                <span className="tabular text-[12px] text-[var(--text-2)]">
                  {r.usage ? fmtK(r.usage.total_tokens) : "n/a"}
                </span>
                <span className="tabular text-[12px] text-[var(--text-2)]">
                  {r.usage ? `$${r.usage.cost_usd.toFixed(3)}` : "n/a"}
                </span>
              </button>
            ))}
          </div>
        </section>

      </div>

      {/* ------------------------------------------------ run drawer */}
      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={
          selected && (
            <div className="flex items-center gap-3">
              <span className="font-mono text-[12px] text-[var(--text-2)]">{selected.id}</span>
              <StatusChip status={selected.status} />
            </div>
          )
        }
      >
        {selected && (
          <div className="space-y-6 p-5">
            <div className="grid grid-cols-2 gap-2.5">
              <Meta label="Agent" value={selected.agent} />
              <Meta label="Model" value={selected.model ?? "n/a"} mono />
              <Meta label="Started" value={timeAgo(selected.created_at)} />
              <Meta label="Duration" value={selected.duration_ms != null ? `${(selected.duration_ms / 1000).toFixed(1)}s` : "n/a"} />
            </div>

            <ReplayPanel
              run={selected}
              onReplayed={() => {
                refresh().catch(() => {});
              }}
            />

            {selected.error && (
              <div className="rounded-xl border border-[var(--danger)]/25 bg-[var(--danger-dim)] px-3.5 py-3">
                <p className="text-[12px] font-semibold font-mediumr text-[var(--danger)]">Error</p>
                <p className="mt-1 break-words font-mono text-[11px] text-[var(--danger)]">{selected.error}</p>
              </div>
            )}

            {selected.usage && (
              <div>
                <section>
                  <p className="mb-2.5 text-[12px] font-semibold font-medium text-[var(--text-3)]">
                    Span waterfall
                  </p>
                  <Waterfall
                    spans={spans}
                    totalMs={selected.duration_ms ?? Math.max(...spans.map((s) => s.end_ms), 1)}
                  />
                </section>
                <p className="text-[11px] font-medium text-[var(--text-3)]">Usage</p>
                <div className="grid grid-cols-3 gap-2">
                  <UsageCell label="Input" value={fmtK(selected.usage.input_tokens)} />
                  <UsageCell label="Output" value={fmtK(selected.usage.output_tokens)} />
                  <UsageCell label="Total" value={fmtK(selected.usage.total_tokens)} />
                  <UsageCell label="Requests" value={String(selected.usage.requests)} />
                  <UsageCell label="Tool calls" value={String(selected.usage.tool_calls)} />
                  <UsageCell label="Cost" value={`$${selected.usage.cost_usd.toFixed(4)}`} />
                </div>
              </div>
            )}

            <div>
              <p className="text-[11px] font-medium text-[var(--text-3)]">Conversation timeline</p>
              {timeline.length === 0 ? (
                <p className="text-[12px] text-[var(--text-3)]">No persisted messages for this run.</p>
              ) : (
                <div className="space-y-2">
                  {timeline.map((m) => (
                    <div
                      key={m.id}
                      className={`rounded-xl border px-3.5 py-2.5 ${
                        m.role === "user"
                          ? "border-[var(--hairline)] bg-[var(--panel-hover)]"
                          : m.role === "system"
                            ? "border-dashed border-[var(--hairline)]"
                            : "border-[var(--hairline)] bg-[var(--bg-raised)]"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[12px] font-bold font-mediumr text-[var(--text-3)]">
                          {m.agent ?? m.role}
                        </span>
                        <span className="tabular text-[10px] text-[var(--text-3)]">
                          {new Date(m.created_at).toLocaleTimeString()}
                        </span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--text-1)]">
                        {m.text.slice(0, 600)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}

/** Replay a stored run: regenerates its answer from recorded history, with an
  * optional model swap. Streams the new run and shows the fresh output. */
function ReplayPanel({ run, onReplayed }: { run: RunSummary; onReplayed: () => void }) {
  const [replaying, setReplaying] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const replay = async () => {
    setReplaying(true);
    setError(null);
    setOutput(null);
    try {
      const res = await backend.replayRun(run.id);
      if (!res.ok || !res.body) throw new Error(`Replay failed: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done = false;
      while (!done) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const line = frame.trim();
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6)) as { type?: string; data?: { delta?: string; text?: string; error?: string } };
            if (event.type === "text.delta" && event.data?.delta) {
              setOutput((prev) => (prev ?? "") + event.data!.delta);
            } else if (event.type === "text.done" && event.data?.text) {
              setOutput(event.data.text);
            } else if (event.type === "run.failed") {
              setError(event.data?.error ?? "Replay failed");
            }
          } catch {
            /* malformed frame */
          }
        }
      }
      onReplayed();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReplaying(false);
    }
  };

  return (
    <div className="rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-3.5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[12px] font-semibold text-[var(--text-1)]">Replay</p>
          <p className="mt-0.5 text-[11px] text-[var(--text-3)]">
            Regenerate this run from its recorded history to compare behavior.
          </p>
        </div>
        <button
          onClick={() => void replay()}
          disabled={replaying}
          className="press fluid shrink-0 rounded-xl border border-[var(--hairline)] px-3 py-2 text-[11.5px] font-semibold text-[var(--text-1)] hover:border-[var(--hairline-strong)] disabled:opacity-50"
        >
          {replaying ? "Replaying…" : "Replay run"}
        </button>
      </div>
      {(output || error) && (
        <div className="mt-3 rounded-lg border border-[var(--hairline)] bg-[var(--panel-hover)] p-3">
          {error ? (
            <p className="text-[11.5px] text-[var(--danger)]">{error}</p>
          ) : (
            <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-[var(--text-1)]">{output}</p>
          )}
        </div>
      )}
    </div>
  );
}

/** Voice quality: sessions, TTFW, barge-ins (Vapi/Retell-style voice metrics). */
function VoicePanel() {
  const [analytics, setAnalytics] = useState<VoiceAnalytics | null>(null);
  const [sessions, setSessions] = useState<VoiceSession[]>([]);

  useEffect(() => {
    const load = () => {
      backend.voiceAnalytics(7).then(setAnalytics).catch(() => {});
      backend.voiceSessions(6).then(setSessions).catch(() => {});
    };
    load();
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, []);

  if (!analytics || analytics.sessions === 0) return null;

  return (
    <section className=" mt-10 min-w-0">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-[13px] font-semibold tracking-tight text-[var(--text-1)]">Voice quality</h3>
        <span className="text-[11px] text-[var(--text-3)]">last {analytics.window_days} days</span>
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <StatCard label="Sessions" value={String(analytics.sessions)} />
        <StatCard label="Avg TTFW" value={`${analytics.avg_ttfw_ms.toFixed(0)}ms`} hint="time to first word" />
        <StatCard label="Barge-ins" value={String(analytics.barge_ins)} hint="caller interruptions" />
        <StatCard label="Turns" value={String(analytics.turns)} />
      </div>
      {sessions.length > 0 && (
        <div className="mt-3 divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)]">
          {sessions.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-[11.5px]">
              <Chip tone={s.status === "ended" ? "ok" : s.status === "failed" ? "danger" : "accent"} dot>
                {s.status}
              </Chip>
              <span className="text-[var(--text-2)]">{s.channel}</span>
              <span className="font-mono text-[10.5px] text-[var(--text-3)]">{s.model ?? "n/a"}</span>
              <span className="ml-auto text-[var(--text-3)]">
                {s.turns} turns · TTFW {s.ttfw_ms != null ? `${s.ttfw_ms}ms` : " - "} · {s.barge_ins} interrupts
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-3">
      <p className="text-[12px] font-semibold font-medium text-[var(--text-3)]">{label}</p>
      <p className="mt-1 text-[17px] font-semibold tabular tracking-tight text-[var(--text-1)]">{value}</p>
      {hint && <p className="text-[10px] text-[var(--text-3)]">{hint}</p>}
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {  return (
    <div className="rounded-xl border border-[var(--hairline)] px-3.5 py-2.5">
      <p className="text-[11px] font-medium text-[var(--text-3)]">{label}</p>
      <p className={`mt-1 truncate text-[12.5px] text-[var(--text-1)] ${mono ? "font-mono" : ""}`}>
        {value}
      </p>
    </div>
  );
}

function UsageCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-[var(--panel-hover)] px-3 py-2.5">
      <p className="text-[12px] font-medium font-mediumr text-[var(--text-3)]">{label}</p>
      <p className="tabular mt-0.5 text-[13px] font-semibold text-[var(--text-1)]">{value}</p>
    </div>
  );
}

export default function ObservabilityPage() {
  return (
    <AuthProvider>
      <AppShell>
        <ObservabilityInner />
      </AppShell>
    </AuthProvider>
  );
}

const KIND_COLORS: Record<string, string> = {
  model: "var(--accent)",
  tools: "var(--ok)",
  tool: "var(--warn)",
};

/** Animated waterfall timeline: one bar per span over a shared time axis. */
function Waterfall({
  spans,
  totalMs,
}: {
  spans: { name: string; kind: string; start_ms: number; end_ms: number; duration_ms: number }[];
  totalMs: number;
}) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  if (spans.length === 0) {
    return (
      <p className="rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-4 py-8 text-center text-[12px] text-[var(--text-3)]">
        Run started before span tracing was enabled. New runs include full span timelines.
      </p>
    );
  }
  const total = Math.max(totalMs, ...spans.map((s) => s.end_ms), 1);
  return (
    <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-4">
      <div className="space-y-2">
        {spans.map((s, i) => {
          const left = (s.start_ms / total) * 100;
          const width = Math.max(1.5, (s.duration_ms / total) * 100);
          const color =
            s.kind === "model" ? "var(--accent)" : s.kind === "tools" ? "var(--ok)" : "var(--warn)";
          return (
            <div key={i} className="flex items-center gap-3">
              <span className="w-20 shrink-0 truncate font-mono text-[10.5px] text-[var(--text-2)]">
                {s.name}
              </span>
              <div className="relative h-5 min-w-0 flex-1 rounded-lg bg-[var(--panel-hover)]">
                <div
                  className="absolute inset-y-0 rounded-lg"
                  style={{
                    left: mounted ? left + "%" : "0%",
                    width: mounted ? Math.max(1.5, width) + "%" : "0%",
                    background: color,
                    opacity: 0.85,
                    transition:
                      "left 700ms var(--ease) " + i * 80 + "ms, width 700ms var(--ease) " + i * 80 + "ms",
                  }}
                />
                <span
                  className="tabular absolute top-1/2 -translate-y-1/2 pl-2 text-[9.5px] font-medium text-[var(--text-2)]"
                  style={{ left: Math.min(left + width, 80) + "%" }}
                >
                  {s.duration_ms}ms
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-4 border-t border-[var(--hairline)] pt-2.5 text-[10px] text-[var(--text-3)]">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-[var(--accent)]" /> model call
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-[var(--warn)]" /> tool call
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-[var(--ok)]" /> tool phase
        </span>
        <span className="tabular ml-auto">{total}ms</span>
      </div>
    </div>
  );
}
