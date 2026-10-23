/**
 * Control-plane API client.
 *
 * The browser talks directly to the FastAPI backend with a Better Auth JWT
 * (from the jwt plugin) as the bearer token. The backend verifies it against
 * Better Auth's JWKS endpoint and maps the `role` claim to RBAC.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

let cachedToken: { value: string; fetchedAt: number } | null = null;

/** Fetch (and briefly cache) a Better Auth JWT for the current session. */
export async function getAuthToken(): Promise<string> {
  if (cachedToken && Date.now() - cachedToken.fetchedAt < 60_000) {
    return cachedToken.value;
  }
  const res = await fetch("/api/auth/token");
  if (!res.ok) throw new Error("Not authenticated");
  const data = (await res.json()) as { token?: string };
  if (!data.token) throw new Error("No token in auth response");
  cachedToken = { value: data.token, fetchedAt: Date.now() };
  return data.token;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getAuthToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${path} failed: ${res.status} ${text.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

// --- types ---

export interface PlatformInfo {
  name: string;
  version: string;
  model: string;
  demo_mode: boolean;
  realtime_model: string;
  realtime_available: boolean;
  auth_mode: string;
  capabilities: Record<string, string>;
  connections: { agents?: ActiveConnection; voice?: ActiveConnection };
  policies: Policies;
  identity: { user_id: string; role: string; tenant_id: string; name: string; email: string };
}

export interface AgentInfo {
  slug: string;
  name: string;
  description: string;
  kind: string;
  skills: string[];
  mcp_servers: string[];
  status: string;
  keys_active?: number;
}

export interface Approval {
  id: string;
  run_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  risk: string;
  status: string;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  ts: string;
  actor: string;
  action: string;
  resource: string;
  detail: Record<string, unknown> | null;
}

export interface UsageSummary {
  window_days: number;
  runs: number;
  input_tokens: number;
  output_tokens: number;
  requests: number;
  tool_calls: number;
  cost_usd: number;
  by_agent: { agent: string; runs: number; cost_usd: number }[];
  by_user?: { user_id: string; runs: number; total_tokens: number; cost_usd: number }[];
  daily: { date: string; cost_usd: number }[];
}

export interface RunSummary {
  id: string;
  conversation_id: string | null;
  agent: string;
  status: "running" | "completed" | "failed" | "awaiting_approval" | "cancelled" | string;
  model: string | null;
  duration_ms: number | null;
  usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    requests: number;
    tool_calls: number;
    cost_usd: number;
  } | null;
  error: string | null;
  created_at: string;
}

export interface Policies {
  mutating_min_role: string;
  require_approval_for_mutations: boolean;
  daily_spend_limit_usd: number;
  per_run_token_limit: number;
}

export interface ActiveConnection {
  provider: string;
  model: string;
  status: string;
}

export interface ConnectionRecordView {
  id: string;
  purpose: "agents" | "voice" | string;
  provider: string;
  model: string;
  fallback_model?: string | null;
  key_last4: string;
  status: "valid" | "invalid" | "untested" | string;
  test_detail: string | null;
  last_tested_at: string | null;
  is_active: boolean;
}

export interface ModelEntry {
  id: string;
  label: string;
  tag: string;
}

export interface AgentKeyView {
  id: string;
  label: string;
  key_last4: string;
  revoked: boolean;
  created_at: string | null;
  last_used_at: string | null;
}

export interface AgentAccessInfo {
  invoke_url: string;
  webhook_url: string;
  manifest_url: string;
  keys: AgentKeyView[];
}

export interface InvokeResult {
  run_id: string;
  agent: string;
  status: "completed" | "awaiting_approval" | "failed" | string;
  output: string;
  approvals: { approval_id: string; tool_name: string; args: Record<string, unknown> }[];
  usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    tool_calls: number;
    cost_usd: number;
    duration_ms: number;
  };
}

export interface TriggerRecord {
  id: string;
  name: string;
  agent_slug: string;
  message: string;
  schedule: string;
  enabled: boolean;
  webhook_url: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string | null;
  last_run_id: string | null;
}

export interface ModelEntry {
  id: string;
  label: string;
  tag: string;
}

export interface RedTeamResult {
  agent: string;
  resilience: number;
  passed: number;
  total: number;
  results: { id: string; category: string; message: string; output: string; passed: boolean }[];
}

