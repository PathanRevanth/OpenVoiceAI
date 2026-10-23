"use client";

/**
 * Audit log — the tamper-evident trail. Hash chain verification runs on
 * every refresh; a broken chain is surfaced loudly, not hidden.
 */

import { useCallback, useEffect, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { Chip, PageHeader, timeAgo } from "@/components/ui";
import { Icon } from "@/components/icons";
import { backend, type AuditEntry } from "@/lib/backend";

function AuditInner() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [chain, setChain] = useState<{ valid: boolean; broken_at: string | null } | null>(null);
  const [query, setQuery] = useState("");

  const refresh = useCallback(async () => {
    const [e, c] = await Promise.all([backend.audit(200), backend.auditVerify()]);
    setEntries(e);
    setChain(c);
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
    const t = setInterval(() => refresh().catch(() => {}), 15_000);
    return () => clearInterval(t);
  }, [refresh]);

  const filtered = entries.filter(
    (e) =>
      !query ||
      e.action.includes(query.toLowerCase()) ||
      e.actor.toLowerCase().includes(query.toLowerCase()) ||
      e.resource.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Audit log"
        description="Append-only, hash-chained record of every consequential action. Any retroactive mutation breaks the chain and is flagged here."
        actions={
          <div className="flex items-center gap-2">
            {chain && (
              <Chip tone={chain.valid ? "ok" : "danger"} dot pulse={!chain.valid}>
                {chain.valid ? "chain verified" : `broken at ${chain.broken_at}`}
              </Chip>
            )}
            <button
              onClick={() => void backend.exportAudit("csv").catch(() => {})}
              className="press fluid rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 py-2 text-[11.5px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)]"
            >
              Export CSV
            </button>
          </div>
        }
      />

      <div className=" mt-8 flex items-center gap-3">
        <div className="relative flex-1">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by action, actor, or resource…"
            className="fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 text-[13px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
          />
        </div>
        <button
          onClick={() => refresh().catch(() => {})}
          className="press fluid flex items-center gap-2 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-2.5 text-[12.5px] font-medium text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
        >
          <Icon name="refresh" size={14} /> Refresh
        </button>
      </div>

      <div className="board mt-4 max-h-[62vh] overflow-y-auto rounded-2xl">
        <div className="sticky top-0 grid grid-cols-[92px_200px_140px_1fr_72px] gap-3 border-b border-[var(--hairline)] bg-[var(--bg-raised)] px-4 py-2.5">
          {["Time", "Action", "Actor", "Resource", ""].map((h, i) => (
            <span key={i} className="text-[11px] font-medium text-[var(--text-3)]">{h}</span>
          ))}
        </div>
        {filtered.map((e) => (
          <div
            key={e.id}
            className="fluid grid grid-cols-[92px_200px_140px_1fr_72px] items-center gap-3 border-b border-[var(--hairline)] px-4 py-2.5 last:border-0 hover:bg-[var(--panel-hover)]"
          >
            <span className="tabular text-[11px] text-[var(--text-3)]">{timeAgo(e.ts)}</span>
            <span className="truncate font-mono text-[11.5px] font-medium text-[var(--accent)]">{e.action}</span>
            <span className="truncate text-[12px] text-[var(--text-2)]">{e.actor}</span>
            <span className="truncate font-mono text-[11px] text-[var(--text-3)]">{e.resource}</span>
            <button
              onClick={() => navigator.clipboard?.writeText(JSON.stringify(e.detail ?? {})).catch(() => {})}
              className="fluid justify-self-end rounded-md px-1.5 py-1 text-[10px] text-[var(--text-3)] opacity-0 hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)] [div:hover>&]:opacity-100 [tr:hover>&]:opacity-100"
              title="Copy detail JSON"
            >
              <Icon name="external" size={13} />
            </button>
          </div>
        ))}
        {filtered.length === 0 && (
          <p className="px-4 py-12 text-center text-[12.5px] text-[var(--text-3)]">
            {entries.length === 0 ? "No audit entries yet." : "No entries match this filter."}
          </p>
        )}
      </div>
    </div>
  );
}

export default function AuditPage() {
  return (
    <AuthProvider>
      <AppShell>
        <AuditInner />
      </AppShell>
    </AuthProvider>
  );
}
