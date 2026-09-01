import { FileText } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ScopeBadge, SourceBadge } from "@/components/badges";
import { Field, SlideOver } from "@/components/SlideOver";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type DocumentDetailResponse,
  type DocumentOut,
  MemoryApiError,
  getDocument,
  getStats,
  listDocuments,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, timeAgo } from "@/lib/format";
import { useDebounced } from "@/lib/use-debounced";
import { useWorkspace } from "@/lib/workspace";

const PAGE_SIZE = 25;

export function Documents() {
  const [q, setQ] = useState("");
  const [workspace, setWorkspace] = useWorkspace();
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const [docs, setDocs] = useState<DocumentOut[] | null>(null);
  const [total, setTotal] = useState(0);
  const [sources, setSources] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const qDebounced = useDebounced(q.trim());
  const workspaceDebounced = useDebounced(workspace.trim());

  const load = useCallback(async () => {
    try {
      const res = await listDocuments({
        q: qDebounced || undefined,
        source: source || undefined,
        workspace: workspaceDebounced || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setDocs(res.documents);
      setTotal(res.total);
    } catch (err) {
      toast.error((err as MemoryApiError).message);
      setDocs([]);
    }
  }, [qDebounced, source, workspaceDebounced, offset]);

  // A page offset only makes sense within the filter set it was reached in.
  useEffect(() => {
    setOffset(0);
  }, [qDebounced, source, workspaceDebounced]);

  useEffect(() => {
    void load();
  }, [load]);

  // The chip set mirrors what the store actually holds — a new collector shows
  // up here without a UI change.
  useEffect(() => {
    getStats()
      .then((s) =>
        setSources(
          Object.entries(s.by_source)
            .sort(([, a], [, b]) => b - a)
            .map(([name]) => name),
        ),
      )
      .catch(() => null);
  }, []);

  const hasFilters = q.trim() !== "" || workspace.trim() !== "" || source !== "";

  function clearFilters() {
    setQ("");
    setWorkspace("");
    setSource("");
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <FileText className="size-6" /> Documents
        </h1>
        <p className="text-sm text-muted-foreground">
          The direct store — every verbatim document in your accessible scopes, with its
          embedded chunks.
        </p>
      </div>

      {/* filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by title…"
          className="max-w-xs"
        />
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder="workspace"
          className="w-52 font-mono text-xs"
        />
        {sources.length > 0 && (
          <div className="inline-flex flex-wrap rounded-full bg-secondary p-1">
            <Chip active={source === ""} onClick={() => setSource("")}>
              All sources
            </Chip>
            {sources.map((s) => (
              <Chip
                key={s}
                active={source === s}
                tone={s === "yodex_failed" ? "destructive" : undefined}
                onClick={() => setSource(s)}
              >
                {s}
              </Chip>
            ))}
          </div>
        )}
      </div>

      {docs === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : docs.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              {hasFilters
                ? "No documents match these filters."
                : "Nothing in the direct store yet — ingest a document and it appears here."}
            </p>
            {hasFilters && (
              <button onClick={clearFilters} className="text-xs link-accent">
                Clear filters
              </button>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                  <th className="py-2.5 pl-4 font-medium">Title</th>
                  <th className="py-2.5 font-medium">Source</th>
                  <th className="py-2.5 font-medium">Scope</th>
                  <th className="py-2.5 font-medium">Workspace</th>
                  <th className="py-2.5 text-right font-medium">Chunks</th>
                  <th className="py-2.5 pr-4 text-right font-medium">Ingested</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr
                    key={d.id}
                    onClick={() => setSelected(d.id)}
                    tabIndex={0}
                    role="button"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSelected(d.id);
                      }
                    }}
                    className="cursor-pointer border-b border-border/50 transition-colors last:border-0 hover:bg-muted/60 focus-visible:outline-none focus-visible:bg-muted/60"
                  >
                    <td className="max-w-0 w-2/5 truncate py-2.5 pl-4 pr-3 font-medium">
                      {d.title ?? d.uri ?? "untitled"}
                    </td>
                    <td className="py-2.5 pr-3">
                      <SourceBadge source={d.source} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <ScopeBadge scope={d.scope} scopeId={d.scope_id} />
                    </td>
                    <td className="max-w-0 truncate py-2.5 pr-3 text-xs text-muted-foreground">
                      {d.workspace ?? "—"}
                    </td>
                    <td className="py-2.5 text-right text-xs tabular-nums">{d.chunks}</td>
                    <td
                      className="py-2.5 pr-4 text-right text-xs text-muted-foreground"
                      title={new Date(d.ingested_at).toLocaleString()}
                    >
                      {timeAgo(d.ingested_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span className="tabular-nums">
                Showing {total === 0 ? 0 : offset + 1}–{offset + docs.length} of {total}
              </span>
              <div className="flex gap-1.5">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  className="rounded-md border border-border px-2.5 py-1 font-medium transition-colors disabled:opacity-40 enabled:hover:bg-muted"
                >
                  Prev
                </button>
                <button
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  className="rounded-md border border-border px-2.5 py-1 font-medium transition-colors disabled:opacity-40 enabled:hover:bg-muted"
                >
                  Next
                </button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {selected && <DocumentDetail documentId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function Chip({
  active,
  tone,
  onClick,
  children,
}: {
  active: boolean;
  tone?: "destructive";
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-full px-3 py-1 text-xs font-medium transition-colors",
        active
          ? tone === "destructive"
            ? "bg-[var(--destructive-bg)] text-[var(--destructive)] shadow-sm"
            : "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** Slide-over for a stored document: metadata + the ordered verbatim chunks
 * exactly as the retriever sees them. Fetches /documents/{id} itself.
 * Exported: the Timeline page opens the same panel from its day groups. */
export function DocumentDetail({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const [doc, setDoc] = useState<DocumentDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDocument(documentId)
      .then((res) => {
        if (!cancelled) setDoc(res);
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  return (
    <SlideOver
      title={
        <>
          <span className="size-2.5 rounded-full bg-[var(--info)]" />
          Document
        </>
      }
      onClose={onClose}
    >
      {error ? (
        <p className="text-sm text-[var(--destructive)]">{error}</p>
      ) : doc === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          <div>
            <div className="flex flex-wrap items-center gap-1.5">
              <SourceBadge source={doc.source} />
              <ScopeBadge scope={doc.scope} scopeId={doc.scope_id} />
              {doc.workspace && <Badge variant="muted">ws:{doc.workspace}</Badge>}
              <Badge variant="outline">{doc.classification}</Badge>
            </div>
            <p className="mt-3 text-sm font-medium leading-relaxed">
              {doc.title ?? "untitled document"}
            </p>
          </div>

          {doc.uri && (
            <div>
              <div className="text-xs text-muted-foreground">URI</div>
              <code className="mt-1 block truncate rounded bg-muted px-2 py-1 text-xs" title={doc.uri}>
                {doc.uri}
              </code>
            </div>
          )}

          <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Field label="Valid at">{fmtDate(doc.valid_at)}</Field>
            <Field label="Ingested">{new Date(doc.ingested_at).toLocaleString()}</Field>
            <Field label="Chunks">
              <span className="tabular-nums">{doc.chunks}</span>
            </Field>
            <Field label="Classification">{doc.classification}</Field>
          </div>

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">
              Verbatim chunks · {doc.chunk_contents.length}
            </div>
            <div className="flex flex-col gap-2">
              {doc.chunk_contents.map((c) => (
                <div key={c.id} className="rounded-lg border border-border">
                  <div className="flex items-center gap-2 border-b border-border/50 px-3 py-2">
                    <span className="text-xs font-medium text-muted-foreground">#{c.ordinal}</span>
                    {c.embedding_model && <Badge variant="secondary">{c.embedding_model}</Badge>}
                  </div>
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-b-lg bg-muted p-3 text-[11px] leading-relaxed">
                    {c.content}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </SlideOver>
  );
}
