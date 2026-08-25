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
}

export function loadIdentity(): Identity {
  try {
    const raw = localStorage.getItem(IDENTITY_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Identity>;
      if (typeof parsed.orgId === "string" && typeof parsed.userId === "string") {
        return { orgId: parsed.orgId, userId: parsed.userId };
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

export interface AnswerResponse {
  query: string;
  markdown: string;
  facts: number;
  passages: number;
  contested: number;
  gaps: string[];
  top_fact_id: string | null;
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
}

export interface AnswerParams {
  q: string;
  workspace?: string;
  agent_mode?: boolean;
  limit?: number;
}

export interface DocumentListParams {
  q?: string;
  source?: string;
  workspace?: string;
  scope?: string;
  limit?: number;
  offset?: number;
}

export interface EpisodeListParams {
  status?: EpisodeStatus;
  source?: string;
  workspace?: string;
  limit?: number;
  offset?: number;
}

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

async function request<T>(path: string, params: Params = {}): Promise<T> {
  const identity = loadIdentity();
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  let resp: Response;
  try {
    resp = await fetch(`/api/v1${path}${query ? `?${query}` : ""}`, {
      headers: {
        Accept: "application/json",
        "X-Test-Org-Id": identity.orgId,
        "X-Test-User-Id": identity.userId,
      },
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
