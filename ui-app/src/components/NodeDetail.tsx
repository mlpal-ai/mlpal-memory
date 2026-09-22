import { Ban, Copy, Pin, PinOff, ThumbsUp, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ScopeBadge } from "@/components/badges";
import { Field, SlideOver } from "@/components/SlideOver";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type EdgeOut,
  MemoryApiError,
  type NodeOut,
  endorseNodes,
  getNode,
  loadIdentity,
  retractNodes,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate } from "@/lib/format";

/** Slide-over for a derived-memory node: typed props, lifecycle, provenance,
 * and its scope-visible neighborhood. Fetches /memory/nodes/{id} itself so any
 * page (or a memory:// citation) can open one from just an id. */
export function NodeDetail({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
  const [node, setNode] = useState<NodeOut | null>(null);
  const [edges, setEdges] = useState<EdgeOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
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
  }, [nodeId, tick]);

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
              {trustTier(node) && <Badge variant={trustTier(node) === "endorsed" ? "success" : "outline"}>trust:{trustTier(node)}</Badge>}
              {node.props.pinned === true && <Badge variant="info">pinned</Badge>}
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

          <TrustActions node={node} onChanged={reload} onRetracted={onClose} />
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

function trustTier(node: NodeOut): string | null {
  const trust = node.props.trust;
  if (trust && typeof trust === "object" && typeof (trust as { tier?: unknown }).tier === "string") {
    return (trust as { tier: string }).tier;
  }
  return null;
}

/** The person's verbs on a memory (memory v6 WP13 / v10): endorse or withdraw,
 * pin or unpin from every session's projection, retract with a reason. These
 * need a person's identity; an agent never calls them on its own behalf. */
function TrustActions({
  node,
  onChanged,
  onRetracted,
}: {
  node: NodeOut;
  onChanged: () => void;
  onRetracted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [retracting, setRetracting] = useState(false);
  const [reason, setReason] = useState("");
  const me = loadIdentity().userId;
  const endorsedBy = Array.isArray(node.props.endorsed_by)
    ? (node.props.endorsed_by as unknown[]).filter((u): u is string => typeof u === "string")
    : [];
  const mine = endorsedBy.includes(me);
  const pinned = node.props.pinned === true;

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      toast.success(label);
      onChanged();
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function retract() {
    setBusy(true);
    try {
      const res = await retractNodes([node.id], reason.trim());
      toast.success(res.retracted ? "Retracted — the current view and the packet drop it; as-of reads keep it." : "Nothing changed.");
      onRetracted();
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-muted-foreground">
        Trust
        {endorsedBy.length > 0 && (
          <span className="font-normal">
            · endorsed by {endorsedBy.length === 1 ? endorsedBy[0] : `${endorsedBy.length} people`}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={mine ? "outline" : "secondary"}
          disabled={busy}
          onClick={() =>
            void run(
              mine ? "Endorsement withdrawn." : "Endorsed — it ranks first in every packet now.",
              () => endorseNodes([node.id], { withdraw: mine }),
            )
          }
        >
          {mine ? <Undo2 className="size-3.5" /> : <ThumbsUp className="size-3.5" />}
          {mine ? "Withdraw endorsement" : "Endorse"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          title={pinned ? "Remove from the reserved slice of every session's projection" : "Keep this in front of every agent session (a quarter of the projection is reserved for pinned facts)"}
          onClick={() =>
            void run(
              pinned ? "Unpinned." : "Pinned — it leads every session's projection.",
              () => endorseNodes([node.id], { pin: true, withdraw: pinned }),
            )
          }
        >
          {pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
          {pinned ? "Unpin" : "Pin"}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRetracting((v) => !v)}>
          <Ban className="size-3.5" /> Retract…
        </Button>
      </div>
      {retracting && (
        <div className="mt-2 flex flex-col gap-2 rounded-lg border border-[var(--destructive)]/40 p-2.5">
          <p className="text-xs text-muted-foreground">
            Retraction is a soft delete with a reason: the current view and the answer packet stop
            serving it; as-of reads keep the history; a ledger row records who.
          </p>
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="why (3–500 characters)"
            className="h-8 text-xs"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="destructive"
              disabled={busy || reason.trim().length < 3}
              onClick={() => void retract()}
            >
              Retract this memory
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRetracting(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
