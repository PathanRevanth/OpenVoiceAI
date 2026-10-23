"use client";

/**
 * Settings: identity, governance policies, models, MCP registry.
 * Plain hairline-divided sections; interactive controls only where needed.
 */

import { useCallback, useEffect, useState } from "react";
import { AuthProvider, useAuthValue } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { Chip, CopyField, PageHeader, SectionHead, Toggle } from "@/components/ui";
import { Icon } from "@/components/icons";
import { backend, type EventSubscription, type GithubOAuthStatus, type PlatformInfo } from "@/lib/backend";

function SettingsInner() {
  const { user } = useAuthValue();
  const [platform, setPlatform] = useState<PlatformInfo | null>(null);
  const [mcp, setMcp] = useState<Awaited<ReturnType<typeof backend.mcp>>>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [form, setForm] = useState({ requireApproval: true, minRole: "ops" });
  const [mcpForm, setMcpForm] = useState({
    name: "",
    url: "",
    transport: "streamable-http" as "streamable-http" | "sse" | "stdio",
  });
  const [mcpBusy, setMcpBusy] = useState(false);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [notifyUrl, setNotifyUrl] = useState("");
  const [notifySaved, setNotifySaved] = useState(false);
  const [notifyTest, setNotifyTest] = useState<string | null>(null);
  const [tokens, setTokens] = useState<
    { id: string; label: string; role: string; key_last4: string; revoked: boolean; last_used_at: string | null }[]
  >([]);
  const [tokenLabel, setTokenLabel] = useState("");
  const [freshToken, setFreshToken] = useState<string | null>(null);
  const [github, setGithub] = useState<GithubOAuthStatus | null>(null);
  const [githubBusy, setGithubBusy] = useState(false);
  const [githubError, setGithubError] = useState<string | null>(null);

  const isAdmin = (user?.role ?? "viewer") === "admin";

  const refresh = useCallback(async () => {
    const [p, m, n, tk, gh] = await Promise.all([
      backend.platform(),
      backend.mcp(),
      backend.getNotifications(),
      backend.userTokens(),
      backend.githubOauthStatus().catch(() => null),
    ]);
    setTokens(tk);
    setNotifyUrl(n.approval_webhook_url ?? "");
    setPlatform(p);
    setMcp(m);
    if (gh) setGithub(gh);
    setForm({
      requireApproval: p.policies.require_approval_for_mutations,
      minRole: p.policies.mutating_min_role,
    });
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  const savePolicies = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await backend.updatePolicies({
        require_approval_for_mutations: form.requireApproval,
        mutating_min_role: form.minRole,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const saveNotify = async () => {
    setSaving(true);
    try {
      await backend.setNotifications(notifyUrl.trim() || null);
      setNotifySaved(true);
      setTimeout(() => setNotifySaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const testNotify = async () => {
    setNotifyTest(null);
    const res = await backend.testNotifications();
    setNotifyTest(res.ok ? "delivered" : "failed: " + (res.detail ?? res.status_code));
    setTimeout(() => setNotifyTest(null), 4000);
  };

  const createToken = async () => {
    setSaving(true);
    try {
      const created = await backend.createUserToken(tokenLabel.trim() || "default");
      setFreshToken(created.key);
      setTokenLabel("");
      setTokens(await backend.userTokens());
    } finally {
      setSaving(false);
    }
  };

  const revokeToken = async (id: string) => {
    setSaving(true);
    try {
      await backend.revokeUserToken(id);
      setTokens(await backend.userTokens());
    } finally {
      setSaving(false);
    }
  };

  const connectGithub = async () => {
    setGithubBusy(true);
    setGithubError(null);
    try {
      const { authorize_url } = await backend.githubOauthAuthorizeUrl();
      window.location.href = authorize_url;
    } catch (err) {
      setGithubError(err instanceof Error ? err.message : "Could not start GitHub connect");
      setGithubBusy(false);
    }
  };

  const disconnectGithub = async () => {
    setGithubBusy(true);
    try {
      await backend.githubOauthDisconnect();
      setGithub(await backend.githubOauthStatus());
    } finally {
      setGithubBusy(false);
    }
  };

  const addMcp = async () => {
    if (!mcpForm.name.trim()) return;
    setMcpBusy(true);
    setMcpError(null);
    try {
      await backend.addMcpServer({
        name: mcpForm.name.trim(),
        transport: mcpForm.transport,
        url: mcpForm.url.trim() || undefined,
        status: "enabled",
      });
      setMcpForm({ name: "", url: "", transport: "streamable-http" });
      await refresh();
    } catch (err) {
      setMcpError(err instanceof Error ? err.message : "Failed to register server");
    } finally {
      setMcpBusy(false);
    }
  };

  const policies = platform?.policies;

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 lg:px-10">
      <PageHeader
        title="Settings"
        description="Identity, governance policies, model configuration, and MCP registry. Policy changes apply immediately to every new run."
      />

      <section className=" mt-10 border-y border-[var(--hairline)] py-6">
        <SectionHead>Profile</SectionHead>
        <div className="mt-5 flex items-center gap-4">
          <span className="grid h-12 w-12 place-items-center rounded-full bg-[var(--accent-dim)] text-[16px] font-bold text-[var(--accent)]">
            {(user?.name ?? "?").slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[14px] font-semibold text-[var(--text-1)]">{user?.name}</p>
            <p className="truncate text-[12px] text-[var(--text-3)]">{user?.email}</p>
          </div>
          <Chip tone="accent">{user?.role ?? "viewer"}</Chip>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-6 border-t border-[var(--hairline)] pt-4 text-[12.5px]">
          <div>
            <p className="text-[12px] font-semibold font-medium text-[var(--text-3)]">Tenant</p>
            <p className="mt-1 font-mono text-[var(--text-1)]">{platform?.identity.tenant_id ?? "not set"}</p>
          </div>
          <div>
            <p className="text-[12px] font-semibold font-medium text-[var(--text-3)]">Auth</p>
            <p className="mt-1 text-[var(--text-1)]">
              {platform?.auth_mode === "betterauth" ? "Better Auth (JWT via JWKS)" : "Dev mode"}
            </p>
          </div>
        </div>
      </section>

      <section className=" mt-12">
        <SectionHead>Integrations</SectionHead>
        <div className="mt-2 divide-y divide-[var(--hairline)] border-b border-[var(--hairline)]">
          <div className="flex items-center justify-between gap-6 py-5">
            <div>
              <p className="flex items-center gap-2 text-[13.5px] font-medium text-[var(--text-1)]">
                GitHub agent
                {github?.connected ? (
                  <Chip tone="ok">connected{github.login ? `: ${github.login}` : ""}</Chip>
                ) : (
                  <Chip>not connected</Chip>
                )}
              </p>
              <p className="mt-0.5 max-w-md text-[12px] leading-relaxed text-[var(--text-3)]">
                Authorize the platform&apos;s GitHub OAuth app so the GitHub specialist can act as
                you  -  including approved writes like commenting on issues and PRs.
              </p>
              {githubError && (
                <p className="mt-1.5 text-[11.5px] font-medium text-[var(--danger)]">{githubError}</p>
              )}
            </div>
            {github?.connected ? (
              <button
                onClick={() => void disconnectGithub()}
                disabled={githubBusy || !isAdmin}
                title={isAdmin ? "Disconnect GitHub" : "Admin role required"}
                className="press fluid shrink-0 rounded-lg border border-[var(--hairline-strong)] px-3 py-1.5 text-[11.5px] font-semibold text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-40"
              >
                Disconnect
              </button>
            ) : (
              <button
                onClick={() => void connectGithub()}
                disabled={githubBusy || !isAdmin || (github !== null && !github.configured)}
                title={isAdmin ? "Connect GitHub" : "Admin role required"}
                className="press fluid flex shrink-0 items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 py-1.5 text-[11.5px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-40"
              >
                <Icon name="external" size={12} /> Connect GitHub
              </button>
            )}
          </div>
        </div>
      </section>

      <section className=" mt-12">
        <SectionHead>Governance policies</SectionHead>
        <div className="mt-2 divide-y divide-[var(--hairline)] border-b border-[var(--hairline)]">
          <div className="flex items-center justify-between gap-6 py-5">
            <div>
              <p className="text-[13.5px] font-medium text-[var(--text-1)]">
                Require approval for mutations
              </p>
              <p className="mt-0.5 max-w-md text-[12px] leading-relaxed text-[var(--text-3)]">
                Every mutating tool call pauses for a human decision before executing.
              </p>
            </div>
            <Toggle
              checked={form.requireApproval}
              onChange={(v) => isAdmin && setForm((f) => ({ ...f, requireApproval: v }))}
              disabled={!isAdmin}
              label="Require approval for mutations"
            />
          </div>
          <div className="flex items-center justify-between gap-6 py-5">
            <div>
              <p className="text-[13.5px] font-medium text-[var(--text-1)]">
                Minimum role for mutations
              </p>
              <p className="mt-0.5 max-w-md text-[12px] leading-relaxed text-[var(--text-3)]">
                Roles below this are blocked from mutating tools entirely. Destructive always needs admin.
              </p>
            </div>
            <div className="flex rounded-xl border border-[var(--hairline)] bg-[var(--panel-hover)] p-1">
              {(["viewer", "ops", "admin"] as const).map((r) => (
                <button
                  key={r}
                  disabled={!isAdmin}
                  onClick={() => setForm((f) => ({ ...f, minRole: r }))}
                  className={`fluid rounded-lg px-2.5 py-1 text-[11.5px] font-medium capitalize disabled:opacity-40 ${
                    form.minRole === r
                      ? "bg-[var(--bg-raised)] text-[var(--text-1)] shadow-[0_1px_2px_rgba(22,23,28,0.08)]"
                      : "text-[var(--text-3)]"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-4 flex items-center justify-between">
          <p className="text-[11.5px] text-[var(--text-3)]">
            {isAdmin
              ? "Applies instantly. Env vars remain the source of truth after a restart."
              : "Only admins can change platform policies."}
          </p>
          {isAdmin && (
            <button
              onClick={() => void savePolicies()}
              disabled={saving}
              className="press fluid flex items-center gap-2 rounded-xl bg-[var(--accent)] px-4 py-2 text-[12.5px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
            >
              {saved ? (
                <>
                  <Icon name="check" size={13} strokeWidth={2} /> Saved
                </>
              ) : (
                "Save policies"
              )}
            </button>
          )}
        </div>
      </section>

      <section className=" mt-12">
        <SectionHead>Models</SectionHead>
        <div className="mt-2 divide-y divide-[var(--hairline)] border-b border-[var(--hairline)]">
          <div className="flex items-center justify-between py-4">
            <div>
              <p className="text-[13px] font-medium text-[var(--text-1)]">Chat model</p>
              <p className="font-mono text-[11px] text-[var(--text-3)]">{platform?.model ?? "unset"}</p>
            </div>
            <Chip tone={platform?.demo_mode ? "amber" : "ok"} dot pulse={platform?.demo_mode}>
              {platform?.demo_mode ? "demo" : "live"}
            </Chip>
          </div>
          <div className="flex items-center justify-between py-4">
            <div>
              <p className="text-[13px] font-medium text-[var(--text-1)]">Realtime voice model</p>
              <p className="font-mono text-[11px] text-[var(--text-3)]">{platform?.realtime_model ?? "unset"}</p>
            </div>
            <Chip tone={platform?.realtime_available ? "ok" : "neutral"} dot>
              {platform?.realtime_available ? "available" : "not configured"}
            </Chip>
          </div>
          <div className="flex items-center justify-between py-4">
            <div>
              <p className="text-[13px] font-medium text-[var(--text-1)]">Budgets</p>
              <p className="text-[11.5px] text-[var(--text-3)]">
                ${policies?.daily_spend_limit_usd ?? 0} daily cap,{" "}
                {(policies?.per_run_token_limit ?? 0).toLocaleString()} tokens per run
              </p>
            </div>
            <Chip tone="neutral">harness spend limits</Chip>
          </div>
        </div>
        <p className="mt-3 text-[11.5px] text-[var(--text-3)]">
          Per-user overrides live in LLM Connections. Platform defaults come from
          VOICEFIRST_DEFAULT_MODEL and VOICEFIRST_REALTIME_MODEL in backend/.env.
        </p>
      </section>

      {/* API tokens */}
      <section className=" mt-12">
        <SectionHead>API tokens</SectionHead>
        <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--text-3)]">
          Personal tokens (vf_pat_...) authenticate platform API calls from scripts, the
          same as your browser session. Shown once at creation, stored hashed.
        </p>
        <div className="mt-3 flex gap-2">
          <input
            value={tokenLabel}
            onChange={(e) => setTokenLabel(e.target.value)}
            placeholder="Label (e.g. deploy script)"
            className="fluid h-9 min-w-0 flex-1 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
          />
          <button
            onClick={() => void createToken()}
            disabled={saving}
            className="press fluid rounded-xl bg-[var(--accent)] px-3.5 py-2 text-[12px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
          >
            {saving ? "Creating" : "Create token"}
          </button>
        </div>
        {freshToken && (
          <div className="mt-3 rounded-xl border border-[var(--accent-border)] bg-[var(--accent-dim)] p-3.5">
            <p className="text-[12px] font-bold font-medium text-[var(--accent)]">
              New token, copy it now
            </p>
            <div className="mt-2">
              <CopyField value={freshToken} />
            </div>
          </div>
        )}
        <div className="mt-4 divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)]">
          {tokens.length === 0 && (
            <p className="px-4 py-4 text-[12px] text-[var(--text-3)]">No personal tokens.</p>
          )}
          {tokens.map((t) => (
            <div key={t.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-[12.5px] font-medium text-[var(--text-1)]">
                  {t.label}{" "}
                  <span className="font-mono text-[10.5px] text-[var(--text-3)]">{t.key_last4}</span>
                </p>
                <p className="text-[10.5px] text-[var(--text-3)]">
                  {t.role}
                  {t.last_used_at
                    ? `, last used ${new Date(t.last_used_at).toLocaleDateString()}`
                    : ", never used"}
                </p>
              </div>
              {!t.revoked && (
                <button
                  onClick={() => void revokeToken(t.id)}
                  disabled={saving}
                  className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-50"
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
        <p className="mt-2 break-all font-mono text-[10.5px] text-[var(--text-3)]">
          {
            'curl -H "Authorization: Bearer vf_pat_..." {API_URL}/api/agents/github/invoke -d \'{"message":"..."}\''
          }
        </p>
      </section>

      <EventSubscriptionsSection />

      <section className=" mb-8 mt-12">
        <SectionHead>MCP registry</SectionHead>
        <div className="mt-2 divide-y divide-[var(--hairline)] border-b border-[var(--hairline)]">
          {mcp.map((m) => (
            <div key={m.slug} className="flex items-center justify-between py-3.5">
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium text-[var(--text-1)]">{m.name}</p>
                <p className="truncate font-mono text-[10.5px] text-[var(--text-3)]">
                  {m.url ?? m.command ?? m.transport}
                </p>
              </div>
              <Chip tone={m.status === "enabled" ? "ok" : "neutral"} dot>
                {m.status}
              </Chip>
            </div>
          ))}
          {mcp.length === 0 && (
            <p className="py-5 text-[12.5px] text-[var(--text-3)]">No MCP servers registered.</p>
          )}
        </div>

        {isAdmin && (
          <div className="mt-5 rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-4">
            <p className="text-[12.5px] font-semibold text-[var(--text-1)]">Register a server</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                value={mcpForm.name}
                onChange={(e) => setMcpForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Name (e.g. Notion MCP)"
                className="fluid h-9 min-w-0 flex-1 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              />
              <select
                value={mcpForm.transport}
                onChange={(e) =>
                  setMcpForm((f) => ({ ...f, transport: e.target.value as typeof f.transport }))
                }
                className="fluid h-9 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-2.5 text-[12.5px] text-[var(--text-1)] outline-none hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              >
                <option value="streamable-http">streamable-http</option>
                <option value="sse">sse</option>
                <option value="stdio">stdio</option>
              </select>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <input
                value={mcpForm.url}
                onChange={(e) => setMcpForm((f) => ({ ...f, url: e.target.value }))}
                placeholder="https://mcp.example.com/mcp (or command for stdio)"
                className="fluid h-9 min-w-0 flex-1 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 font-mono text-[12px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
              />
              <button
                onClick={() => void addMcp()}
                disabled={mcpBusy || !mcpForm.name.trim()}
                className="press fluid rounded-xl bg-[var(--solid)] px-4 py-2 text-[12px] font-semibold text-[var(--on-solid)] hover:opacity-90 disabled:opacity-40"
              >
                {mcpBusy ? "Adding" : "Add server"}
              </button>
            </div>
            {mcpError && <p className="mt-2 text-[11.5px] text-[var(--danger)]">{mcpError}</p>}
          </div>
        )}
      </section>
    </div>
  );
}

/** Outbound event subscriptions: signed webhook fan-out with retry + dead-letter. */
function EventSubscriptionsSection() {
  const [subs, setSubs] = useState<EventSubscription[]>([]);
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState("*");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSubs(await backend.eventSubscriptions());
    } catch {
      /* signed out */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = async () => {
    if (!url.trim().startsWith("http")) return;
    setBusy(true);
    try {
      await backend.createEventSubscription({
        url: url.trim(),
        events: events.split(",").map((e) => e.trim()).filter(Boolean),
        secret: secret.trim() || undefined,
      });
      setUrl("");
      setSecret("");
      setEvents("*");
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const test = async (id: string) => {
    setTestResult("Testing…");
    try {
      const r = await backend.testEventSubscription(id);
      setTestResult(r.ok ? `Delivered (HTTP ${r.status_code})` : `Failed (HTTP ${r.status_code ?? "network"})`);
    } catch (e) {
      setTestResult(e instanceof Error ? e.message : "Failed");
    }
  };

  return (
    <section className=" mb-8 mt-12">
      <SectionHead>Event subscriptions</SectionHead>
      <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--text-3)]">
        Push platform events (runs, approvals, budgets, voice sessions) to your webhook. Deliveries are
        HMAC-signed, retried on failure, and dead-lettered after repeated consecutive failures.
        Patterns like <span className="font-mono">run.*</span> or <span className="font-mono">*</span> select events.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://your-service.example.com/hooks/voicefirst"
          className="fluid h-9 min-w-0 flex-[2] rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
        />
        <input
          value={events}
          onChange={(e) => setEvents(e.target.value)}
          placeholder="events (run.*, approval.*)"
          className="fluid h-9 min-w-0 flex-1 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 font-mono text-[12px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
        />
        <input
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder="signing secret (optional)"
          className="fluid h-9 min-w-0 flex-1 rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
        />
        <button
          onClick={() => void create()}
          disabled={busy || !url.trim()}
          className="press fluid rounded-xl bg-[var(--accent)] px-3.5 py-2 text-[12px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
        >
          Subscribe
        </button>
      </div>
      <div className="mt-4 divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)]">
        {subs.length === 0 && (
          <p className="px-4 py-4 text-[12px] text-[var(--text-3)]">No event subscriptions yet.</p>
        )}
        {subs.map((s) => (
          <div key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-[12.5px] font-medium text-[var(--text-1)]">{s.url}</p>
              <p className="mt-0.5 text-[10.5px] text-[var(--text-3)]">
                <span className="font-mono">{s.events.join(", ")}</span>
                {s.has_secret && " · signed"}
                {` · ${s.deliveries_total} deliveries`}
                {s.last_status ? `, last: ${s.last_status}` : ""}
              </p>
            </div>
            {s.dead_lettered && (
              <span className="rounded-full bg-[var(--warn-dim)] px-2 py-0.5 text-[10px] font-bold text-[var(--warn)]">
                dead-lettered
              </span>
            )}
            {!s.enabled && !s.dead_lettered && (
              <span className="rounded-full bg-[var(--panel-hover)] px-2 py-0.5 text-[10px] font-semibold text-[var(--text-3)]">
                paused
              </span>
            )}
            <button onClick={() => void test(s.id)} className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)]">
              Test
            </button>
            <button
              onClick={() => void backend.setEventSubscriptionEnabled(s.id, !s.enabled).then(refresh)}
              className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)]"
            >
              {s.enabled ? "Pause" : "Resume"}
            </button>
            <button
              onClick={() => void backend.deleteEventSubscription(s.id).then(refresh)}
              className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--danger)] hover:border-[var(--danger)]"
            >
              Delete
            </button>
          </div>
        ))}
      </div>
      {testResult && <p className="mt-2 text-[11px] text-[var(--text-3)]">{testResult}</p>}
    </section>
  );
}

export default function SettingsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <SettingsInner />
      </AppShell>
    </AuthProvider>
  );
}
