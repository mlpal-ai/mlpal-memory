import { Activity, Copy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ScopeBadge, SourceBadge } from "@/components/badges";
import { Field, SlideOver } from "@/components/SlideOver";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type EpisodeDetailResponse,
  type EpisodeOut,
  type EpisodeStatus,
  MemoryApiError,
  getEpisode,
  listEpisodes,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { timeAgo } from "@/lib/format";
import { useDebounced } from "@/lib/use-debounced";
import { useWorkspace } from "@/lib/workspace";

const PAGE_SIZE = 25;
const STATUSES = ["pending", "processed", "dropped", "dead"] as const;

const STATUS_VARIANT: Record<EpisodeStatus, "success" | "warning" | "muted" | "destructive"> = {
  processed: "success",
  pending: "warning",
  dropped: "muted",
  dead: "destructive",
};

// Chip tint when a status filter is active — same palette as the badges.
const STATUS_CHIP_ACTIVE: Record<EpisodeStatus, string> = {
  processed: "bg-[var(--success-bg)] text-[var(--success)] shadow-sm",
  pending: "bg-[var(--warning-bg)] text-[var(--warning)] shadow-sm",
  dropped: "bg-card text-foreground shadow-sm",
  dead: "bg-[var(--destructive-bg)] text-[var(--destructive)] shadow-sm",
};

