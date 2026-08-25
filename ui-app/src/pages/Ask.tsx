import { Bot, MessageCircleQuestion, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { NodeDetail } from "@/components/NodeDetail";
import { PacketMarkdown, type CitationKind } from "@/components/PacketMarkdown";
import { PassageDetail } from "@/components/PassageDetail";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type AnswerResponse,
  MemoryApiError,
  type PassageOut,
  answerMemory,
  searchMemory,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtMs } from "@/lib/format";

const EXAMPLES = [
  "how does auth work",
  "what did we decide about the graph driver",
  "known failure modes in ingestion",
];

export function Ask() {
  const [q, setQ] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [agentMode, setAgentMode] = useState(false);
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [passages, setPassages] = useState<Map<string, PassageOut>>(new Map());
  const [loading, setLoading] = useState(false);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const [openPassage, setOpenPassage] = useState<PassageOut | null>(null);

  async function ask(query: string) {
    const trimmed = query.trim();
    if (!trimmed) return;
    setQ(trimmed);
    setLoading(true);
    try {
      // The packet is markdown-only; a parallel search with the same context
      // fetches the structured passages so chunk citations open in-place.
      const [ans, res] = await Promise.all([
        answerMemory({ q: trimmed, workspace: workspace.trim() || undefined, agent_mode: agentMode }),
        searchMemory({ q: trimmed, workspace: workspace.trim() || undefined, limit: 25, depth: 0 })
          .catch(() => null),
      ]);
      setAnswer(ans);
      setPassages(new Map((res?.passages ?? []).map((p) => [p.id, p])));
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
    if (!passage && answer) {
      // answer/search rankings diverge at the margins — retry with a deep
      // window before giving up (there is no single-chunk endpoint).
      try {
        const res = await searchMemory({
          q: answer.query,
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

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <MessageCircleQuestion className="size-6" /> Ask memory
        </h1>
        <p className="text-sm text-muted-foreground">
          A deterministic memory packet — scope-resolved facts and verbatim evidence with
          memory:// citations. No LLM in the loop; it abstains honestly.
        </p>
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
          disabled={loading || !q.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          <Sparkles className="size-4" /> Ask
        </button>
      </div>

      {answer && (
        <div className="flex flex-wrap items-center gap-2">
          <MetaChip label="took" value={fmtMs(answer.took_ms)} />
          <MetaChip label="facts" value={String(answer.facts)} />
          <MetaChip label="passages" value={String(answer.passages)} />
          {answer.contested > 0 && (
            <MetaChip label="contested" value={String(answer.contested)} tone="warn" />
          )}
          {answer.gaps.length > 0 && (
            <MetaChip label="gaps" value={String(answer.gaps.length)} tone="warn" />
          )}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Consulting memory…</p>
      ) : answer ? (
        <Card>
          <CardContent className="py-5">
            <PacketMarkdown markdown={answer.markdown} onCitation={onCitation} />
          </CardContent>
        </Card>
      ) : (
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
      )}

      {openNode && <NodeDetail nodeId={openNode} onClose={() => setOpenNode(null)} />}
      {openPassage && <PassageDetail passage={openPassage} onClose={() => setOpenPassage(null)} />}
    </div>
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
