"use client";

/**
 * LLM Connections. per-user provider key vault.
 *
 * Keys are saved once, tested live against the provider, encrypted at rest
 * (AES, per-user scoped), and never returned to the browser. only the last
 * four characters. "Agents" keys power chat runs; "Voice" keys power the
 * realtime speech-to-speech session.
 */

import { useCallback, useEffect, useState } from "react";
import { AuthProvider } from "@/components/auth-context";
import { AppShell } from "@/components/app-shell";
import { Chip, PageHeader, timeAgo } from "@/components/ui";
import { Icon, type IconName } from "@/components/icons";
import { ProviderTile } from "@/components/provider-logo";
import {
  backend,
  type ConnectionCatalog,
  type ConnectionRecordView,
} from "@/lib/backend";

type Purpose = "agents" | "voice";

const PURPOSE_META: Record<Purpose, { title: string; body: string; icon: IconName }> = {
  agents: {
    title: "Agents",
    body: "Powers chat runs and delegation. Works with OpenAI, Anthropic, and Google.",
    icon: "bot",
  },
  voice: {
    title: "Voice",
    body: "Powers realtime speech-to-speech. OpenAI and Google offer realtime voice APIs.",
    icon: "waveform",
  },
};

const STATUS_CHIP = {
  valid: { tone: "ok" as const, label: "Connected" },
  invalid: { tone: "danger" as const, label: "Invalid key" },
  untested: { tone: "neutral" as const, label: "Untested" },
};

