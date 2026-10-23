"use client";

/**
 * Usage & spend. Editorial layout: display figures in a hairline stat strip,
 * spend bars and the daily chart in plain sections. No boxed cards.
 */

import { useCallback, useEffect, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { PageHeader, SectionHead, StatStrip, fmtK } from "@/components/ui";
import { backend, type UsageSummary } from "@/lib/backend";

const WINDOWS = [7, 30, 90] as const;

function UsageInner() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [barsIn, setBarsIn] = useState(false);

  const refresh = useCallback(async () => {
    setUsage(await backend.usage(days));
    requestAnimationFrame(() => requestAnimationFrame(() => setBarsIn(true)));
  }, [days]);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  const totalTokens = (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0);
  const maxDaily = Math.max(...(usage?.daily ?? []).map((d) => d.cost_usd), 0.0001);
  const maxAgent = Math.max(...(usage?.by_agent ?? []).map((d) => d.cost_usd), 0.0001);

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Usage & spend"
        description="Token consumption and cost across the fleet. Budgets are enforced by the platform: spend stops when a limit is hit."
        actions={
          <div className="flex items-center gap-2">
            <div className="flex rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-1">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  onClick={() => {
                    setBarsIn(false);
                    setDays(w);
                  }}
                  className={`fluid rounded-lg px-3 py-1.5 text-[12px] font-medium ${
                    days === w
                      ? "bg-[var(--text-1)] text-[var(--accent-contrast)]"
                      : "text-[var(--text-2)] hover:text-[var(--text-1)]"
                  }`}
                >
                  {w}d
                </button>
              ))}
            </div>
            <button
              onClick={() => void backend.exportUsage(days, "csv").catch(() => {})}
              className="press fluid rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 py-2 text-[11.5px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)]"
            >
              Export CSV
            </button>
          </div>
        }
      />

      <div className=" mt-8">
        <StatStrip
          items={[
            { label: "Cost", value: `$${(usage?.cost_usd ?? 0).toFixed(2)}`, sub: `last ${usage?.window_days ?? days} days` },
            { label: "Runs", value: fmtK(usage?.runs ?? 0), sub: `${fmtK(usage?.requests ?? 0)} model requests` },
            { label: "Tokens", value: fmtK(totalTokens), sub: `${fmtK(usage?.input_tokens ?? 0)} in, ${fmtK(usage?.output_tokens ?? 0)} out` },
            { label: "Tool calls", value: fmtK(usage?.tool_calls ?? 0), sub: "across all specialists" },
          ]}
        />
      </div>

      <div className="mt-12 grid gap-12 lg:grid-cols-2">
        <section className="">
          <SectionHead>Spend by agent</SectionHead>
          <div className="mt-5 space-y-5">
            {(usage?.by_agent ?? []).map((row) => (
              <div key={row.agent}>
                <div className="flex items-center justify-between text-[12.5px]">
                  <span className="font-mono text-[var(--text-1)]">{row.agent}</span>
                  <span className="tabular text-[var(--text-2)]">
                    {row.runs} runs, ${row.cost_usd.toFixed(2)}
                  </span>
                </div>
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--panel-hover)]">
                  <div
                    className="h-full w-full origin-left rounded-full bg-[var(--accent)]"
                    style={{
                      transform: barsIn
                        ? `scaleX(${Math.max(0.03, row.cost_usd / maxAgent)})`
                        : "scaleX(0.001)",
                      transition: "transform 800ms var(--ease)",
                    }}
                  />
                </div>
              </div>
            ))}
            {(usage?.by_agent ?? []).length === 0 && (
              <p className="py-6 text-[12.5px] text-[var(--text-3)]">
                No spend recorded in this window.
              </p>
            )}
          </div>
        </section>

        <section className="">
          <SectionHead>Daily cost</SectionHead>
          <div className="mt-5 flex h-44 items-end gap-1">
            {(usage?.daily ?? []).map((d) => (
              <div key={d.date} className="group relative flex h-full flex-1 items-end">
                <div
                  className="fluid w-full rounded-t-md bg-[var(--accent)] opacity-90 group-hover:bg-[var(--accent-strong)]"
                  style={{
                    transform: barsIn
                      ? `scaleY(${Math.max(0.025, d.cost_usd / maxDaily)})`
                      : "scaleY(0.025)",
                    transformOrigin: "bottom",
                    height: "100%",
                    transition: "transform 700ms var(--ease)",
                  }}
                />
                <div className="pointer-events-none absolute -top-8 left-1/2 -translate-x-1/2 rounded-lg bg-[var(--solid)] px-2 py-1 text-[10px] font-semibold text-[var(--on-solid)] opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                  ${d.cost_usd.toFixed(3)}
                </div>
              </div>
            ))}
            {(usage?.daily ?? []).length === 0 && (
              <p className="w-full py-16 text-center text-[12.5px] text-[var(--text-3)]">
                No daily data in this window.
              </p>
            )}
          </div>
          {(usage?.daily ?? []).length > 0 && (
            <div className="mt-2 flex justify-between font-mono text-[10px] text-[var(--text-3)]">
              <span>{usage?.daily[0]?.date}</span>
              <span>{usage?.daily[usage.daily.length - 1]?.date}</span>
            </div>
          )}
        </section>
      </div>

      {(usage?.by_user?.length ?? 0) > 0 && (
        <section className="mt-12 mb-8">
          <SectionHead>Spend by user</SectionHead>
          <p className="mt-1 text-[12px] text-[var(--text-3)]">
            Per-user cost attribution for chargeback and showback reports.
          </p>
          <div className="mt-4 divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)]">
            {[...(usage?.by_user ?? [])]
              .sort((a, b) => b.cost_usd - a.cost_usd)
              .map((row) => (
                <div key={row.user_id} className="flex items-center gap-3 px-4 py-3 text-[12.5px]">
                  <span className="grid h-7 w-7 place-items-center rounded-full bg-[var(--accent-dim)] text-[10.5px] font-bold text-[var(--accent)]">
                    {row.user_id.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[var(--text-1)]">
                    {row.user_id}
                  </span>
                  <span className="tabular text-[var(--text-2)]">{row.runs} runs</span>
                  <span className="tabular text-[var(--text-2)]">{fmtK(row.total_tokens)} tok</span>
                  <span className="tabular font-semibold text-[var(--text-1)]">
                    ${row.cost_usd.toFixed(2)}
                  </span>
                </div>
              ))}
          </div>
        </section>
      )}
    </div>
  );
}

export default function UsagePage() {
  return (
    <AuthProvider>
      <AppShell>
        <UsageInner />
      </AppShell>
    </AuthProvider>
  );
}