export interface ConnectionCatalog {
  providers: Record<string, { label: string; tile: string; tint: string }>;
  models: Record<string, Record<string, ModelEntry[]>>;
  supports: Record<string, Record<string, boolean>>;
}

export interface RunEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface GithubOAuthStatus {
  configured: boolean;
  connected: boolean;
  login: string | null;
  token_prefix: string | null;
}

export interface VoiceCatalogVoice {
  id: string;
  label: string;
}

export interface VoiceCatalogProvider {
  label: string;
  tile?: string;
  key_present: boolean;
  voice_capable: boolean;
  available: boolean;
  voices: VoiceCatalogVoice[];
  voice_models: { id: string; label: string; tag?: string }[];
  reason: string | null;
}

export interface VoiceCatalog {
  default_realtime_model: string;
  default_chat_model: string;
  providers: Record<string, VoiceCatalogProvider>;
  chat_models: Record<string, { id: string; label: string; tag?: string }[]>;
}

// --- endpoints ---

export interface ChatAttachmentInput {
  name: string;
  media_type: string;
  data: string;
  /** Local-only data URL for optimistic thumbnails; never sent to the API. */
  previewUrl?: string;
}

/** Persisted attachment metadata on a user message. */
export interface ChatAttachmentView {
  name: string;
  media_type: string;
  /** Bounded data URL for small images; absent for large/non-image files. */
  preview?: string;
}

/** A named workspace grouping conversations with shared instructions. */
export interface Project {
  id: string;
  name: string;
  instructions: string;
  icon: string;
  color: string;
  conversation_count: number;
  created_at: string | null;
}

/** One cited web source for an assistant answer (Perplexity-grade). */
export interface ChatSource {
  id: string;
  title: string;
  url: string;
}

/** Full-text match across message history. */
export interface ConversationMatch {
  id: string;
  title: string;
  channel: string;
  updated_at: string;
  snippet: string;
  matched_role: string;
}

/** A user-saved prompt template from the composer library. */
export interface PromptTemplate {
  id: string;
  title: string;
  body: string;
  created_at: string | null;
}

/** One assistant message that may declare artifacts (parsed in-browser). */
export interface ArtifactSource {
  message_id: string;
  conversation_id: string;
  conversation_title: string;
  project_id: string | null;
  text: string;
  created_at: string | null;
}

/** Token/cost footer for one assistant turn. */
export interface TurnUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface UserToken {
  id: string;
  label: string;
  role: string;
  key_last4: string;
  revoked: boolean;
  created_at: string | null;
  last_used_at: string | null;
}

