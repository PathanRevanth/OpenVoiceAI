"use client";

/**
 * Approvals — the human-in-the-loop queue. A decision list, not a card grid:
 * each row carries the tool, its arguments, risk, age, and the verdict.
 */

import { useCallback, useEffect, useState } from "react";
import { AuthProvider, useAuthValue } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { Chip, EmptyState, PageHeader, SectionHead, timeAgo } from "@/components/ui";
import { Icon } from "@/components/icons";
import { backend } from "@/lib/backend";

function ApprovalsInner() {
  const { user } = useAuthValue();
  const [approvals, setApprovals] = useState<Awaited<ReturnType<typeof backend.approvals>>>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const role = user?.role ?? "viewer";
  const canDecide = role !== "viewer";

  const refresh = useCallback(async () => {
    setApprovals(await backend.approvals());
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
    const t = setInterval(() => refresh().catch(() => {}), 5_000);
    return () => clearInterval(t);
  }, [refresh]);

  const decide = async (id: string, approved: boolean) => {
    setBusy(id);
    try {
      await backend.decideApproval(id, approved);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Approvals"
        description="Mutating tool calls pause their run until someone signs off. Approving resumes the run exactly where it stopped."
      />

      {!canDecide && approvals.length > 0 && (
        <p className="mt-6 rounded-xl border border-[var(--hairline)] bg-[var(--warn-dim)] px-4 py-3 text-[12.5px] text-[var(--warn)]">
          You have the viewer role. Deciding approvals requires ops or admin.
        </p>
      )}

      <div className="mt-8">
        {approvals.length === 0 ? (
          <EmptyState
            icon="shield"
            title="Approval queue is clear"
            body="When an agent needs to touch the outside world, the request lands here first. Nothing mutating runs without a human decision."
          />
        ) : (
          <>
            <SectionHead>
              Pending <span className="tabular text-[var(--text-3)]">({approvals.length})</span>
            </SectionHead>
            <div className="mt-4 divide-y divide-[var(--hairline)] border-y border-[var(--hairline)]">
              {approvals.map((a) => (
                <div key={a.id} className="fluid flex flex-wrap items-center gap-x-5 gap-y-3 py-5 hover:bg-[var(--hover)]">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--warn-dim)]">
                    <Icon name="wrench" size={16} className="text-[var(--warn)]" />
                  </span>
                  <div className="min-w-0 flex-1 basis-72">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate font-mono text-[12.5px] font-semibold text-[var(--text-1)]">
                        {a.tool_name}
                      </p>
                      <Chip tone={a.risk === "destructive" ? "danger" : "amber"}>{a.risk}</Chip>
                    </div>
                    <p className="mt-1 truncate font-mono text-[10.5px] text-[var(--text-3)]">
                      {JSON.stringify(a.args).slice(0, 160)}
                    </p>
                  </div>
                  <span className="tabular shrink-0 text-[11px] text-[var(--text-3)]">
                    {timeAgo(a.created_at)}
                  </span>
                  {canDecide && (
                    <div className="flex shrink-0 gap-1.5">
                      <button
                        onClick={() => decide(a.id, true)}
                        disabled={busy === a.id}
                        className="press fluid flex items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3.5 py-2 text-[12px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
                      >
                        <Icon name="check" size={13} strokeWidth={2} /> Approve
                      </button>
                      <button
                        onClick={() => decide(a.id, false)}
                        disabled={busy === a.id}
                        className="press fluid flex items-center gap-1.5 rounded-lg border border-[var(--hairline-strong)] px-3.5 py-2 text-[12px] font-semibold text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-50"
                      >
                        <Icon name="x" size={13} strokeWidth={2} /> Deny
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <ApprovalsInner />
      </AppShell>
    </AuthProvider>
  );
}
