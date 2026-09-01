/**
 * Typed client for the mlpal-memory-graph API.
 *
 * Same-origin only: the built app is served by the service itself at /ui, and
 * the dev server proxies /api to it — so every call is a relative /api/v1/...
 * request. Identity is the local-first dev scheme (X-Test-* headers, mirrored
 * from the platform's test harness); it is editable and persisted so the
 * explorer can flip between tenants/users. Types mirror the Pydantic schemas
 * in src/mlpal_memory_graph/schemas/.
 */

// ── identity (dev-auth headers) ─────────────────────────────────────────────

const IDENTITY_KEY = "mlpal.memory.identity";

export interface Identity {
  orgId: string;
  userId: string;
  /** Managed deployments: an mlpal API key (memory.read scope). When set it is
   * the credential — the server derives org/user from it and the dev headers
   * are not sent. Lives in localStorage only, same as the platform dashboard. */
  apiKey?: string;
}

export function loadIdentity(): Identity {
  try {
    const raw = localStorage.getItem(IDENTITY_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Identity>;
      if (typeof parsed.orgId === "string" && typeof parsed.userId === "string") {
        return {
          orgId: parsed.orgId,
          userId: parsed.userId,
          apiKey: typeof parsed.apiKey === "string" && parsed.apiKey !== "" ? parsed.apiKey : undefined,
        };
      }
    }
  } catch {
    // fall through to defaults
  }
  return { orgId: "local", userId: "user" };
}

export function saveIdentity(identity: Identity): void {
  localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity));
  window.dispatchEvent(new CustomEvent("mlpal:identity-changed"));
}

/** True when no identity was ever chosen — the app is running on the fallback. */
export function identityIsFallback(): boolean {
  return localStorage.getItem(IDENTITY_KEY) === null;
}

/** The server's dev-identity hint (owner of the local corpus). null outside dev. */
export async function fetchDevIdentity(): Promise<Identity | null> {
  try {
    const r = await fetch(`/api/v1/ops/dev-identity`);
    if (!r.ok) return null;
    const d = (await r.json()) as { org: string; user: string };
    return { orgId: d.org, userId: d.user };
  } catch {
    return null;
  }
}

/** First-run bootstrap: personal memory is owner-only, so a wrong default
 * identity legitimately sees an almost-empty tenant with no explanation.
 * If the user never chose an identity, adopt the server's hint. */
export async function bootstrapIdentity(): Promise<Identity | null> {
  if (!identityIsFallback()) return null;
  const hint = await fetchDevIdentity();
  if (hint) saveIdentity(hint);
  return hint;
}

// ── wire types (mirror schemas/memory.py) ───────────────────────────────────

export interface NodeOut {
  id: string;
  type: string;
  key: string;
  name: string;
  summary: string | null;
  score: number;
  props: Record<string, unknown>;
  scope: string;
  scope_id: string | null;
  also_known_at: string[];
  origin: string;
  confidence: number | null;
  status: string;
  workspace: string | null;
  contested: boolean;
  observed_count: number;
  derived_from: string[];
}

export interface EdgeOut {
  id: string;
  type: string;
  src_id: string;
  dst_id: string;
  fact: string | null;
  valid_at: string | null;
  invalid_at: string | null;
  scope: string;
  scope_id: string | null;
}

export interface PassageOut {
  id: string;
  document_id: string;
  content: string;
  score: number;
  ordinal: number;
  scope: string;
  scope_id: string | null;
  source: string | null;
  origin: string;
  workspace: string | null;
  document_uri: string | null;
  document_title: string | null;
  valid_at: string | null;
}

export interface SearchResponse {
  nodes: NodeOut[];
  edges: EdgeOut[];
  passages: PassageOut[];
}

export type AnswerMode = "packet" | "synthesized" | "hybrid" | "hop";

export interface AnswerResponse {
  query: string;
  markdown: string;
  facts: number;
  passages: number;
  contested: number;
  gaps: string[];
  top_fact_id: string | null;
  mode: AnswerMode;
  synth_model: string | null;
  synth_ms: number | null;
  hops: number | null;
  hop_trace: string[] | null;
  invented_citations: number;
  took_ms: number;
}

export interface ProjectionResponse {
  markdown: string;
  estimated_tokens: number;
  fact_count: number;
  truncated: boolean;
}

export interface StoreStats {
  documents: number;
  chunks: number;
  nodes: number;
  edges: number;
  episodes: number;
  by_scope: Record<string, number>;
  by_source: Record<string, number>;
  by_status: Record<string, number>;
  top_workspaces: Array<{ workspace: string; nodes: number }>;
  contested: number;
}

/** One value a watched metric held, with its bitemporal validity window. */
export interface MetricValueOut {
  value: string;
  display: string;
  valid_at: string | null;
  invalid_at: string | null;
  current: boolean;
  evidence_span: string | null;
}