export const backend = {
  platform: () => api<PlatformInfo>("/api/platform"),
  agents: () => api<AgentInfo[]>("/api/agents"),
  skills: () =>
    api<{ slug: string; name: string; description: string; status: string }[]>(
      "/api/skills",
    ),
  approvals: () => api<Approval[]>("/api/approvals"),
  decideApproval: (id: string, approved: boolean) =>
    api<{ id: string; status: string }>(`/api/approvals/${id}/decide`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  audit: (limit = 50) => api<AuditEntry[]>(`/api/audit?limit=${limit}`),
  auditVerify: () => api<{ valid: boolean; broken_at: string | null }>("/api/audit/verify"),
  usage: (days = 30) => api<UsageSummary>(`/api/usage?days=${days}`),
  mcp: () =>
    api<
      {
        slug: string;
        name: string;
        transport: string;
        url: string | null;
        command: string | null;
        status: string;
        health: string;
      }[]
    >("/api/mcp"),
  checkMcp: (slug: string) =>
    api<{ slug: string; health: string; tools?: number; detail?: string }>(
      `/api/mcp/${slug}/check`,
      { method: "POST" },
    ),
  conversations: () =>
    api<
      { id: string; title: string; channel: string; project_id: string | null; archived: boolean; updated_at: string }[]
    >("/api/conversations"),
  conversation: (id: string) =>
    api<{
      id: string;
      title: string;
      project_id: string | null;
      archived: boolean;
      messages: {
        id: string;
        role: string;
        agent: string | null;
        content: { text?: string; sources?: ChatSource[]; followups?: string[]; model?: string; thinking?: string; attachments?: ChatAttachmentView[] };
        created_at: string;
      }[];
    }>(`/api/conversations/${id}`),
  moveConversation: (id: string, project_id: string | null) =>
    api<{ id: string; project_id: string | null }>(`/api/conversations/${id}/move`, {
      method: "POST",
      body: JSON.stringify({ project_id }),
    }),
  archiveConversation: (id: string) =>
    api<{ id: string; archived: boolean }>(`/api/conversations/${id}/archive`, { method: "POST" }),
  unarchiveConversation: (id: string) =>
    api<{ id: string; archived: boolean }>(`/api/conversations/${id}/unarchive`, { method: "POST" }),
  projects: () => api<Project[]>("/api/projects"),
  createProject: (name: string, opts?: { icon?: string; color?: string }) =>
    api<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, icon: opts?.icon ?? "folder", color: opts?.color ?? "slate" }),
    }),
  updateProject: (id: string, patch: { name?: string; instructions?: string; icon?: string; color?: string }) =>
    api<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteProject: (id: string) =>
    api<{ deleted: boolean }>(`/api/projects/${id}`, { method: "DELETE" }),
  renameConversation: (id: string, title: string) =>
    api<{ id: string; title: string }>(`/api/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  duplicateConversation: (id: string) =>
    api<{ id: string; title: string }>(`/api/conversations/${id}/duplicate`, {
      method: "POST",
    }),
  shareConversation: (id: string) =>
    api<{ share_token: string }>(`/api/conversations/${id}/share`, { method: "POST" }),
  revokeShare: (id: string) =>
    api<{ revoked: number }>(`/api/conversations/${id}/share`, { method: "DELETE" }),
  submitFeedback: (runId: string, rating: "up" | "down", comment?: string) =>
    api<{ id: string; run_id: string; rating: string }>(`/api/runs/${runId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating, comment: comment ?? null }),
    }),
  searchConversations: (q: string, limit = 20) =>
    api<ConversationMatch[]>(`/api/conversations/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  getInstructions: () => api<{ instructions: string }>("/api/preferences/instructions"),
  setInstructions: (instructions: string) =>
    api<{ instructions: string }>("/api/preferences/instructions", {
      method: "PUT",
      body: JSON.stringify({ instructions }),
    }),
  prompts: () => api<PromptTemplate[]>("/api/prompts"),
  createPrompt: (title: string, body: string) =>
    api<PromptTemplate>("/api/prompts", { method: "POST", body: JSON.stringify({ title, body }) }),
  updatePrompt: (id: string, patch: { title?: string; body?: string }) =>
    api<PromptTemplate>(`/api/prompts/${id}`, { method: "PUT", body: JSON.stringify(patch) }),
  deletePrompt: (id: string) =>
    api<{ deleted: boolean }>(`/api/prompts/${id}`, { method: "DELETE" }),
  artifactsIndex: (limit = 200) =>
    api<ArtifactSource[]>(`/api/artifacts/index?limit=${limit}`),
  deleteConversation: (id: string) =>
    api<{ deleted: boolean }>(`/api/conversations/${id}`, { method: "DELETE" }),
  runs: (params?: {
    limit?: number;
    offset?: number;
    status?: string;
    agent?: string;
    since_hours?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null) qs.set(k, String(v));
      }
    }
    return api<{ total: number; runs: RunSummary[] }>(`/api/runs?${qs.toString()}`);
  },
  cancelRun: (runId: string) =>
    api<{ cancelled: boolean }>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  deleteRun: (runId: string) =>
    api<{ deleted: boolean }>(`/api/runs/${runId}`, { method: "DELETE" }),
  clearRuns: () => api<{ deleted: number }>("/api/runs/clear", { method: "POST" }),
  runDetail: (runId: string) =>
    api<
      RunSummary & {
        ttft_ms: number | null;
        spans: { name: string; kind: string; start_ms: number; end_ms: number; duration_ms: number }[];
      }
    >(`/api/runs/${runId}`),
  updatePolicies: (patch: { require_approval_for_mutations?: boolean; mutating_min_role?: string }) =>
    api<Policies>("/api/policies", { method: "PATCH", body: JSON.stringify(patch) }),
  setAgentStatus: (slug: string, status: "enabled" | "disabled") =>
    api<AgentInfo>(`/api/agents/${slug}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  addMcpServer: (body: {
    name: string;
    transport: "streamable-http" | "sse" | "stdio";
    url?: string;
    command?: string;
    status?: "enabled" | "disabled";
  }) => api<{ slug: string }>("/api/mcp", { method: "POST", body: JSON.stringify(body) }),
  agentKeys: (slug: string) =>
    api<AgentAccessInfo & { keys: AgentKeyView[] }>(`/api/agents/${slug}/keys`),
  createAgentKey: (slug: string, label: string) =>
    api<AgentKeyView & { key: string }>(`/api/agents/${slug}/keys`, {
      method: "POST",
      body: JSON.stringify({ label }),
    }),
  revokeAgentKey: (slug: string, keyId: string) =>
    api<{ revoked: boolean }>(`/api/agent-keys/${keyId}?slug=${slug}`, { method: "DELETE" }),
  userTokens: () => api<UserToken[]>("/api/tokens"),
  createUserToken: (label: string) =>
    api<UserToken & { key: string }>("/api/tokens", { method: "POST", body: JSON.stringify({ label }) }),
  revokeUserToken: (id: string) =>
    api<{ revoked: boolean }>(`/api/tokens/${id}`, { method: "DELETE" }),
  getSkill: (slug: string) => api<{ slug: string; content: string }>(`/api/studio/skills/${slug}`),
  saveSkill: (slug: string, content: string) =>
    api<{ slug: string; version: number; rebuilt: boolean }>(`/api/studio/skills/${slug}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  skillVersions: (slug: string) =>
    api<{ id: string; version: number; created_by: string; created_at: string; chars: number }[]>(
      `/api/studio/skills/${slug}/versions`,
    ),
  rollbackSkill: (slug: string, version: number) =>
    api<{ slug: string; version: number; rebuilt: boolean }>(
      `/api/studio/skills/${slug}/versions/${version}/rollback`,
      { method: "POST" },
    ),
  redTeam: (slug: string) =>
    api<RedTeamResult>(`/api/security/redteam/${slug}`, { method: "POST" }),
  triggers: () => api<TriggerRecord[]>("/api/triggers"),
  createTrigger: (body: {
    name: string;
    agent_slug: string;
    message: string;
    schedule_type: "interval" | "daily";
    interval_minutes?: number;
    time_of_day?: string;
    webhook_url?: string;
  }) => api<TriggerRecord>("/api/triggers", { method: "POST", body: JSON.stringify(body) }),
  setTriggerEnabled: (id: string, enabled: boolean) =>
    api<TriggerRecord>(`/api/triggers/${id}?enabled=${enabled}`, { method: "PATCH" }),
  deleteTrigger: (id: string) =>
    api<{ deleted: boolean }>(`/api/triggers/${id}`, { method: "DELETE" }),
  runTriggerNow: (id: string) =>
    api<InvokeResult>(`/api/triggers/${id}/run`, { method: "POST" }),
  getNotifications: () =>
    api<{ approval_webhook_url: string | null }>("/api/notifications"),
  setNotifications: (approvalWebhookUrl: string | null) =>
    api<{ approval_webhook_url: string | null }>("/api/notifications", {
      method: "PUT",
      body: JSON.stringify({ approval_webhook_url: approvalWebhookUrl }),
    }),
  testNotifications: () =>
    api<{ ok: boolean; status_code?: number; detail?: string }>("/api/notifications/test", {
      method: "POST",
    }),
  connections: () => api<ConnectionRecordView[]>("/api/connections"),
  connectionCatalog: () => api<ConnectionCatalog>("/api/connections/catalog"),
  saveConnection: (body: {
    purpose: string;
    provider: string;
    model: string;
    key: string;
    fallback_model?: string;
  }) =>
    api<ConnectionRecordView>("/api/connections", { method: "POST", body: JSON.stringify(body) }),
  testConnection: (id: string) =>
    api<ConnectionRecordView>(`/api/connections/${id}/test`, { method: "POST" }),
  activateConnection: (id: string) =>
    api<ConnectionRecordView>(`/api/connections/${id}/activate`, { method: "POST" }),
  deleteConnection: (id: string) =>
    api<{ deleted: boolean }>(`/api/connections/${id}`, { method: "DELETE" }),
  githubOauthStatus: () => api<GithubOAuthStatus>("/api/github/oauth/status"),
  githubOauthAuthorizeUrl: () =>
    api<{ authorize_url: string }>("/api/github/oauth/authorize-url"),
  githubOauthDisconnect: () =>
    api<{ connected: boolean }>("/api/github/oauth", { method: "DELETE" }),
  voiceCatalog: () => api<VoiceCatalog>("/api/voice/catalog"),
  modelCatalog: (refresh = false) =>
    api<ModelCatalog>(`/api/models/catalog${refresh ? "?refresh=true" : ""}`),
  // --- Enterprise pack ---
  replayRun: async (runId: string, model?: string | null) => {
    const token = await getAuthToken();
    return fetch(`${API_URL}/api/runs/${runId}/replay`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ model: model ?? null }),
    });
  },
  voiceAnalytics: (days = 7) => api<VoiceAnalytics>(`/api/voice/analytics?days=${days}`),
  voiceSessions: (limit = 20) => api<VoiceSession[]>(`/api/voice/sessions?limit=${limit}`),
  eventSubscriptions: () => api<EventSubscription[]>("/api/event-subscriptions"),
  createEventSubscription: (body: { url: string; events: string[]; secret?: string }) =>
    api<EventSubscription>("/api/event-subscriptions", { method: "POST", body: JSON.stringify(body) }),
  setEventSubscriptionEnabled: (id: string, enabled: boolean) =>
    api<EventSubscription>(`/api/event-subscriptions/${id}?enabled=${enabled}`, { method: "PATCH" }),
  deleteEventSubscription: (id: string) =>
    api<{ deleted: boolean }>(`/api/event-subscriptions/${id}`, { method: "DELETE" }),
  testEventSubscription: (id: string) =>
    api<{ ok: boolean; status_code?: number }>(`/api/event-subscriptions/${id}/test`, { method: "POST" }),
  exportAudit: (format: "csv" | "json" = "csv") => downloadFile(`/api/audit/export?format=${format}`, `audit-export.${format}`),
  exportUsage: (days = 30, format: "csv" | "json" = "csv") =>
    downloadFile(`/api/usage/export?days=${days}&format=${format}`, `usage-export.${format}`),
  exportMine: () => downloadFile("/api/export/mine", "voicefirst-export.json"),
  sharedConversation: (token: string) =>
    fetch(`${API_URL}/api/shared/${encodeURIComponent(token)}`).then(async (res) => {
      if (!res.ok) throw new Error("This link is invalid or was revoked.");
      return res.json() as Promise<SharedConversationView>;
    }),
};

export interface SharedConversationMessage {
  role: string;
  text: string;
  sources?: ChatSource[];
  model?: string | null;
  created_at: string;
}

export interface SharedConversationView {
  title: string;
  messages: SharedConversationMessage[];
}

/** Authenticated file download (exports). */
async function downloadFile(path: string, filename: string) {
  const token = await getAuthToken();
  const res = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** Provider-neutral reasoning effort, as accepted by POST /api/chat. */
export type ReasoningEffort = "minimal" | "low" | "medium" | "high";

export type ProviderStatus = "ok" | "no_key" | "invalid_key" | "unavailable";

export interface CatalogModelEntry {
  id: string;
  name: string;
  provider: string;
  efforts: ReasoningEffort[];
}

export interface ProviderModelCatalog {
  provider: string;
  label: string;
  status: ProviderStatus;
  message: string | null;
  fetched_at: number;
  models: CatalogModelEntry[];
}

export interface ModelCatalog {
  providers: Record<string, ProviderModelCatalog>;
}

export interface VoiceAnalytics {
  window_days: number;
  sessions: number;
  turns: number;
  barge_ins: number;
  avg_ttfw_ms: number;
  avg_duration_ms: number;
  by_day: { date: string; sessions: number; avg_ttfw_ms: number }[];
}

export interface VoiceSession {
  id: string;
  channel: string;
  provider: string | null;
  model: string | null;
  voice: string | null;
  status: string;
  turns: number;
  ttfw_ms: number | null;
  barge_ins: number;
  duration_ms: number | null;
  error: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface EventSubscription {
  id: string;
  url: string;
  events: string[];
  enabled: boolean;
  dead_lettered: boolean;
  failure_count: number;
  deliveries_total: number;
  has_secret: boolean;
  last_status: string | null;
  last_status_code: number | null;
  last_delivery_at: string | null;
  created_at: string | null;
}

/** Parse an SSE response body, yielding one event per `data: <json>` frame.
  * Only complete frames (terminated by a blank line) are processed, so frames
  * split across network chunks are never corrupted or dropped. */
async function* parseSse<T = RunEvent>(res: Response): AsyncGenerator<T> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = frame.trim();
      if (line.startsWith("data: ")) {
        try {
          yield JSON.parse(line.slice(6)) as T;
        } catch {
          // ignore malformed frame
        }
      }
    }
  }
}

/** POST /api/chat and yield SSE events as they arrive. */
export async function* streamChat(
  message: string,
  conversationId: string | null,
  attachments?: ChatAttachmentInput[],
  agent?: string | null,
  model?: string | null,
  effort?: ReasoningEffort | null,
): AsyncGenerator<RunEvent> {
  const token = await getAuthToken();
  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      attachments: (attachments ?? []).map(({ previewUrl: _p, ...a }) => a),
      agent: agent ?? null,
      model: model ?? null,
      effort: effort ?? null,
    }),
  });
  if (!res.ok || !res.body) throw new Error(`Chat failed: ${res.status}`);
  yield* parseSse(res);
}

/** Resume a run paused for approvals, after decisions were submitted. */
export async function* streamResume(runId: string, model?: string | null): AsyncGenerator<RunEvent> {
  const token = await getAuthToken();
  const res = await fetch(`${API_URL}/api/runs/${runId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ model: model ?? null }),
  });
  if (!res.ok || !res.body) throw new Error(`Resume failed: ${res.status}`);
  yield* parseSse(res);
}

