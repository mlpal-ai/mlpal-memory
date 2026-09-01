import { CalendarClock, History } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { NodeDetail } from "@/components/NodeDetail";
import { PacketMarkdown, type CitationKind } from "@/components/PacketMarkdown";
import { PassageDetail } from "@/components/PassageDetail";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type AnswerResponse,
  type DocumentOut,
  MemoryApiError,
  type MetricHistoryOut,
  type MetricValueOut,
  type PassageOut,
  answerMemory,
  getMetrics,
  listDocuments,
  searchMemory,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, fmtMs, plural } from "@/lib/format";
import { useDebounced } from "@/lib/use-debounced";
import { useWorkspace } from "@/lib/workspace";
import { DocumentDetail } from "@/pages/Documents";

const DAY_MS = 86_400_000;
const DEFAULT_QUESTION = "how much does the platform cost per day";

/** The bitemporal story page: when memory formed (ingestion), what each
 * watched value was over valid time (supersession), and the same question
 * answered as-of any point in the corpus (time travel). */
export function Timeline() {
  const [workspace, setWorkspace] = useWorkspace();
  const [docs, setDocs] = useState<DocumentOut[] | null>(null);
  const [metrics, setMetrics] = useState<MetricHistoryOut[] | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);

  const workspaceDebounced = useDebounced(workspace.trim());

  // stale-response guard: identity bootstrap can fire a reload while the first
  // fetch is in flight — only the LATEST request may write state, or the slower
  // stale response (wrong identity) silently wins the race.
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const [docsRes, metricsRes] = await Promise.all([
        // valid-time order: the page spans history instead of yesterday's batch
        listDocuments({ workspace: workspaceDebounced || undefined, limit: 100, order: "valid" }),
        getMetrics(workspaceDebounced || undefined),
      ]);
      if (seq !== loadSeq.current) return;
      setDocs(docsRes.documents);
      setMetrics(metricsRes.metrics);
    } catch (err) {
      if (seq !== loadSeq.current) return;
      toast.error((err as MemoryApiError).message);
      setDocs([]);
      setMetrics([]);
    }
  }, [workspaceDebounced]);

  useEffect(() => {
    void load();
    const reload = () => void load();
    window.addEventListener("mlpal:identity-changed", reload);
    return () => window.removeEventListener("mlpal:identity-changed", reload);
  }, [load]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <History className="size-6" /> Timeline
        </h1>
        <p className="text-sm text-muted-foreground">
          Watch an organization learn — when memory formed, how watched values were superseded,
          and what the answer would have been on any given day.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder="workspace — try aws-migration"
          aria-label="Workspace"
          className="w-64 font-mono text-xs"
        />
        <span className="text-xs text-muted-foreground">
          Demo data: sidebar identity org <code>x6c6-timeline</code> · user{" "}
          <code>x6c-replayer</code>, workspace <code>aws-migration</code>.
        </span>
      </div>

      <FormationTimeline docs={docs} onSelect={setSelectedDoc} />

      <WatchedValues metrics={metrics} />

      <TimeTravel docs={docs} workspace={workspaceDebounced} />

      {selectedDoc && (
        <DocumentDetail documentId={selectedDoc} onClose={() => setSelectedDoc(null)} />
      )}
    </div>
  );
}

// ── §2 memory formation ─────────────────────────────────────────────────────

