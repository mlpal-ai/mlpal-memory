import { Bot, MessageCircleQuestion, Radar, Sparkles } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { NodeDetail } from "@/components/NodeDetail";
import { PacketMarkdown, type CitationKind } from "@/components/PacketMarkdown";
import { PassageDetail } from "@/components/PassageDetail";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type AnswerResponse,
  type HopEvent,
  MemoryApiError,
  type PassageOut,
  answerMemory,
  searchMemory,
  streamAnswer,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtMs, plural } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

const EXAMPLES = [
  "how does auth work",
  "what did we decide about the graph driver",
  "known failure modes in ingestion",
];

const MAX_HOPS = 3;

type AskRoute = "packet" | "answer" | "deep";

const ROUTES: Array<{ id: AskRoute; label: string; cost: string }> = [
  { id: "packet", label: "Packet", cost: "free · ~150ms · deterministic" },
  { id: "answer", label: "Answer", cost: "1 model call" },
  { id: "deep", label: "Deep search", cost: `up to ${MAX_HOPS + 1} calls · loops` },
];

/** What mode=hybrid returns: "short cited answer\n\n---\n\nfull packet". */
function splitHybrid(markdown: string): { answer: string; packet: string | null } {
  const sep = markdown.indexOf("\n\n---\n\n");
  if (sep === -1) return { answer: markdown, packet: null };
  return { answer: markdown.slice(0, sep), packet: markdown.slice(sep + 7) };
}

interface HopFinal {
  markdown: string;
  hops: number;
  trace: string[];
  invented_citations: number;
  model: string;
  elapsedMs: number;
}