function ConnectionsInner() {
  const [purpose, setPurpose] = useState<Purpose>("agents");
  const [catalog, setCatalog] = useState<ConnectionCatalog | null>(null);
  const [connections, setConnections] = useState<ConnectionRecordView[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const [fallbackModel, setFallbackModel] = useState<string>("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [cat, conns] = await Promise.all([backend.connectionCatalog(), backend.connections()]);
    setCatalog(cat);
    setConnections(conns);
    return conns;
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  const recordsForPurpose = connections.filter((c) => c.purpose === purpose);
  const activeRecord = recordsForPurpose.find((c) => c.is_active && c.status === "valid");
  const providers = Object.entries(catalog?.providers ?? {});
  const supports = (p: string) => catalog?.supports?.[purpose]?.[p] ?? false;

  const selectProvider = (p: string) => {
    setSelectedProvider(p);
    setKey("");
    setError(null);
    const existing = recordsForPurpose.find((c) => c.provider === p);
    const models = catalog?.models?.[purpose]?.[p] ?? [];
    setModel(existing?.model ?? models[0]?.id ?? "");
    setFallbackModel("");
  };

  const save = async () => {
    if (!selectedProvider || !model || key.length < 8) return;
    setBusy("save");
    setError(null);
    try {
      await backend.saveConnection({
        purpose,
        provider: selectedProvider,
        model,
        key,
        fallback_model: fallbackModel || undefined,
      });
      setKey("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message.replace(/^API .*failed: \d+\s*/, "") : "Save failed");
    } finally {
      setBusy(null);
    }
  };

  const test = async (id: string) => {
    setBusy(id);
    try {
      await backend.testConnection(id);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const activate = async (id: string) => {
    setBusy(id);
    try {
      await backend.activateConnection(id);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const remove = async (id: string) => {
    setBusy(id);
    try {
      await backend.deleteConnection(id);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 lg:px-10">
      <PageHeader
        title="LLM Connections"
        description="Bring your own keys. Each key is tested against its provider, encrypted at rest, scoped to your account. and never leaves the server except to the provider."
      />

      {/* security note */}
      <div className=" mt-6 flex items-start gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--accent-dim)] px-4 py-3.5">
        <Icon name="shield" size={16} className="mt-0.5 shrink-0 text-[var(--accent)]" />
        <p className="text-[12px] leading-relaxed text-[var(--text-2)]">
          <b className="text-[var(--text-1)]">Encrypted at rest.</b> Keys are stored with
          AES encryption, mapped to your user, and never sent back to the browser. only
          •••• last-4. Choose separate connections for Agents and Voice.
        </p>
      </div>

      {/* purpose tabs */}
      <div className=" mt-8 grid gap-3 sm:grid-cols-2">
        {(Object.keys(PURPOSE_META) as Purpose[]).map((p) => {
          const meta = PURPOSE_META[p];
          const active = connections.find(
            (c) => c.purpose === p && c.is_active && c.status === "valid",
          );
          const isSelected = purpose === p;
          return (
            <button
              key={p}
              onClick={() => {
                setPurpose(p);
                setSelectedProvider(null);
              }}
              className={`fluid w-full rounded-xl border p-4 text-left ${
                isSelected
                  ? "border-[var(--accent)] bg-[var(--accent-dim)]"
                  : "border-[var(--hairline)] bg-[var(--bg-raised)] hover:border-[var(--hairline-strong)]"
              }`}
            >
              <div className="flex items-center justify-between">
                <span
                  className={`grid h-9 w-9 place-items-center rounded-xl ${
                    isSelected ? "bg-[var(--accent)] text-[var(--accent-contrast)]" : "bg-[var(--panel-hover)] text-[var(--text-2)]"
                  }`}
                >
                  <Icon name={meta.icon} size={16} />
                </span>
                {active ? (
                  <Chip tone="ok" dot>
                    {active.provider}, {active.model}
                  </Chip>
                ) : (
                  <Chip tone="neutral">not configured</Chip>
                )}
              </div>
              <p className="mt-3 text-[14px] font-semibold tracking-tight text-[var(--text-1)]">
                {meta.title}
              </p>
              <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--text-3)]">{meta.body}</p>
            </button>
          );
        })}
      </div>

      {/* provider rows */}
      <div className=" mt-8">
        <p className="text-[11px] font-medium text-[var(--text-3)]">Choose a provider for {PURPOSE_META[purpose].title}</p>
        <div className="mt-3 divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)]">
          {providers.map(([key, meta]) => {
            const supported = supports(key);
            const record = recordsForPurpose.find((c) => c.provider === key);
            const isSel = selectedProvider === key;
            return (
              <button
                key={key}
                disabled={!supported}
                onClick={() => selectProvider(key)}
                className={`fluid relative flex w-full items-center gap-4 px-4 py-3 text-left ${
                  isSel ? "bg-[var(--accent-dim)]" : supported ? "hover:bg-[var(--panel-hover)]" : "opacity-45"
                }`}
              >
                <ProviderTile provider={key} label={meta.label} tile={meta.tile} />
                <span className="text-[13px] font-semibold text-[var(--text-1)]">{meta.label}</span>
                {record && (
                  <span className="ml-auto">
                    <Chip tone={STATUS_CHIP[record.status as keyof typeof STATUS_CHIP]?.tone ?? "neutral"} dot>
                      {record.key_last4}
                    </Chip>
                  </span>
                )}
                {!supported && (
                  <span className="text-[10.5px] text-[var(--text-3)]">No {purpose} API</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* key form */}
      {selectedProvider && (
        <div className="mt-6 rounded-2xl border border-[var(--hairline)] p-5">
          <div className="flex items-center gap-3">
            <ProviderTile
              provider={selectedProvider}
              label={catalog?.providers[selectedProvider]?.label ?? selectedProvider}
              tile={catalog?.providers[selectedProvider]?.tile}
            />
            <div>
              <p className="text-[13.5px] font-semibold text-[var(--text-1)]">
                {catalog?.providers[selectedProvider]?.label}, {PURPOSE_META[purpose].title}
              </p>
              <p className="text-[11px] text-[var(--text-3)]">
                Paste a key. it is tested live, then encrypted. It cannot be viewed again.
              </p>
            </div>
          </div>

          <div className="mt-5">
            <p className="text-[11.5px] font-medium text-[var(--text-2)]">Model</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {(catalog?.models?.[purpose]?.[selectedProvider] ?? []).map((m) => {
                const isSel = model === m.id;
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setModel(m.id)}
                    className={`fluid rounded-xl border px-3 py-2.5 text-left ${
                      isSel
                        ? "border-[var(--accent)] bg-[var(--accent-dim)]"
                        : "border-[var(--hairline)] bg-[var(--bg-raised)] hover:border-[var(--hairline-strong)]"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12.5px] font-semibold text-[var(--text-1)]">{m.label}</span>
                      {m.tag && (
                        <span
                          className="shrink-0 rounded-md px-1.5 py-0.5 text-[12px] font-bold font-medium"
                          style={
                            isSel
                              ? { background: "var(--accent)", color: "white" }
                              : { background: "var(--panel-hover)", color: "var(--text-3)" }
                          }
                        >
                          {m.tag}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 font-mono text-[10px] text-[var(--text-3)]">{m.id}</p>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mt-4">
            <p className="mb-1.5 text-[11.5px] font-medium text-[var(--text-2)]">
              Fallback model{" "}
              <span className="font-normal text-[var(--text-3)]">(optional, same provider)</span>
            </p>
            <select
              value={fallbackModel}
              onChange={(e) => setFallbackModel(e.target.value)}
              className="fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 text-[12.5px] text-[var(--text-1)] outline-none hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            >
              <option value="">None</option>
              {(catalog?.models?.[purpose]?.[selectedProvider] ?? [])
                .filter((m) => m.id !== model)
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
            </select>
            <p className="mt-1 text-[10.5px] text-[var(--text-3)]">
              If the primary model errors or rate-limits, the platform retries on this one automatically.
            </p>
          </div>
          <div className="mt-4">
            <p className="mb-1.5 text-[11.5px] font-medium text-[var(--text-2)]">API key</p>
            <input
              value={key}
              onChange={(e) => setKey(e.target.value)}
              type="password"
              autoComplete="off"
              placeholder="Paste key (min 8 chars)"
              className="fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-3 font-mono text-[12px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]"
            />
          </div>

          {error && (
            <p className="mt-3 rounded-xl border border-[var(--danger)]/25 bg-[var(--danger-dim)] px-3.5 py-2.5 text-[12px] text-[var(--danger)]">
              {error}
            </p>
          )}

          <div className="mt-4 flex items-center justify-between">
            <p className="text-[11px] text-[var(--text-3)]">
              Saving replaces any existing {catalog?.providers[selectedProvider]?.label} key for{" "}
              {PURPOSE_META[purpose].title.toLowerCase()}.
            </p>
            <button
              onClick={() => void save()}
              disabled={busy === "save" || key.length < 8 || !model}
              className="press fluid flex items-center gap-2 rounded-xl bg-[var(--accent)] px-4 py-2 text-[12.5px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-40"
            >
              <Icon name="check" size={13} strokeWidth={2} />
              {busy === "save" ? "Testing & saving…" : "Test & save key"}
            </button>
          </div>
        </div>
      )}

      {/* saved connections */}
      <div className=" mt-8 pb-8">
        <p className="text-[11px] font-medium text-[var(--text-3)]">Saved connections</p>
        <div className="divide-y divide-[var(--hairline)] rounded-xl border border-[var(--hairline)]">
          {recordsForPurpose.length === 0 && (
            <p className="px-5 py-8 text-center text-[12.5px] text-[var(--text-3)]">
              No {PURPOSE_META[purpose].title.toLowerCase()} connections yet. pick a provider above.
            </p>
          )}
          {recordsForPurpose.map((c) => {
            const meta = catalog?.providers[c.provider];
            const chip = STATUS_CHIP[c.status as keyof typeof STATUS_CHIP] ?? STATUS_CHIP.untested;
            return (
              <div key={c.id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                <ProviderTile provider={c.provider} label={meta?.label ?? c.provider} tile={meta?.tile} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-[13px] font-medium text-[var(--text-1)]">{meta?.label ?? c.provider}</p>
                    <Chip tone={chip.tone} dot pulse={c.status === "invalid"}>
                      {chip.label}
                    </Chip>
                    {c.is_active && c.status === "valid" && <Chip tone="accent">in use</Chip>}
                  </div>
                  <p className="mt-0.5 truncate font-mono text-[10.5px] text-[var(--text-3)]">
                    {c.model}, key {c.key_last4}
                    {c.last_tested_at ? `, tested ${timeAgo(c.last_tested_at)}` : ""}
                    {c.test_detail ? `, ${c.test_detail}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1.5">
                  {!c.is_active && c.status === "valid" && (
                    <button
                      onClick={() => activate(c.id)}
                      disabled={busy === c.id}
                      className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)]"
                    >
                      Use
                    </button>
                  )}
                  <button
                    onClick={() => test(c.id)}
                    disabled={busy === c.id}
                    className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--text-2)] hover:border-[var(--hairline-strong)] hover:text-[var(--text-1)] disabled:opacity-50"
                  >
                    {busy === c.id ? "…" : "Re-test"}
                  </button>
                  <button
                    onClick={() => remove(c.id)}
                    disabled={busy === c.id}
                    className="press fluid rounded-lg border border-[var(--hairline)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--danger)] hover:border-[var(--danger)] disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function ConnectionsPage() {
  return (
    <AuthProvider>
      <AppShell>
        <ConnectionsInner />
      </AppShell>
    </AuthProvider>
  );
}