export interface MetricHistoryOut {
  key: string;
  label: string;
  workspace: string | null;
  values: MetricValueOut[];
}

export interface MetricsResponse {
  metrics: MetricHistoryOut[];
}

// ── wire types (mirror schemas/document.py) ─────────────────────────────────

export interface DocumentOut {
  id: string;
  title: string | null;
  uri: string | null;
  source: string | null;
  scope: string;
  scope_id: string | null;
  workspace: string | null;
  classification: string;
  valid_at: string | null;
  ingested_at: string;
  chunks: number;
}

export interface DocumentListResponse {
  documents: DocumentOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface ChunkOut {
  id: string;
  ordinal: number;
  content: string;
  embedding_model: string | null;
}

export interface DocumentDetailResponse extends DocumentOut {
  chunk_contents: ChunkOut[];
}

// ── wire types (mirror schemas/episode.py) ──────────────────────────────────

export type EpisodeStatus = "pending" | "processed" | "dropped" | "dead";

export interface EpisodeOut {
  event_id: string;
  occurred_at: string;
  ingested_at: string;
  source: string;
  action_type: string;
  scope: string;
  scope_id: string | null;
  workspace: string | null;
  lifecycle: string;
  tier: string | null;
  status: EpisodeStatus;
  processed_at: string | null;
  dropped_reason: string | null;
  error_count: number;
  dead_at: string | null;
}

export interface EpisodeListResponse {
  episodes: EpisodeOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface EpisodeDetailResponse extends EpisodeOut {
  payload: Record<string, unknown>;
  error: string | null;
  has_content: boolean;
}

export interface SearchParams {
  q?: string;
  workspace?: string;
  scope?: string;
  origin?: "direct" | "derived";
  limit?: number;
  depth?: number;
  /** boost (default): workspace focuses ranking · filter: hard-bound to it */
  workspace_mode?: "boost" | "filter";
}

export interface AnswerParams {
  q: string;
  workspace?: string;
  agent_mode?: boolean;
  limit?: number;
  /** ISO instant — answer as memory stood at this valid time (time travel). */
  as_of?: string;
  /** packet (deterministic, $0) | synthesized | hybrid (answer + packet, 1 call) | hop. */
  mode?: AnswerMode;
  max_hops?: number;
}

export interface DocumentListParams {
  q?: string;
  source?: string;
  workspace?: string;
  scope?: string;
  limit?: number;
  offset?: number;
  /** ingested (newest first, default) | valid (event-time ascending — spans history). */
  order?: "ingested" | "valid";
}

export interface EpisodeListParams {
  status?: EpisodeStatus;
  source?: string;
  workspace?: string;
  limit?: number;
  offset?: number;
}

// ── wire types (curation — /memory/curate + DELETE /documents/{id}) ─────────

export interface CurateCandidate {
  id: string;
  title: string | null;
  valid_at: string | null;
  served_count: number;
  reason: string;
}

export interface CuratePreviewResponse {
  mode: "preview";
  workspace: string;
  candidates: CurateCandidate[];
  keep_count: number;
  note: string;
}

export interface ForgetResult {
  id: string;
  purged_chunks: number;
  title: string | null;
}

export interface CurateExecutedResponse {
  mode: "executed";
  forgotten: number;
  purged_chunks: number;
  documents: ForgetResult[];
}

// ── wire types (the memory hop's live SSE events) ───────────────────────────

export type HopEvent =
  | { type: "retrieved"; hop: number; query: string; citations: number }
  | { type: "deciding"; hop: number; action: "answer" | "search"; queries?: string[] }
  | { type: "early_stop"; hop: number; reason: string }
  | { type: "composing"; hop: number }
  | {
      type: "answer";
      markdown: string;
      hops: number;
      trace: string[];
      invented_citations: number;
      model: string;
    }
  | { type: "error"; detail: string };

// ── error type ──────────────────────────────────────────────────────────────

export class MemoryApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "MemoryApiError";
    this.status = status;
  }
}

function extractMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const detail = (body as Record<string, unknown>).detail;
    if (typeof detail === "string") return detail;
  }
  if (typeof body === "string" && body) return body;
  return fallback;
}

// ── client ──────────────────────────────────────────────────────────────────

type Params = Record<string, string | number | boolean | undefined>;

function identityHeaders(): Record<string, string> {
  const identity = loadIdentity();
  if (identity.apiKey) {
    return { "X-API-Key": identity.apiKey };
  }
  return {
    "X-Test-Org-Id": identity.orgId,
    "X-Test-User-Id": identity.userId,
  };
}

function buildQuery(params: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  return query ? `?${query}` : "";
}

