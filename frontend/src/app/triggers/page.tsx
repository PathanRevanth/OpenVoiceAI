"use client";

/**
 * Triggers: scheduled agent runs. Create an interval or daily trigger, and
 * the platform fires it through the same invocation path as the API and
 * webhooks. Results land in Observability and optionally post to a webhook.
 */

import { useCallback, useEffect, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { Chip, CopyField, EmptyState, PageHeader, SectionHead, Toggle, timeAgo } from "@/components/ui";
import { Icon } from "@/components/icons";
import { backend, type AgentInfo, type TriggerRecord } from "@/lib/backend";

function TriggersInner() {
  const [triggers, setTriggers] = useState<TriggerRecord[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    agent_slug: "",
    message: "",
    scheduleType: "interval" as "interval" | "daily",
    intervalMinutes: 60,
    timeOfDay: "09:00",
    webhookUrl: "",
  });

  const refresh = useCallback(async () => {
    const [t, a] = await Promise.all([backend.triggers(), backend.agents()]);
    setTriggers(t);
    setAgents(a);
    return { t, a };
  }, []);

  useEffect(() => {
    refresh().then(({ a }) => {
      setForm((f) => ({ ...f, agent_slug: f.agent_slug || a.find((x) => x.status === "enabled")?.slug || "" }));
    }).catch(() => {});
  }, [refresh]);

  const create = async () => {
    setBusy("create");
    setError(null);
    try {
      await backend.createTrigger({
        name: form.name.trim(),
        agent_slug: form.agent_slug,
        message: form.message.trim(),
        schedule_type: form.scheduleType,
        interval_minutes: form.scheduleType === "interval" ? form.intervalMinutes : undefined,
        time_of_day: form.scheduleType === "daily" ? form.timeOfDay : undefined,
        webhook_url: form.webhookUrl.trim() || undefined,
      });
      setForm((f) => ({ ...f, name: "", message: "", webhookUrl: "" }));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message.replace(/^API .*failed: \d+\s*/, "") : "Failed to create trigger");
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (t: TriggerRecord) => {
    setBusy(t.id);
    try {
      await backend.setTriggerEnabled(t.id, !t.enabled);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const runNow = async (t: TriggerRecord) => {
    setBusy(t.id);
    try {
      await backend.runTriggerNow(t.id);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const remove = async (t: TriggerRecord) => {
    setBusy(t.id);
    try {
      await backend.deleteTrigger(t.id);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const canCreate = form.name.trim() && form.agent_slug && form.message.trim();

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Triggers"
        description="Scheduled agent runs. Every firing goes through the same governance path as the API: policy, approvals, usage, and the audit trail."
      />

      {/* create */}
      <div className="mt-8 rounded-2xl border border-[var(--hairline)] p-5">
        <SectionHead>New trigger</SectionHead>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">Name</span>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Daily churn digest"
              className="fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">Agent</span>
            <select
              value={form.agent_slug}
              onChange={(e) => setForm((f) => ({ ...f, agent_slug: e.target.value }))}
              className="fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] text-[var(--text-1)] outline-none hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            >
              {agents.filter((a) => a.status === "enabled").map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="mt-3 block">
          <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">Message</span>
          <textarea
            value={form.message}
            onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
            rows={2}
            placeholder="Aggregate MRR by region and flag churn risks."
            className="fluid w-full resize-none rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-2.5 text-[12.5px] leading-relaxed outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
          />
        </label>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">Schedule</span>
            <div className="flex rounded-xl border border-[var(--hairline)] bg-[var(--panel-hover)] p-1">
              {(["interval", "daily"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setForm((f) => ({ ...f, scheduleType: t }))}
                  className={`fluid rounded-lg px-3 py-1.5 text-[12px] font-medium ${
                    form.scheduleType === t
                      ? "bg-[var(--bg-raised)] text-[var(--text-1)] shadow-[0_1px_2px_rgba(22,23,28,0.08)]"
                      : "text-[var(--text-3)]"
                  }`}
                >
                  {t === "interval" ? "Interval" : "Daily"}
                </button>
              ))}
            </div>
          </div>
          {form.scheduleType === "interval" ? (
            <label className="block">
              <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">Every (minutes)</span>
              <input
                type="number"
                min={1}
                max={10080}
                value={form.intervalMinutes}
                onChange={(e) => setForm((f) => ({ ...f, intervalMinutes: Number(e.target.value) }))}
                className="fluid h-10 w-28 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] tabular outline-none hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              />
            </label>
          ) : (
            <label className="block">
              <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">At (UTC)</span>
              <input
                type="time"
                value={form.timeOfDay}
                onChange={(e) => setForm((f) => ({ ...f, timeOfDay: e.target.value }))}
                className="fluid h-10 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] tabular outline-none hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              />
            </label>
          )}
          <label className="min-w-0 flex-1 basis-56">
            <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">
              Result webhook (optional)
            </span>
            <input
              value={form.webhookUrl}
              onChange={(e) => setForm((f) => ({ ...f, webhookUrl: e.target.value }))}
              placeholder="https://hooks.example.com/voicefirst"
              className="fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 font-mono text-[11.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            />
          </label>
        </div>
        {error && (
          <p className="mt-3 rounded-xl border border-[var(--danger)]/25 bg-[var(--danger-dim)] px-3.5 py-2.5 text-[12px] text-[var(--danger)]">
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end">
          <button
            onClick={() => void create()}
            disabled={busy === "create" || !canCreate}
            className="press fluid rounded-xl bg-[var(--accent)] px-4 py-2 text-[12.5px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-40"
          >
            {busy === "create" ? "Creating" : "Create trigger"}
          </button>
        </div>
      </div>

      {/* list */}
      <section className=" mt-12">
        <SectionHead>
          Triggers{" "}
          {triggers.length > 0 && (
            <span className="tabular text-[14px] text-[var(--text-3)]">({triggers.length})</span>
          )}
        </SectionHead>
        {triggers.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              icon="clock"
              title="No triggers yet"
              body="Schedule an agent to run on an interval or daily at a fixed time. Results appear in Observability and can post to your webhook."
            />
          </div>
        ) : (
          <div className="mt-4 divide-y divide-[var(--hairline)] border-y border-[var(--hairline)]">
            {triggers.map((t) => (
              <div key={t.id} className="flex flex-wrap items-center gap-x-5 gap-y-3 py-5">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--accent-dim)]">
                  <Icon name="clock" size={16} className="text-[var(--accent)]" />
                </span>
                <div className="min-w-0 flex-1 basis-72">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-[14.5px] font-semibold text-[var(--text-1)]">{t.name}</p>
                    <Chip tone="neutral">{t.agent_slug}</Chip>
                    {t.last_status && (
                      <Chip
                        tone={
                          t.last_status === "completed"
                            ? "ok"
                            : t.last_status === "failed"
                              ? "danger"
                              : t.last_status === "awaiting_approval"
                                ? "amber"
                                : "neutral"
                        }
                      >
                        {t.last_status}
                      </Chip>
                    )}
                  </div>
                  <p className="mt-0.5 line-clamp-1 text-[12px] text-[var(--text-2)]">{t.message}</p>
                  <p className="mt-1 font-mono text-[10.5px] text-[var(--text-3)]">
                    {t.schedule}
                    {t.next_run_at ? `, next ${new Date(t.next_run_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
                    {t.last_run_at ? `, ran ${timeAgo(t.last_run_at)}` : ""}
                    {t.webhook_url ? ", delivers to webhook" : ""}
                  </p>
                </div>
                <Toggle
                  checked={t.enabled}
                  onChange={() => toggle(t)}
                  disabled={busy === t.id}
                  label={`Enable ${t.name}`}
                />
                <div className="flex shrink-0 gap-1.5">
                  <button
                    onClick={() => runNow(t)}
                    disabled={busy === t.id}
                    className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)] disabled:opacity-50"
                  >
                    Run now
                  </button>
                  <button
                    onClick={() => remove(t)}
                    disabled={busy === t.id}
                    className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default function TriggersPage() {
  return (
    <AuthProvider>
      <AppShell>
        <TriggersInner />
      </AppShell>
    </AuthProvider>
  );
}