export function Episodes() {
  const [status, setStatus] = useState<"" | EpisodeStatus>("");
  const [source, setSource] = useState("");
  const [workspace, setWorkspace] = useWorkspace();
  const [offset, setOffset] = useState(0);
  const [episodes, setEpisodes] = useState<EpisodeOut[] | null>(null);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Partial<Record<EpisodeStatus, number>>>({});
  const [selected, setSelected] = useState<string | null>(null);

  const sourceDebounced = useDebounced(source.trim());
  const workspaceDebounced = useDebounced(workspace.trim());

  const load = useCallback(async () => {
    try {
      const base = {
        source: sourceDebounced || undefined,
        workspace: workspaceDebounced || undefined,
      };
      // Per-status totals for the chips — the loaded page is status-filtered,
      // so each count needs its own whole-ledger query (Traces pattern).
      const [res, ...countRes] = await Promise.all([
        listEpisodes({ ...base, status: status || undefined, limit: PAGE_SIZE, offset }),
        ...STATUSES.map((s) => listEpisodes({ ...base, status: s, limit: 1 })),
      ]);
      setEpisodes(res.episodes);
      setTotal(res.total);
      setCounts(Object.fromEntries(STATUSES.map((s, i) => [s, countRes[i].total])));
    } catch (err) {
      toast.error((err as MemoryApiError).message);
      setEpisodes([]);
    }
  }, [status, sourceDebounced, workspaceDebounced, offset]);

  // A page offset only makes sense within the filter set it was reached in.
  useEffect(() => {
    setOffset(0);
  }, [status, sourceDebounced, workspaceDebounced]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasFilters = status !== "" || source.trim() !== "" || workspace.trim() !== "";

  function clearFilters() {
    setStatus("");
    setSource("");
    setWorkspace("");
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <Activity className="size-6" /> Episodes
        </h1>
        <p className="text-sm text-muted-foreground">
          The ingestion ledger — what flowed in, what folded into the graph, what was declined
          and what dead-lettered.
        </p>
      </div>

      {/* filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex flex-wrap rounded-full bg-secondary p-1">
          <Chip active={status === ""} activeClass="bg-card text-foreground shadow-sm" onClick={() => setStatus("")}>
            All
          </Chip>
          {STATUSES.map((s) => (
            <Chip
              key={s}
              active={status === s}
              activeClass={STATUS_CHIP_ACTIVE[s]}
              onClick={() => setStatus(s)}
            >
              {s}
              {counts[s] != null && (
                <span className="ml-1.5 tabular-nums opacity-70">{counts[s]}</span>
              )}
            </Chip>
          ))}
        </div>
        <Input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder="source"
          className="w-40 font-mono text-xs"
        />
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder="workspace"
          className="w-52 font-mono text-xs"
        />
      </div>

      {episodes === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : episodes.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              {hasFilters
                ? "No episodes match these filters."
                : "Nothing in the ledger yet — POST an episode and it appears here."}
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
                  <th className="py-2.5 pl-4 font-medium">Event</th>
                  <th className="py-2.5 font-medium">Action</th>
                  <th className="py-2.5 font-medium">Source</th>
                  <th className="py-2.5 font-medium">Status</th>
                  <th className="py-2.5 font-medium">Scope</th>
                  <th className="py-2.5 pr-4 text-right font-medium">Occurred</th>
                </tr>
              </thead>
              <tbody>
                {episodes.map((e) => (
                  <tr
                    key={e.event_id}
                    onClick={() => setSelected(e.event_id)}
                    tabIndex={0}
                    role="button"
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        setSelected(e.event_id);
                      }
                    }}
                    className="cursor-pointer border-b border-border/50 transition-colors last:border-0 hover:bg-muted/60 focus-visible:outline-none focus-visible:bg-muted/60"
                  >
                    <td className="max-w-0 w-1/4 py-2.5 pl-4 pr-3">
                      <code className="block truncate text-xs" title={e.event_id}>
                        {e.event_id}
                      </code>
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-muted-foreground">{e.action_type}</td>
                    <td className="py-2.5 pr-3">
                      <SourceBadge source={e.source} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <Badge variant={STATUS_VARIANT[e.status]}>{e.status}</Badge>
                    </td>
                    <td className="py-2.5 pr-3">
                      <ScopeBadge scope={e.scope} scopeId={e.scope_id} />
                    </td>
                    <td
                      className="py-2.5 pr-4 text-right text-xs text-muted-foreground"
                      title={new Date(e.occurred_at).toLocaleString()}
                    >
                      {timeAgo(e.occurred_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span className="tabular-nums">
                Showing {total === 0 ? 0 : offset + 1}–{offset + episodes.length} of {total}
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

      {selected && <EpisodeDetail eventId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function Chip({
  active,
  activeClass,
  onClick,
  children,
}: {
  active: boolean;
  activeClass: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-full px-3 py-1 text-xs font-medium capitalize transition-colors",
        active ? activeClass : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** Slide-over for a ledger entry: lifecycle, envelope payload, and — first
 * thing — why it was declined or what failed. Raw content is never served by
 * the API (metadata-only by design); has_content just says it was captured. */
function EpisodeDetail({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [episode, setEpisode] = useState<EpisodeDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getEpisode(eventId)
      .then((res) => {
        if (!cancelled) setEpisode(res);
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  function copyId() {
    void navigator.clipboard?.writeText(eventId);
    toast.success("Event id copied.");
  }

  return (
    <SlideOver
      title={
        <>
          <span
            className={cn(
              "size-2.5 rounded-full",
              episode?.status === "processed" && "bg-[var(--success)]",
              episode?.status === "pending" && "bg-[var(--warning)]",
              episode?.status === "dropped" && "bg-muted-foreground",
              (episode === null || episode.status === "dead") && "bg-[var(--destructive)]",
            )}
          />
          Episode
        </>
      }
      onClose={onClose}
    >
      {error ? (
        <p className="text-sm text-[var(--destructive)]">{error}</p>
      ) : episode === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          {episode.dropped_reason && (
            <div className="rounded-lg bg-[var(--warning-bg)] px-3.5 py-2.5 text-xs text-[var(--warning)]">
              <div className="font-medium">Dropped</div>
              <div className="mt-1 whitespace-pre-wrap">{episode.dropped_reason}</div>
            </div>
          )}
          {episode.error && (
            <div className="rounded-lg bg-[var(--destructive-bg)] px-3.5 py-2.5 text-xs text-[var(--destructive)]">
              <div className="font-medium">
                {episode.status === "dead" ? "Dead-lettered" : "Last error"}
                {episode.error_count > 0 && ` · ${episode.error_count} attempt${episode.error_count === 1 ? "" : "s"}`}
              </div>
              <div className="mt-1 whitespace-pre-wrap">{episode.error}</div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={STATUS_VARIANT[episode.status]}>{episode.status}</Badge>
            <SourceBadge source={episode.source} />
            <ScopeBadge scope={episode.scope} scopeId={episode.scope_id} />
            {episode.workspace && <Badge variant="muted">ws:{episode.workspace}</Badge>}
            {episode.tier && <Badge variant="outline">{episode.tier}</Badge>}
            {episode.has_content && <Badge variant="info">content captured</Badge>}
          </div>

          <div>
            <div className="text-xs text-muted-foreground">Event id</div>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-xs">
                {episode.event_id}
              </code>
              <button
                onClick={copyId}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted"
              >
                <Copy className="size-3.5" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Field label="Action">
              <code className="text-xs">{episode.action_type}</code>
            </Field>
            <Field label="Lifecycle">{episode.lifecycle}</Field>
            <Field label="Occurred">{new Date(episode.occurred_at).toLocaleString()}</Field>
            <Field label="Ingested">{new Date(episode.ingested_at).toLocaleString()}</Field>
            <Field label="Processed">
              {episode.processed_at ? new Date(episode.processed_at).toLocaleString() : "—"}
            </Field>
            <Field label="Dead at">
              {episode.dead_at ? new Date(episode.dead_at).toLocaleString() : "—"}
            </Field>
          </div>

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">Payload</div>
            {Object.keys(episode.payload).length === 0 ? (
              <p className="text-sm text-muted-foreground">Empty payload.</p>
            ) : (
              <pre className="max-h-96 overflow-auto rounded-lg bg-muted p-3 text-[11px] leading-relaxed">
                {JSON.stringify(episode.payload, null, 2)}
              </pre>
            )}
          </div>
        </>
      )}
    </SlideOver>
  );
}