export function Ask() {
  const [q, setQ] = useState("");
  const [workspace, setWorkspace] = useWorkspace();
  const [route, setRoute] = useState<AskRoute>("packet");
  const [agentMode, setAgentMode] = useState(false);
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [passages, setPassages] = useState<Map<string, PassageOut>>(new Map());
  const [loading, setLoading] = useState(false);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const [openPassage, setOpenPassage] = useState<PassageOut | null>(null);
  // deep-search stream state
  const [hopEvents, setHopEvents] = useState<HopEvent[]>([]);
  const [hopFinal, setHopFinal] = useState<HopFinal | null>(null);
  const [hopError, setHopError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [askedQ, setAskedQ] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  function resetResults() {
    setAnswer(null);
    setHopEvents([]);
    setHopFinal(null);
    setHopError(null);
  }

  async function ask(query: string) {
    const trimmed = query.trim();
    if (!trimmed) return;
    setQ(trimmed);
    setAskedQ(trimmed);
    abortRef.current?.abort();
    resetResults();

    const ws = workspace.trim() || undefined;
    // The packet is markdown-only; a parallel search with the same context
    // fetches the structured passages so chunk citations open in-place.
    const passagesPromise = searchMemory({ q: trimmed, workspace: ws, limit: 25, depth: 0 })
      .then((res) => setPassages(new Map(res.passages.map((p) => [p.id, p]))))
      .catch(() => setPassages(new Map()));

    if (route === "deep") {
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      const t0 = performance.now();
      try {
        await streamAnswer(
          { q: trimmed, workspace: ws, agent_mode: agentMode, max_hops: MAX_HOPS },
          (ev) => {
            if (ev.type === "answer") {
              setHopFinal({
                markdown: ev.markdown,
                hops: ev.hops,
                trace: ev.trace,
                invented_citations: ev.invented_citations,
                model: ev.model,
                elapsedMs: performance.now() - t0,
              });
            } else if (ev.type === "error") {
              setHopError(ev.detail);
            } else {
              setHopEvents((prev) => [...prev, ev]);
            }
          },
          controller.signal,
        );
      } catch (err) {
        setHopError((err as MemoryApiError).message);
      } finally {
        if (abortRef.current === controller) setStreaming(false);
      }
      return;
    }

    setLoading(true);
    try {
      const [ans] = await Promise.all([
        answerMemory({
          q: trimmed,
          workspace: ws,
          agent_mode: agentMode,
          // "Answer" costs the same single call as synthesized but keeps the
          // full packet attached, so the disclosure needs no second request.
          mode: route === "answer" ? "hybrid" : "packet",
        }),
        passagesPromise,
      ]);
      setAnswer(ans);
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setLoading(false);
    }
  }

  async function onCitation(kind: CitationKind, id: string) {
    if (kind === "node") {
      setOpenNode(id);
      return;
    }
    let passage = passages.get(id);
    if (!passage && askedQ) {
      // answer/search rankings diverge at the margins — retry with a deep
      // window before giving up (there is no single-chunk endpoint).
      try {
        const res = await searchMemory({
          q: askedQ,
          workspace: workspace.trim() || undefined,
          origin: "direct",
          limit: 100,
          depth: 0,
        });
        const merged = new Map(passages);
        for (const p of res.passages) merged.set(p.id, p);
        setPassages(merged);
        passage = merged.get(id);
      } catch {
        // fall through to the URI fallback
      }
    }
    if (passage) {
      setOpenPassage(passage);
    } else {
      // chunk fell outside every retrieval window — hand over the citable URI
      // instead of a dead end.
      void navigator.clipboard?.writeText(`memory://chunk/${id}`);
      toast.info("Chunk not in the current retrieval window — memory:// URI copied.");
    }
  }

  const hybrid = answer && answer.mode === "hybrid" ? splitHybrid(answer.markdown) : null;
  const busy = loading || streaming;
  const showEmpty = !busy && !answer && hopEvents.length === 0 && !hopFinal && !hopError;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <MessageCircleQuestion className="size-6" /> Ask memory
        </h1>
        <p className="text-sm text-muted-foreground">
          Three routes into the same governed store: a free deterministic packet, a one-call
          answer composed from it, or a deep search that loops retrieval until the question
          is answered. Every route carries memory:// citations and abstains honestly.
        </p>
      </div>

      {/* the route ladder — the user chooses what a question is allowed to cost */}
      <div className="inline-flex w-fit flex-wrap gap-1 rounded-lg bg-secondary p-1">
        {ROUTES.map((r) => (
          <button
            key={r.id}
            onClick={() => setRoute(r.id)}
            aria-pressed={route === r.id}
            className={cn(
              "flex flex-col items-start rounded-md px-3.5 py-1.5 text-left transition-colors",
              route === r.id
                ? "bg-card shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <span className="text-sm font-medium">{r.label}</span>
            <span
              className={cn(
                "text-[10px] tabular-nums",
                route === r.id ? "text-[var(--link)]" : "text-muted-foreground",
              )}
            >
              {r.cost}
            </span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void ask(q)}
          placeholder="Ask what the org remembers…"
          className="min-w-64 flex-1"
        />
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void ask(q)}
          placeholder="workspace (optional)"
          className="w-56 font-mono text-xs"
        />
        <button
          onClick={() => setAgentMode((v) => !v)}
          title="Agent mode: failed-attempt narrative becomes constraints"
          className={cn(
            "inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
            agentMode
              ? "border-transparent bg-[var(--info-bg)] text-[var(--info)]"
              : "border-border text-muted-foreground hover:bg-muted",
          )}
        >
          <Bot className="size-3.5" />
          Agent mode
        </button>
        <button
          onClick={() => void ask(q)}
          disabled={busy || !q.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          {route === "deep" ? <Radar className="size-4" /> : <Sparkles className="size-4" />}
          {route === "deep" ? "Search deep" : "Ask"}
        </button>
      </div>

      {answer && (
        <div className="flex flex-wrap items-center gap-2">
          <MetaChip label="took" value={fmtMs(answer.took_ms)} />
          <MetaChip label="facts" value={String(answer.facts)} />
          <MetaChip label="passages" value={String(answer.passages)} />
          {answer.synth_model && <MetaChip label="model" value={answer.synth_model} />}
          {answer.mode !== "packet" && (
            <InventedChip count={answer.invented_citations} />
          )}
          {answer.contested > 0 && (
            <MetaChip label="contested" value={String(answer.contested)} tone="warn" />
          )}
          {answer.gaps.length > 0 && (
            <MetaChip label="gaps" value={String(answer.gaps.length)} tone="warn" />
          )}
        </div>
      )}

      {/* ── deep search: the hop, watched live ─────────────────────────── */}
      {hopFinal && (
        <div className="flex flex-wrap items-center gap-2">
          <MetaChip label="hops" value={String(hopFinal.hops)} />
          <MetaChip label="model" value={hopFinal.model} />
          <MetaChip label="took" value={`${(hopFinal.elapsedMs / 1000).toFixed(1)}s`} />
          <InventedChip count={hopFinal.invented_citations} />
        </div>
      )}

      {hopFinal && (
        <Card>
          <CardContent className="py-5">
            <PacketMarkdown markdown={hopFinal.markdown} onCitation={onCitation} />
          </CardContent>
        </Card>
      )}

      {hopError && (
        <div className="rounded-lg bg-[var(--destructive-bg)] px-4 py-3 text-sm text-[var(--destructive)]">
          Deep search failed — {hopError}
        </div>
      )}

      {(streaming || (hopEvents.length > 0 && (hopFinal || hopError))) &&
        (hopFinal || hopError ? (
          <details className="rounded-lg border border-border bg-card px-4 py-3">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
              live trace · {plural(hopEvents.length, "step")}
            </summary>
            <TraceRows events={hopEvents} streaming={false} />
          </details>
        ) : (
          <Card>
            <CardContent className="py-4">
              <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <Radar className="size-3.5 animate-pulse text-[var(--accent)]" />
                Searching memory — live trace
              </div>
              <TraceRows events={hopEvents} streaming />
            </CardContent>
          </Card>
        ))}

      {loading ? (
        <p className="text-sm text-muted-foreground">Consulting memory…</p>
      ) : answer ? (
        <>
          <Card>
            <CardContent className="py-5">
              <PacketMarkdown
                markdown={hybrid ? hybrid.answer : answer.markdown}
                onCitation={onCitation}
              />
            </CardContent>
          </Card>
          {hybrid?.packet && (
            <details className="rounded-lg border border-border bg-card px-4 py-3">
              <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
                Show the packet this answer was composed from
              </summary>
              <div className="mt-2">
                <PacketMarkdown markdown={hybrid.packet} onCitation={onCitation} />
              </div>
            </details>
          )}
        </>
      ) : showEmpty ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <p className="text-sm text-muted-foreground">
              Ask a question and get the packet — try one of these:
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => void ask(ex)}
                  className="rounded-full border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {ex}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {openNode && <NodeDetail nodeId={openNode} onClose={() => setOpenNode(null)} />}
      {openPassage && <PassageDetail passage={openPassage} onClose={() => setOpenPassage(null)} />}
    </div>
  );
}

function traceLabel(ev: HopEvent): ReactNode {
  switch (ev.type) {
    case "retrieved":
      return (
        <>
          hop {ev.hop} · searching <em className="not-italic font-medium text-foreground">{ev.query}</em>{" "}
          · {plural(ev.citations, "citation")}
        </>
      );
    case "deciding":
      return ev.action === "answer" ? (
        <>hop {ev.hop} · deciding → answer</>
      ) : (
        <>hop {ev.hop} · deciding → search</>
      );
    case "early_stop":
      return (
        <>
          hop {ev.hop} · early stop — {ev.reason}
        </>
      );
    case "composing":
      return <>composing answer…</>;
    default:
      return null;
  }
}

function TraceRows({ events, streaming }: { events: HopEvent[]; streaming: boolean }) {
  if (events.length === 0) {
    return <p className="mt-1 text-sm text-muted-foreground">Opening the stream…</p>;
  }
  return (
    <ol className="mt-1 flex flex-col gap-1.5">
      {events.map((ev, i) => {
        const newest = streaming && i === events.length - 1;
        return (
          <li key={i} className="flex items-center gap-2.5 text-sm text-muted-foreground">
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-full",
                newest ? "animate-pulse bg-[var(--accent)]" : "bg-[var(--n400)]",
              )}
            />
            <span className={cn(newest && "text-foreground")}>{traceLabel(ev)}</span>
          </li>
        );
      })}
    </ol>
  );
}

function InventedChip({ count }: { count: number }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium",
        count === 0
          ? "bg-[var(--success-bg)] text-[var(--success)]"
          : "bg-[var(--warning-bg)] text-[var(--warning)]",
      )}
    >
      {plural(count, "invented citation")}
      {count === 0 && " ✓"}
    </span>
  );
}

function MetaChip({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-secondary px-3 py-1 text-xs font-medium",
        tone === "warn" && "bg-[var(--warning-bg)] text-[var(--warning)]",
      )}
    >
      <span className={cn("text-muted-foreground", tone === "warn" && "text-[var(--warning)]")}>
        {label}
      </span>
      <span className="tabular-nums">{value}</span>
    </span>
  );
}