/** Documents grouped by valid-time day, oldest first — the corpus forming. */
function FormationTimeline({
  docs,
  onSelect,
}: {
  docs: DocumentOut[] | null;
  onSelect: (id: string) => void;
}) {
  const groups = useMemo(() => {
    const byDay = new Map<string, DocumentOut[]>();
    for (const d of docs ?? []) {
      const day = fmtDate(d.valid_at ?? d.ingested_at);
      byDay.set(day, [...(byDay.get(day) ?? []), d]);
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [docs]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Memory formation</CardTitle>
      </CardHeader>
      <CardContent>
        {docs === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : groups.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No documents in this workspace yet — ingest something and its formation appears here.
          </p>
        ) : (
          <ol className="relative flex flex-col gap-4 border-l border-border pl-5">
            {groups.map(([day, dayDocs]) => (
              <li key={day} className="relative">
                <span
                  aria-hidden
                  className="absolute -left-[23px] top-1.5 size-2 rounded-full bg-[var(--accent)]"
                />
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-xs font-semibold tabular-nums">{day}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {plural(dayDocs.length, "doc")}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {dayDocs.map((d) => (
                    <button
                      key={d.id}
                      onClick={() => onSelect(d.id)}
                      title={d.title ?? d.uri ?? "untitled"}
                      className="inline-flex max-w-72 items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs transition-colors hover:bg-muted"
                    >
                      <SourceDot source={d.source} />
                      <span className="truncate font-medium">
                        {d.title ?? d.uri ?? "untitled"}
                      </span>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {d.chunks}
                      </span>
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

const SOURCE_DOT: Record<string, string> = {
  claude_code: "bg-[var(--info)]",
  md_file: "bg-[var(--success)]",
  memory_file: "bg-[var(--success)]",
  repo_doc: "bg-[var(--n500)]",
  skill: "bg-[var(--warning)]",
  yodex: "bg-[var(--info)]",
  yodex_failed: "bg-[var(--destructive)]",
};

function SourceDot({ source }: { source: string | null }) {
  return (
    <span
      title={source ?? undefined}
      className={cn(
        "size-2 shrink-0 rounded-full",
        (source && SOURCE_DOT[source]) || "bg-[var(--n400)]",
      )}
    />
  );
}

// ── §3 watched values ───────────────────────────────────────────────────────

interface Segment extends MetricValueOut {
  leftPct: number;
  widthPct: number;
}

/** Distinct, positive-duration validity windows, positioned on a shared
 * [first valid_at → now] axis. Extraction can emit duplicate or zero-width
 * observations; those stay in the value-history list but not the bar. */
function toSegments(values: MetricValueOut[]): Segment[] {
  const now = Date.now();
  const seen = new Set<string>();
  const windows = values.filter((v) => {
    if (!v.valid_at) return false;
    const start = Date.parse(v.valid_at);
    const end = v.invalid_at ? Date.parse(v.invalid_at) : now;
    if (end <= start) return false;
    const key = `${v.value}|${v.valid_at}|${v.invalid_at ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (windows.length === 0) return [];
  const origin = Math.min(...windows.map((v) => Date.parse(v.valid_at as string)));
  const span = Math.max(now - origin, 1);
  return windows.map((v) => {
    const start = Date.parse(v.valid_at as string);
    const end = v.invalid_at ? Date.parse(v.invalid_at) : now;
    return {
      ...v,
      leftPct: ((start - origin) / span) * 100,
      widthPct: Math.max(((end - start) / span) * 100, 0.8),
    };
  });
}

function WatchedValues({ metrics }: { metrics: MetricHistoryOut[] | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Watched values</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {metrics === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : metrics.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No watched metrics in this workspace yet. When memory observes a value change —
            a cost, a version — its whole history lands here.
          </p>
        ) : (
          metrics.map((m) => <MetricBar key={m.key} metric={m} />)
        )}
      </CardContent>
    </Card>
  );
}

/** One metric's supersession chain as a segmented validity bar: superseded
 * values greyed, the current one amber and running to "now". */
function MetricBar({ metric }: { metric: MetricHistoryOut }) {
  const [selected, setSelected] = useState<Segment | null>(null);
  const segments = useMemo(() => toSegments(metric.values), [metric.values]);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm font-medium">{metric.label}</div>
        <code className="text-[11px] text-muted-foreground">{metric.key}</code>
      </div>

      {segments.length === 0 ? (
        <p className="mt-1.5 text-xs text-muted-foreground">No dated values.</p>
      ) : (
        <>
          <div className="relative mt-1.5 h-9 overflow-hidden rounded-md bg-muted/40">
            {segments.map((s, i) => (
              <button
                key={`${s.valid_at}-${i}`}
                onClick={() => setSelected((cur) => (cur === s ? null : s))}
                title={`${s.display} · ${fmtDate(s.valid_at)} → ${
                  s.invalid_at ? fmtDate(s.invalid_at) : "now"
                }`}
                aria-pressed={selected === s}
                style={{ left: `${s.leftPct}%`, width: `${s.widthPct}%` }}
                className={cn(
                  "absolute inset-y-0 border-r-2 border-[var(--card)] px-1.5 text-left text-[11px] font-medium transition-colors last:border-r-0",
                  s.current
                    ? "bg-[var(--accent)] text-[var(--accent-foreground)]"
                    : "bg-[var(--n200)] text-[var(--n600)] hover:bg-[var(--n400)]/50 dark:bg-[var(--n800)] dark:text-[var(--n400)] dark:hover:bg-[var(--n600)]/60",
                  selected === s && "ring-2 ring-inset ring-[var(--ring)]",
                )}
              >
                {s.widthPct > 5 && (
                  <span className="flex h-full items-center gap-1.5 overflow-hidden tabular-nums">
                    {s.current && (
                      <span className="relative flex size-1.5 shrink-0">
                        <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
                        <span className="relative inline-flex size-1.5 rounded-full bg-current" />
                      </span>
                    )}
                    <span className="truncate">{s.value}</span>
                  </span>
                )}
              </button>
            ))}
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground">
            <span>{fmtDate(segments[0].valid_at)}</span>
            <span>now</span>
          </div>
        </>
      )}

      {selected && (
        <div className="mt-2 rounded-lg border border-border bg-background p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-sm font-medium">{selected.display}</span>
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
              {fmtDate(selected.valid_at)} → {selected.invalid_at ? fmtDate(selected.invalid_at) : "now"}
              {selected.current && " · current"}
            </span>
          </div>
          {selected.evidence_span && (
            <blockquote className="mt-2 border-l-2 border-[var(--accent)] pl-2.5 font-mono text-[11px] leading-relaxed text-muted-foreground">
              “{selected.evidence_span}”
            </blockquote>
          )}
        </div>
      )}

      {/* plain-text history — screen readers and anyone who prefers a list */}
      <details className="mt-1.5">
        <summary className="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground">
          value history · {metric.values.length}
        </summary>
        <ol className="mt-1 flex flex-col gap-0.5 pl-4 text-xs">
          {metric.values.map((v, i) => (
            <li key={i} className={cn("tabular-nums", !v.current && "text-muted-foreground")}>
              {v.display} — {fmtDate(v.valid_at)} → {v.invalid_at ? fmtDate(v.invalid_at) : "now"}
              {v.current && " (current)"}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

// ── §4 time travel ──────────────────────────────────────────────────────────

/** Ask the same question as-of any day in the corpus's valid-time range and
 * watch the packet flip as supersessions come and go. */
function TimeTravel({ docs, workspace }: { docs: DocumentOut[] | null; workspace: string }) {
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  // null = now (no as_of); otherwise a UTC epoch-day index on the slider.
  const [asOfDay, setAsOfDay] = useState<number | null>(null);
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const [openPassage, setOpenPassage] = useState<PassageOut | null>(null);

  const range = useMemo(() => {
    const days = (docs ?? [])
      .map((d) => d.valid_at ?? d.ingested_at)
      .filter((iso): iso is string => iso != null)
      .map((iso) => Math.floor(Date.parse(iso) / DAY_MS));
    if (days.length === 0) return null;
    return { min: Math.min(...days), max: Math.max(...days) };
  }, [docs]);

  const questionDebounced = useDebounced(question.trim(), 500);
  const asOfDayDebounced = useDebounced(asOfDay, 350);
  // End of the selected UTC day, so that day's observations are included.
  const asOfIso =
    asOfDayDebounced == null
      ? undefined
      : new Date((asOfDayDebounced + 1) * DAY_MS - 1000).toISOString();

  useEffect(() => {
    if (!questionDebounced) return;
    let cancelled = false;
    setLoading(true);
    answerMemory({
      q: questionDebounced,
      workspace: workspace || undefined,
      as_of: asOfIso,
      limit: 25,
    })
      .then((res) => {
        if (!cancelled) setAnswer(res);
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) toast.error(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [questionDebounced, workspace, asOfIso]);

  async function onCitation(kind: CitationKind, id: string) {
    if (kind === "node") {
      setOpenNode(id);
      return;
    }
    // The packet is markdown-only; fetch the structured passages on demand so
    // chunk citations open in-place (same fallback as the Ask page).
    try {
      const res = await searchMemory({
        q: questionDebounced,
        workspace: workspace || undefined,
        origin: "direct",
        limit: 100,
        depth: 0,
      });
      const passage = res.passages.find((p) => p.id === id);
      if (passage) {
        setOpenPassage(passage);
        return;
      }
    } catch {
      // fall through to the URI fallback
    }
    void navigator.clipboard?.writeText(`memory://chunk/${id}`);
    toast.info("Chunk not in the current retrieval window — memory:// URI copied.");
  }

  const sliderValue = asOfDay ?? range?.max ?? 0;
  const asOfLabel = asOfDay != null ? new Date(asOfDay * DAY_MS).toISOString().slice(0, 10) : null;
  const oneDay = range !== null && range.min === range.max;
  // undebounced position + in-flight state → immediate feedback while dragging
  const asking = loading || asOfDay !== asOfDayDebounced;
  const thumbPct =
    range && range.max > range.min
      ? ((sliderValue - range.min) / (range.max - range.min)) * 100
      : 50;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2 text-sm">
          <CalendarClock className="size-4" /> Time travel
        </CardTitle>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold tabular-nums",
            asOfLabel
              ? "bg-[var(--accent)] text-[var(--accent-foreground)]"
              : "bg-secondary text-secondary-foreground",
          )}
        >
          {asOfLabel ? `as of ${asOfLabel}` : "now"}
        </span>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask what the org knew…"
          aria-label="Question"
        />

        {range === null ? (
          <p className="text-sm text-muted-foreground">
            No dated documents — the slider needs a corpus to travel across.
          </p>
        ) : (
          <>
            <div className={cn("flex items-center gap-3", asOfLabel && "pb-4")}>
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                {new Date(range.min * DAY_MS).toISOString().slice(0, 10)}
              </span>
              <div className="relative w-full">
                <input
                  type="range"
                  min={range.min}
                  max={range.max}
                  step={1}
                  value={sliderValue}
                  disabled={oneDay}
                  onChange={(e) => setAsOfDay(Number(e.target.value))}
                  aria-label="As-of date"
                  className="w-full accent-[var(--accent)] disabled:opacity-40"
                />
                {asOfLabel && (
                  <span
                    style={{ left: `${Math.min(88, Math.max(12, thumbPct))}%` }}
                    className="absolute top-full flex -translate-x-1/2 items-center gap-1.5 whitespace-nowrap font-mono text-[10px] tabular-nums text-muted-foreground"
                  >
                    as of {asOfLabel}
                    {asking && (
                      <>
                        <span className="text-[var(--accent)]">· asking…</span>
                        <span className="size-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
                      </>
                    )}
                  </span>
                )}
              </div>
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                {new Date(range.max * DAY_MS).toISOString().slice(0, 10)}
              </span>
              <button
                onClick={() => setAsOfDay(null)}
                disabled={asOfDay === null}
                className="shrink-0 rounded-md border border-border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-40 enabled:hover:bg-muted"
              >
                Now
              </button>
            </div>
            {oneDay && (
              <p className="text-xs text-muted-foreground">
                This workspace's memory all dates to one day — time travel needs history. Try
                workspace <code>aws-migration</code>.
              </p>
            )}
          </>
        )}

        {answer && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="tabular-nums">{fmtMs(answer.took_ms)}</span>
            <span>·</span>
            <span className="tabular-nums">{plural(answer.facts, "fact")}</span>
            <span>·</span>
            <span className="tabular-nums">{plural(answer.passages, "passage")}</span>
            {loading && <span className="text-[var(--accent)]">consulting…</span>}
          </div>
        )}

        {answer ? (
          <div className={cn("transition-opacity", loading && "opacity-60")}>
            <PacketMarkdown markdown={answer.markdown} onCitation={onCitation} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {loading ? "Consulting memory…" : "Type a question to replay what the org knew."}
          </p>
        )}
      </CardContent>

      {openNode && <NodeDetail nodeId={openNode} onClose={() => setOpenNode(null)} />}
      {openPassage && <PassageDetail passage={openPassage} onClose={() => setOpenPassage(null)} />}
    </Card>
  );
}