async function request<T>(
  path: string,
  params: Params = {},
  init: { method?: "POST" | "DELETE"; body?: unknown } = {},
): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`/api/v1${path}${buildQuery(params)}`, {
      method: init.method ?? "GET",
      headers: {
        Accept: "application/json",
        ...(init.body !== undefined && { "Content-Type": "application/json" }),
        ...identityHeaders(),
      },
      ...(init.body !== undefined && { body: JSON.stringify(init.body) }),
      cache: "no-store",
    });
  } catch (e) {
    throw new MemoryApiError(
      `Could not reach the memory API. Is the stack running? (${
        e instanceof Error ? e.message : String(e)
      })`,
      0,
    );
  }

  const text = await resp.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!resp.ok) {
    throw new MemoryApiError(extractMessage(parsed, `HTTP ${resp.status}`), resp.status);
  }
  return parsed as T;
}

export function getStats(): Promise<StoreStats> {
  return request<StoreStats>("/memory/stats");
}

export function searchMemory(params: SearchParams): Promise<SearchResponse> {
  return request<SearchResponse>("/memory/search", { ...params });
}

export function answerMemory(params: AnswerParams): Promise<AnswerResponse> {
  return request<AnswerResponse>("/memory/answer", { ...params });
}

export function getMetrics(workspace?: string): Promise<MetricsResponse> {
  return request<MetricsResponse>("/memory/metrics", { workspace });
}

export function getProjection(tokenBudget = 5000): Promise<ProjectionResponse> {
  return request<ProjectionResponse>("/memory/projection", { token_budget: tokenBudget });
}

export function getNode(nodeId: string, depth = 1): Promise<SearchResponse> {
  return request<SearchResponse>(`/memory/nodes/${encodeURIComponent(nodeId)}`, { depth });
}

export function listDocuments(params: DocumentListParams): Promise<DocumentListResponse> {
  return request<DocumentListResponse>("/documents", { ...params });
}

export function getDocument(documentId: string): Promise<DocumentDetailResponse> {
  return request<DocumentDetailResponse>(`/documents/${encodeURIComponent(documentId)}`);
}

export function listEpisodes(params: EpisodeListParams): Promise<EpisodeListResponse> {
  return request<EpisodeListResponse>("/episodes", { ...params });
}

export function getEpisode(eventId: string): Promise<EpisodeDetailResponse> {
  return request<EpisodeDetailResponse>(`/episodes/${encodeURIComponent(eventId)}`);
}

// ── curation ────────────────────────────────────────────────────────────────

/** Phase 1: one model call classifies the workspace's documents against the
 * instruction and returns a preview. Nothing is deleted. */
export function curatePreview(
  instruction: string,
  workspace: string,
): Promise<CuratePreviewResponse> {
  return request<CuratePreviewResponse>(
    "/memory/curate",
    {},
    { method: "POST", body: { instruction, workspace } },
  );
}

/** Phase 2: deletes exactly the confirmed ids through the audited forget path. */
export function curateConfirm(
  workspace: string,
  confirmIds: string[],
): Promise<CurateExecutedResponse> {
  return request<CurateExecutedResponse>(
    "/memory/curate",
    {},
    { method: "POST", body: { workspace, confirm_ids: confirmIds } },
  );
}

/** Hard-delete one document + its chunks (direct tier), audited server-side. */
export function forgetDocument(documentId: string): Promise<ForgetResult> {
  return request<ForgetResult>(`/documents/${encodeURIComponent(documentId)}`, {}, {
    method: "DELETE",
  });
}

// ── the memory hop, live (SSE over fetch) ───────────────────────────────────

export interface StreamAnswerParams {
  q: string;
  workspace?: string;
  agent_mode?: boolean;
  max_hops?: number;
}

/** Streams /memory/answer/stream. EventSource cannot send the identity
 * headers, so this parses the `event:`/`data:` frames off a fetch body.
 * Every frame's data JSON carries its own `type`, so data alone suffices. */
export async function streamAnswer(
  params: StreamAnswerParams,
  onEvent: (ev: HopEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`/api/v1/memory/answer/stream${buildQuery({ ...params })}`, {
      headers: { Accept: "text/event-stream", ...identityHeaders() },
      cache: "no-store",
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new MemoryApiError(
      `Could not reach the memory API. Is the stack running? (${
        e instanceof Error ? e.message : String(e)
      })`,
      0,
    );
  }
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      // keep raw text
    }
    throw new MemoryApiError(extractMessage(parsed, `HTTP ${resp.status}`), resp.status);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line
      for (;;) {
        const sep = buffer.indexOf("\n\n");
        if (sep === -1) break;
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const dataLines = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart());
        if (dataLines.length === 0) continue;
        try {
          onEvent(JSON.parse(dataLines.join("\n")) as HopEvent);
        } catch {
          // a malformed frame is dropped, never fatal to the stream
        }
      }
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw e;
  }
}
