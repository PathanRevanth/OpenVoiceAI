"use client";

/**
 * Agents: the fleet as a data table.
 *
 * Columns: agent (icon, name, slug, description), status, tools (skills +
 * MCP), active webhook keys, and row actions (access, enable/disable).
 * Client-side filtering and column sorting. The Access drawer manages the
 * per-agent webhook keys, skill editor, and red-team scan.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AuthProvider, useAuthValue } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import {
  Chip,
  CopyField,
  Drawer,
  PageHeader,
  SectionHead,
  Toggle,
} from "@/components/ui";
import { Icon } from "@/components/icons";
import { AgentLogo, agentMark } from "@/components/provider-logo";
import {
  backend,
  type AgentAccessInfo,
  type AgentInfo,
  type AgentKeyView,
  type PlatformInfo,
} from "@/lib/backend";

type SortKey = "name" | "mcp" | "active" | "keys";
type SortDir = "asc" | "desc";

type McpState = "connected" | "configured" | "disabled" | "builtin";

function mcpStateFor(a: AgentInfo, mcpList: { slug: string; status: string; health: string }[]): McpState {
  if (a.mcp_servers.length === 0) return "builtin";
  const records = a.mcp_servers
    .map((slug) => mcpList.find((m) => m.slug === slug))
    .filter((m): m is { slug: string; status: string; health: string } => Boolean(m));
  if (records.length === 0) return "disabled";
  if (records.every((m) => m.status === "enabled" && m.health === "healthy")) return "connected";
  if (records.every((m) => m.status !== "enabled")) return "disabled";
  return "configured";
}

const MCP_CHIP: Record<McpState, { tone: "ok" | "amber" | "danger" | "neutral"; label: string; title: string }> = {
  connected: { tone: "ok", label: "connected", title: "MCP server reachable and enabled" },
  configured: { tone: "amber", label: "not checked", title: "MCP server registered but not yet verified" },
  disabled: { tone: "danger", label: "disabled", title: "The agent's MCP server is disabled or missing" },
  builtin: { tone: "neutral", label: "built-in", title: "This agent uses built-in tools only (no MCP)" },
};

function AgentsInner() {
  const { user } = useAuthValue();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [skills, setSkills] = useState<Awaited<ReturnType<typeof backend.skills>>>([]);
  const [mcp, setMcp] = useState<Awaited<ReturnType<typeof backend.mcp>>>([]);
  const [platform, setPlatform] = useState<PlatformInfo | null>(null);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [accessFor, setAccessFor] = useState<AgentInfo | null>(null);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const isAdmin = (user?.role ?? "viewer") === "admin";

  const refresh = useCallback(async () => {
    const [a, s, m, p] = await Promise.all([
      backend.agents(),
      backend.skills(),
      backend.mcp(),
      backend.platform(),
    ]);
    setAgents(a);
    setSkills(s);
    setMcp(m);
    setPlatform(p);
    return a;
  }, []);

  const checkMcpHealth = useCallback(async (list: Awaited<ReturnType<typeof backend.mcp>>) => {
    const unchecked = list.filter((m) => m.status === "enabled" && (!m.health || m.health === "unknown"));
    await Promise.allSettled(unchecked.map((m) => backend.checkMcp(m.slug)));
    if (unchecked.length > 0) {
      setMcp(await backend.mcp());
    }
  }, []);

  useEffect(() => {
    refresh()
      .then((/* warm */) => undefined)
      .catch(() => {});
  }, [refresh]);

  useEffect(() => {
    if (mcp.length > 0) void checkMcpHealth(mcp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcp.length]);

  const toggleAgent = async (slug: string, status: "enabled" | "disabled") => {
    setBusySlug(slug);
    try {
      await backend.setAgentStatus(slug, status);
      await refresh();
    } finally {
      setBusySlug(null);
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = agents.filter(
      (a) =>
        !q ||
        a.name.toLowerCase().includes(q) ||
        a.slug.includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.skills.some((s) => s.includes(q)),
    );
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (sortKey === "keys") return ((a.keys_active ?? 0) - (b.keys_active ?? 0)) * dir;
      if (sortKey === "active") {
        const rank = (x: AgentInfo) => (x.status === "enabled" ? 0 : 1);
        return (rank(a) - rank(b)) * dir;
      }
      if (sortKey === "mcp") {
        const rank = (x: AgentInfo) => {
          const s = mcpStateFor(x, mcp);
          return { connected: 0, configured: 1, builtin: 2, disabled: 3 }[s];
        };
        return (rank(a) - rank(b)) * dir;
      }
      return a.name.localeCompare(b.name) * dir;
    });
  }, [agents, mcp, query, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const SortHead = ({
    label,
    sort,
    className = "",
  }: {
    label: string;
    sort?: SortKey;
    className?: string;
  }) => (
    <th className={`px-4 py-3 text-left ${className}`}>
      {sort ? (
        <button
          onClick={() => toggleSort(sort)}
          className="fluid inline-flex items-center gap-1 text-[12px] font-semibold font-medium text-[var(--text-3)] hover:text-[var(--text-1)]"
        >
          {label}
          <Icon
            name="chevron"
            size={11}
            className={`transition-transform duration-200 ${
              sortKey === sort ? (sortDir === "asc" ? "rotate-90" : "-rotate-90") : "opacity-0"
            }`}
          />
        </button>
      ) : (
        <span className="text-[12px] font-semibold font-medium text-[var(--text-3)]">
          {label}
        </span>
      )}
    </th>
  );

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Agents"
        description="The fleet at a glance. Every active agent is callable over its own session API, webhook, and public manifest."
        actions={
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter agents, skills…"
            className="fluid h-10 w-56 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
          />
        }
      />

      {/* ------------------------------------------------ data table */}
      <div className=" mt-8 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] shadow-[var(--shadow-card)]">
        <table className="w-full min-w-[860px] border-collapse text-left">
          <thead>
            <tr className="border-b border-[var(--hairline)]">
              <SortHead label="Agent" sort="name" />
              <SortHead label="MCP" sort="mcp" />
              <th className="px-4 py-3 text-left">
                <span className="text-[12px] font-semibold font-medium text-[var(--text-3)]">
                  Skills & tools
                </span>
              </th>
              <th className="w-24 px-4 py-3 text-left">
                <span
                  className="text-[12px] font-semibold font-medium text-[var(--text-3)]"
                  title="Whether the agent can be triggered by the Orchestrator, the session API, or its webhook"
                >
                  Active
                </span>
              </th>
              <SortHead label="Keys" sort="keys" className="w-20" />
              <th className="px-4 py-3 text-right">
                <span className="text-[12px] font-semibold font-medium text-[var(--text-3)]">
                  Actions
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((a) => {
              const enabled = a.status === "enabled";
              const isOrchestrator = a.kind === "orchestrator";
              const visibleSkills = a.skills.slice(0, 2);
              const extraSkills = a.skills.length - visibleSkills.length;
              return (
                <tr
                  key={a.slug}
                  className="fluid border-b border-[var(--hairline)] last:border-0 hover:bg-[var(--hover)]"
                >
                  <td className="px-4 py-4">
                    <div className="flex items-start gap-3.5">
                      {agentMark(a.slug) ? (
                        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--panel-hover)] ring-1 ring-[var(--hairline)]">
                          <AgentLogo slug={a.slug} size={22} />
                        </span>
                      ) : (
                      <span
                        className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${
                          isOrchestrator ? "bg-[var(--accent)]" : "bg-[var(--accent-dim)]"
                        }`}
                      >
                        <Icon
                          name={isOrchestrator ? "spark" : "wrench"}
                          size={17}
                          className={isOrchestrator ? "text-[var(--accent-contrast)]" : "text-[var(--accent)]"}
                        />
                      </span>
                      )}
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[14.5px] font-semibold tracking-tight text-[var(--text-1)]">
                            {a.name}
                          </span>
                          {isOrchestrator && <Chip tone="accent">routes all work</Chip>}
                        </div>
                        <p className="mt-0.5 line-clamp-1 max-w-md text-[12px] leading-relaxed text-[var(--text-3)]">
                          {a.description}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    {(() => {
                      const state = mcpStateFor(a, mcp);
                      const meta = MCP_CHIP[state];
                      return (
                        <span title={meta.title}>
                          <Chip tone={meta.tone} dot pulse={state === "configured"}>
                            {meta.label}
                          </Chip>
                        </span>
                      );
                    })()}
                  </td>
                  <td className="px-4 py-4">
                    <div className="flex max-w-[260px] flex-wrap gap-1">
                      {visibleSkills.map((s) => (
                        <span
                          key={s}
                          className="rounded-md bg-[var(--panel-hover)] px-1.5 py-0.5 font-mono text-[9.5px] text-[var(--text-2)]"
                        >
                          skill:{s}
                        </span>
                      ))}
                      {extraSkills > 0 && (
                        <span className="rounded-md bg-[var(--panel-hover)] px-1.5 py-0.5 font-mono text-[9.5px] text-[var(--text-3)]">
                          +{extraSkills}
                        </span>
                      )}
                      {a.mcp_servers.map((s) => (
                        <span
                          key={s}
                          className="rounded-md bg-[var(--accent-dim)] px-1.5 py-0.5 font-mono text-[9.5px] text-[var(--accent)]"
                        >
                          mcp:{s}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    <Toggle
                      checked={enabled}
                      onChange={(v) => (isAdmin ? toggleAgent(a.slug, v ? "enabled" : "disabled") : undefined)}
                      disabled={!isAdmin || busySlug === a.slug}
                      label={`Trigger ${a.name} via Orchestrator, API, or Webhook`}
                    />
                  </td>
                  <td className="px-4 py-4">
                    <span className="tabular text-[13px] font-semibold text-[var(--text-1)]">
                      {a.keys_active ?? 0}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => setAccessFor(a)}
                        disabled={!enabled}
                        title={enabled ? "API & webhook access" : "Enable the agent first"}
                        className="press fluid flex items-center gap-1.5 rounded-lg bg-[var(--text-1)] px-3 py-1.5 text-[11.5px] font-semibold text-[var(--accent-contrast)] hover:opacity-90 disabled:opacity-40"
                      >
                        <Icon name="plug" size={12} /> Access
                      </button>

                    </div>
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-12 text-center text-[12.5px] text-[var(--text-3)]">
                  {agents.length === 0
                    ? "No agents registered yet."
                    : "No agents match this filter."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ------------------------------------------------ registry sections */}
      <section className=" mt-12">
        <SectionHead>Skills</SectionHead>
        <p className="mt-1 text-[12.5px] text-[var(--text-3)]">
          Behavioral playbooks in SKILL.md, loaded on demand.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {skills.map((s) => (
            <span
              key={s.slug}
              title={s.description}
              className="fluid rounded-full border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-1.5 font-mono text-[11px] text-[var(--text-2)] hover:border-[var(--hairline-strong)]"
            >
              {s.name}
            </span>
          ))}
        </div>
      </section>

      <section className=" mb-8 mt-12">
        <SectionHead>MCP connections</SectionHead>
        <div className="mt-4 divide-y divide-[var(--hairline)] border-y border-[var(--hairline)]">
          {mcp.map((m) => (
            <div key={m.slug} className="flex items-center justify-between py-3.5">
              <div className="flex min-w-0 items-center gap-3">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--panel-hover)]">
                  <Icon name="plug" size={14} className="text-[var(--text-2)]" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-[var(--text-1)]">{m.name}</p>
                  {m.url && (
                    <p className="truncate font-mono text-[10.5px] text-[var(--text-3)]">{m.url}</p>
                  )}
                </div>
              </div>
              <Chip tone={m.status === "enabled" ? "ok" : "neutral"} dot>
                {m.status}
              </Chip>
            </div>
          ))}
          {mcp.length === 0 && (
            <p className="py-5 text-[12.5px] text-[var(--text-3)]">
              No MCP servers registered. Add one in Settings.
            </p>
          )}
        </div>
      </section>

      <AccessDrawer agent={accessFor} onClose={() => setAccessFor(null)} onKeysChanged={refresh} />
    </div>
  );
}

/** Per-agent API access: webhook keys, skill editor, and red-team scan. */
function AccessDrawer({
  agent,
  onClose,
  onKeysChanged,
}: {
  agent: AgentInfo | null;
  onClose: () => void;
  onKeysChanged: () => void;
}) {
  const [access, setAccess] = useState<(AgentAccessInfo & { keys: AgentKeyView[] }) | null>(null);
  const [label, setLabel] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [skill, setSkill] = useState<string | null>(null);
  const [skillSaved, setSkillSaved] = useState(false);
  const [redTeam, setRedTeam] = useState<Awaited<ReturnType<typeof backend.redTeam>> | null>(null);
  const [rtBusy, setRtBusy] = useState(false);

  useEffect(() => {
    if (agent) {
      setAccess(null);
      setFreshKey(null);
      setSkill(null);
      setRedTeam(null);
      backend
        .agentKeys(agent.slug)
        .then(setAccess)
        .catch(() => setAccess(null));
      backend
        .getSkill(agent.slug)
        .then((r) => setSkill(r.content))
        .catch(() => setSkill(null));
    }
  }, [agent]);

  const reload = useCallback(() => {
    if (!agent) return;
    backend
      .agentKeys(agent.slug)
      .then(setAccess)
      .catch(() => {});
    onKeysChanged();
  }, [agent, onKeysChanged]);

  const createKey = async () => {
    if (!agent) return;
    setBusy("create");
    try {
      const created = await backend.createAgentKey(agent.slug, label.trim() || "default");
      setFreshKey(created.key);
      setLabel("");
      reload();
    } finally {
      setBusy(null);
    }
  };

  const revoke = async (keyId: string) => {
    if (!agent) return;
    setBusy(keyId);
    try {
      await backend.revokeAgentKey(agent.slug, keyId);
      reload();
    } finally {
      setBusy(null);
    }
  };

  const saveSkill = async () => {
    if (!agent || skill === null) return;
    setBusy("skill");
    try {
      await backend.saveSkill(agent.slug, skill);
      setSkillSaved(true);
      setTimeout(() => setSkillSaved(false), 2500);
    } finally {
      setBusy(null);
    }
  };

  const scanRedTeam = async () => {
    if (!agent) return;
    setRtBusy(true);
    try {
      setRedTeam(await backend.redTeam(agent.slug));
    } finally {
      setRtBusy(false);
    }
  };

  return (
    <Drawer
      open={agent !== null}
      onClose={onClose}
      width={600}
      title={
        agent && (
          <div className="flex items-center gap-2.5">
            <span className="text-[14px] font-semibold text-[var(--text-1)]">
              {agent.name}
            </span>
            <span className="font-mono text-[10.5px] text-[var(--text-3)]">/{agent.slug}</span>
          </div>
        )
      }
    >
      {agent && (
        <div className="space-y-8 p-5">
          <section>
            <h3 className="display-2">Endpoints</h3>
            <div className="mt-4 space-y-2.5">
              <div>
                <p className="mb-1.5 text-[11px] font-semibold font-medium text-[var(--text-3)]">
                  Invoke (your session)
                </p>
                <CopyField
                  value={access?.invoke_url ?? `${location.origin}/api/agents/${agent.slug}/invoke`}
                />
              </div>
              <div>
                <p className="mb-1.5 text-[11px] font-semibold font-medium text-[var(--text-3)]">
                  Webhook (external systems, agent key)
                </p>
                <CopyField
                  value={
                    access?.webhook_url ?? `${location.origin}/api/webhooks/agents/${agent.slug}`
                  }
                />
              </div>
              <div>
                <p className="mb-1.5 text-[11px] font-semibold font-medium text-[var(--text-3)]">
                  Manifest (public agent card)
                </p>
                <CopyField
                  value={
                    access?.manifest_url ?? `${location.origin}/api/agents/${agent.slug}/manifest`
                  }
                />
              </div>
            </div>
            <div className="mt-4 rounded-xl bg-[var(--panel-hover)] p-3.5">
              <p className="mb-2 text-[12px] font-semibold font-medium text-[var(--text-3)]">
                Quick start
              </p>
              <pre className="overflow-x-auto font-mono text-[10.5px] leading-relaxed text-[var(--text-2)]">
                {buildCurl(agent.slug, access?.webhook_url)}
              </pre>
            </div>
          </section>

          <section>
            <h3 className="display-2">Webhook keys</h3>
            <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--text-3)]">
              Shown once at creation, stored hashed. Revoke immediately on doubt.
            </p>
            {freshKey && (
              <div className="mt-3 rounded-xl border border-[var(--accent-border)] bg-[var(--accent-dim)] p-3.5">
                <p className="text-[12px] font-bold font-medium text-[var(--accent)]">
                  New key, copy it now
                </p>
                <div className="mt-2">
                  <CopyField value={freshKey} />
                </div>
              </div>
            )}
            <div className="mt-3 flex gap-2">
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label (e.g. n8n pipeline)"
                className="fluid h-9 min-w-0 flex-1 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              />
              <button
                onClick={() => void createKey()}
                disabled={busy === "create"}
                className="press fluid rounded-xl bg-[var(--accent)] px-3.5 py-2 text-[12px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
              >
                {busy === "create" ? "Creating" : "Create key"}
              </button>
            </div>
            <div className="mt-3 divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)]">
              {(access?.keys ?? []).length === 0 && (
                <p className="px-4 py-4 text-[12px] text-[var(--text-3)]">No keys yet.</p>
              )}
              {(access?.keys ?? []).map((k) => (
                <div key={k.id} className="flex items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[12.5px] font-medium text-[var(--text-1)]">
                      {k.label}{" "}
                      <span className="font-mono text-[10.5px] text-[var(--text-3)]">
                        {k.key_last4}
                      </span>
                    </p>
                    <p className="text-[10.5px] text-[var(--text-3)]">
                      {k.revoked
                        ? "revoked"
                        : k.last_used_at
                          ? `last used ${new Date(k.last_used_at).toLocaleDateString()}`
                          : "never used"}
                    </p>
                  </div>
                  {!k.revoked && (
                    <button
                      onClick={() => revoke(k.id)}
                      disabled={busy === k.id}
                      className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-50"
                    >
                      Revoke
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section>
            <h3 className="display-2">Behavior (SKILL.md)</h3>
            <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--text-3)]">
              The agent's instructions. Saving writes the file, versions it, and rebuilds
              the live fleet. Admins only.
            </p>
            <textarea
              value={skill ?? ""}
              onChange={(e) => setSkill(e.target.value)}
              rows={10}
              spellCheck={false}
              className="fluid mt-3 w-full resize-y rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3.5 py-3 font-mono text-[11.5px] leading-relaxed outline-none hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            />
            <div className="mt-2 flex items-center justify-between">
              <button
                onClick={() => void scanRedTeam()}
                disabled={rtBusy}
                className="press fluid flex items-center gap-1.5 rounded-lg border border-[var(--hairline)] px-3 py-2 text-[11.5px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)] disabled:opacity-50"
              >
                <Icon name="shield" size={12} /> {rtBusy ? "Scanning…" : "Red-team scan"}
              </button>
              <button
                onClick={() => void saveSkill()}
                disabled={busy === "skill" || skill === null}
                className="press fluid flex items-center gap-2 rounded-xl bg-[var(--accent)] px-4 py-2 text-[12px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
              >
                <Icon name="check" size={13} strokeWidth={2} />
                {busy === "skill" ? "Saving…" : skillSaved ? "Saved & applied" : "Save & apply"}
              </button>
            </div>
            {redTeam && (
              <div className="mt-3 rounded-xl border border-[var(--hairline)] p-3.5">
                <div className="flex items-center gap-2">
                  <Chip
                    tone={redTeam.resilience >= 0.9 ? "ok" : redTeam.resilience >= 0.7 ? "amber" : "danger"}
                  >
                    resilience {Math.round(redTeam.resilience * 100)}%
                  </Chip>
                  <span className="tabular text-[11px] text-[var(--text-3)]">
                    {redTeam.passed}/{redTeam.total} attacks resisted
                  </span>
                </div>
                <div className="mt-2 max-h-48 space-y-1.5 overflow-y-auto">
                  {redTeam.results.map((r) => (
                    <div key={r.id} className="flex items-center gap-2 text-[11px]">
                      <Icon
                        name={r.passed ? "check" : "x"}
                        size={11}
                        strokeWidth={2}
                        className={r.passed ? "text-[var(--ok)]" : "text-[var(--danger)]"}
                      />
                      <span className="w-32 shrink-0 text-[var(--text-3)]">{r.category}</span>
                      <span className="min-w-0 flex-1 truncate text-[var(--text-2)]">{r.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

        </div>
      )}
    </Drawer>
  );
}

function buildCurl(slug: string, webhookUrl?: string): string {
  const url = webhookUrl ?? location.origin + "/api/webhooks/agents/" + slug;
  const q = '"';
  return [
    "curl -X POST " + url + " " + q + "\\",
    "  -H " + q + "Authorization: Bearer vf_ak_..." + q + " " + q + "\\",
    "  -H " + q + "Content-Type: application/json" + q + " " + q + "\\",
    "  -d '" + q + "message" + q + ": " + q + "List open issues" + q + "'",
  ].join("\n");
}

export default function AgentsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <AgentsInner />
      </AppShell>
    </AuthProvider>
  );
}
