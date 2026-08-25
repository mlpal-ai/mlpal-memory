import { Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ScopeBadge } from "@/components/badges";
import { Field, SlideOver } from "@/components/SlideOver";
import { Badge } from "@/components/ui/badge";
import { type EdgeOut, MemoryApiError, type NodeOut, getNode } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate } from "@/lib/format";

/** Slide-over for a derived-memory node: typed props, lifecycle, provenance,
 * and its scope-visible neighborhood. Fetches /memory/nodes/{id} itself so any
 * page (or a memory:// citation) can open one from just an id. */
export function NodeDetail({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
  const [node, setNode] = useState<NodeOut | null>(null);
  const [edges, setEdges] = useState<EdgeOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getNode(nodeId)
      .then((res) => {
        if (cancelled) return;
        setNode(res.nodes[0] ?? null);
        setEdges(res.edges);
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [nodeId]);

  function copyUri() {
    void navigator.clipboard?.writeText(`memory://node/${nodeId}`);
    toast.success("memory:// URI copied.");
  }

  return (
    <SlideOver
      title={
        <>
          <span className="size-2.5 rounded-full bg-[var(--accent)]" />
          Memory node
        </>
      }
      onClose={onClose}
    >
      {error ? (
        <p className="text-sm text-[var(--destructive)]">{error}</p>
      ) : node === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          <div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{node.type}</Badge>
              <ScopeBadge scope={node.scope} scopeId={node.scope_id} />
              {node.workspace && <Badge variant="muted">ws:{node.workspace}</Badge>}
              {node.contested && <Badge variant="destructive">contested</Badge>}
            </div>
            <p className="mt-3 text-sm font-medium leading-relaxed">{node.name}</p>
            {node.summary && (
              <p className="mt-1.5 text-sm text-muted-foreground">{node.summary}</p>
            )}
          </div>

          <div>
            <div className="text-xs text-muted-foreground">memory:// URI</div>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-xs">
                memory://node/{node.id}
              </code>
              <button
                onClick={copyUri}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted"
              >
                <Copy className="size-3.5" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Field label="Lifecycle">
              <Badge variant={node.status === "published" ? "success" : "secondary"}>
                {node.status}
              </Badge>
            </Field>
            <Field label="Observed">×{node.observed_count}</Field>
            <Field label="Origin">{node.origin}</Field>
            <Field label="Confidence">
              {node.confidence != null ? node.confidence.toFixed(2) : "—"}
            </Field>
          </div>

          {Object.keys(node.props).length > 0 && (
            <div>
              <div className="mb-1.5 text-xs font-medium text-muted-foreground">Typed props</div>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-[11px] leading-relaxed">
                {JSON.stringify(node.props, null, 2)}
              </pre>
            </div>
          )}

          {node.derived_from.length > 0 && (
            <div>
              <div className="mb-1.5 text-xs font-medium text-muted-foreground">
                Derived from {node.derived_from.length} episode
                {node.derived_from.length === 1 ? "" : "s"}
              </div>
              <div className="flex flex-col gap-1">
                {node.derived_from.slice(0, 8).map((id) => (
                  <code key={id} className="truncate rounded bg-muted px-2 py-1 text-[11px]">
                    {id}
                  </code>
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">
              {edges.length} edge{edges.length === 1 ? "" : "s"} in scope-visible neighborhood
            </div>
            {edges.length === 0 ? (
              <p className="text-sm text-muted-foreground">No connected facts in your scopes.</p>
            ) : (
              <div className="flex flex-col gap-2">
                {edges.slice(0, 20).map((e) => (
                  <div
                    key={e.id}
                    className={cn(
                      "rounded-lg border border-border p-2.5 text-xs",
                      e.invalid_at && "opacity-60",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant={e.type === "CONTRADICTS" ? "destructive" : "secondary"}>
                        {e.type}
                      </Badge>
                      <span className="text-muted-foreground">
                        {fmtDate(e.valid_at)}
                        {e.invalid_at && ` → invalidated ${fmtDate(e.invalid_at)}`}
                      </span>
                    </div>
                    {e.fact && <p className="mt-1.5 leading-relaxed">{e.fact}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </SlideOver>
  );
}