/** Download a conversation export (markdown or json) via authenticated fetch. */
export async function exportConversation(conversationId: string, format: "md" | "json" = "md") {
  const token = await getAuthToken();
  const res = await fetch(
    `${API_URL}/api/conversations/${conversationId}/export?format=${format}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${conversationId}.${format}`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** Subscribe to the global platform event stream (SSE) with reconnect and
  * exponential backoff (2.5s → 30s; reset on a successful connection). */
export function connectEventStream(
  onEvent: (event: { type: string; ts: string; payload: Record<string, unknown> }) => void,
): () => void {
  // Browsers cap HTTP/1.1 connections per origin (about six). One SSE stream per
  // tab starves chat requests once several tabs are open, so exactly one tab
  // holds the stream (Web Locks) and fans events out to the others
  // (BroadcastChannel).
  let stopped = false;
  let leader = false;
  let controller: AbortController | null = null;
  let backoffMs = 2500;
  const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("vf-events") : null;
  const lockAbort = new AbortController();

  const emit = (event: { type: string; ts: string; payload: Record<string, unknown> }) => {
    onEvent(event);
    channel?.postMessage(event);
  };

  channel?.addEventListener("message", (msg) => {
    if (!leader && !stopped) onEvent(msg.data as { type: string; ts: string; payload: Record<string, unknown> });
  });

  const streamLoop = async () => {
    while (!stopped) {
      try {
        const token = await getAuthToken();
        controller = new AbortController();
        const res = await fetch(`${API_URL}/api/events`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`events ${res.status}`);
        backoffMs = 2500;
        for await (const event of parseSse<{
          type?: string;
          ts?: string;
          payload?: Record<string, unknown>;
        }>(res)) {
          if (stopped) break;
          emit({
            type: String(event.type ?? "event"),
            ts: String(event.ts ?? new Date().toISOString()),
            payload: event.payload ?? {},
          });
        }
      } catch {
        if (stopped) return;
      }
      if (!stopped) {
        await new Promise((r) => setTimeout(r, backoffMs));
        backoffMs = Math.min(backoffMs * 2, 30_000);
      }
    }
  };

  if (typeof navigator !== "undefined" && navigator.locks) {
    navigator.locks
      .request("vf-events-stream", { signal: lockAbort.signal }, async () => {
        leader = true;
        try {
          await streamLoop();
        } finally {
          leader = false;
        }
      })
      .catch(() => {});
  } else {
    leader = true;
    void streamLoop();
  }

  return () => {
    stopped = true;
    controller?.abort();
    lockAbort.abort();
    channel?.close();
  };
}

/** Connect the voice WebSocket (auth via token query param). */
export async function connectVoice(): Promise<WebSocket> {
  const token = await getAuthToken();
  const wsUrl = API_URL.replace(/^http/, "ws");
  const ws = new WebSocket(`${wsUrl}/api/voice/ws?token=${encodeURIComponent(token)}`);
  return new Promise((resolve, reject) => {
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error("Voice WebSocket failed"));
  });
}
