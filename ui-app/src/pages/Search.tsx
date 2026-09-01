import { Search as SearchIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ScopeBadge, SourceBadge } from "@/components/badges";
import { NodeDetail } from "@/components/NodeDetail";
import { PassageDetail } from "@/components/PassageDetail";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  MemoryApiError,
  type PassageOut,
  type SearchResponse,
  searchMemory,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, fmtScore } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

const SCOPES = ["", "user", "team", "org", "repo", "service", "agent"] as const;
const ORIGINS = ["", "direct", "derived"] as const;

export function Search() {
  const [q, setQ] = useState("");
  const [workspace, setWorkspace] = useWorkspace();
  const [scope, setScope] = useState<(typeof SCOPES)[number]>("");
  const [origin, setOrigin] = useState<(typeof ORIGINS)[number]>("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const [openPassage, setOpenPassage] = useState<PassageOut | null>(null);

  async function run() {
    const trimmed = q.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      const res = await searchMemory({
        q: trimmed,
        workspace: workspace.trim() || undefined,
        scope: scope || undefined,
        origin: origin || undefined,
        limit: 25,
        depth: 1,
      });
      setResult(res);
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setLoading(false);
    }
  }

  const empty = result !== null && result.nodes.length === 0 && result.passages.length === 0;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <SearchIcon className="size-6" /> Search
        </h1>
        <p className="text-sm text-muted-foreground">
          Hybrid retrieval across your accessible scopes — derived facts and verbatim passages,
          deduped narrowest-wins.
        </p>
      </div>

      {/* filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
          placeholder="Search memory…"
          className="min-w-64 flex-1"
        />
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
          placeholder="workspace (optional)"
          className="w-52 font-mono text-xs"
        />
        <button
          onClick={() => void run()}
          disabled={loading || !q.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          Search
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-full bg-secondary p-1">
          {SCOPES.map((s) => (
            <Chip key={s || "all"} active={scope === s} onClick={() => setScope(s)}>
              {s === "" ? "All scopes" : s}
            </Chip>
          ))}
        </div>
        <div className="inline-flex rounded-full bg-secondary p-1">
          {ORIGINS.map((o) => (
            <Chip key={o || "all"} active={origin === o} onClick={() => setOrigin(o)}>
              {o === "" ? "Both tiers" : o}
            </Chip>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Searching…</p>
      ) : result === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Type a query — results include derived facts (the graph) and direct passages
            (verbatim chunks with provenance).
          </CardContent>
        </Card>
      ) : empty ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nothing in your accessible scopes matches this query.
          </CardContent>
        </Card>
      ) : (
        <>
          {result.nodes.length > 0 && (
            <section className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold text-muted-foreground">
                Facts · {result.nodes.length}
              </h2>
              {result.nodes.map((n) => (
                <button
                  key={n.id}
                  onClick={() => setOpenNode(n.id)}
                  className="rounded-xl border border-border bg-card p-4 text-left shadow-sm transition-colors hover:border-ring"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="secondary">{n.type}</Badge>
                    <ScopeBadge scope={n.scope} scopeId={n.scope_id} />
                    {n.workspace && <Badge variant="muted">ws:{n.workspace}</Badge>}
                    {n.contested && <Badge variant="destructive">contested</Badge>}
                    {n.observed_count > 1 && <Badge variant="outline">×{n.observed_count}</Badge>}
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {fmtScore(n.score)}
                    </span>
                  </div>
                  <p className="mt-2 line-clamp-3 text-sm leading-relaxed">{n.name}</p>
                </button>
              ))}
            </section>
          )}

          {result.passages.length > 0 && (
            <section className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold text-muted-foreground">
                Passages · {result.passages.length}
              </h2>
              {result.passages.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setOpenPassage(p)}
                  className="rounded-xl border border-border bg-card p-4 text-left shadow-sm transition-colors hover:border-ring"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <SourceBadge source={p.source} />
                    <ScopeBadge scope={p.scope} scopeId={p.scope_id} />
                    {p.workspace && <Badge variant="muted">ws:{p.workspace}</Badge>}
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {fmtScore(p.score)}
                    </span>
                  </div>
                  <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                    {p.content}
                  </p>
                  <div className="mt-2 text-xs text-muted-foreground">
                    {p.document_title ?? p.document_uri ?? "untitled document"} ·{" "}
                    {fmtDate(p.valid_at)}
                  </div>
                </button>
              ))}
            </section>
          )}
        </>
      )}

      {openNode && <NodeDetail nodeId={openNode} onClose={() => setOpenNode(null)} />}
      {openPassage && <PassageDetail passage={openPassage} onClose={() => setOpenPassage(null)} />}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-full px-3 py-1 text-xs font-medium capitalize transition-colors",
        active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
