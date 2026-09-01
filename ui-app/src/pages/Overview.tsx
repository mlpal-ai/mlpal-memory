import {
  Database,
  History,
  Inbox,
  Layers,
  type LucideIcon,
  MessageCircleQuestion,
  Search as SearchIcon,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";

import { SourceBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type MemoryApiError, type StoreStats, getStats } from "@/lib/api";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/format";
import { useDismissed } from "@/lib/use-dismissed";

export function Overview() {
  const [stats, setStats] = useState<StoreStats | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(() => {
    setLoadFailed(false);
    getStats()
      .then(setStats)
      .catch((err: MemoryApiError) => {
        toast.error(err.message);
        setLoadFailed(true);
      });
  }, []);

  useEffect(() => {
    // identity edits in the sidebar change what the tenant-bounded stats show
    load();
    window.addEventListener("mlpal:identity-changed", load);
    return () => window.removeEventListener("mlpal:identity-changed", load);
  }, [load]);

  const empty = stats !== null && stats.documents === 0 && stats.nodes === 0 && stats.episodes === 0;

  const sourceData = Object.entries(stats?.by_source ?? {})
    .sort(([, a], [, b]) => b - a)
    .map(([source, documents]) => ({ source, documents }));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display text-4xl">Memory overview</h1>
        <p className="text-sm text-muted-foreground">
          {empty
            ? "A fresh store — ingest something and it appears here."
            : "Store composition for your tenant — direct passages, derived facts, and where they came from."}
        </p>
      </div>

      {loadFailed && (
        <div className="rounded-lg bg-[var(--warning-bg)] px-4 py-3 text-sm text-[var(--warning)]">
          Stats failed to load —{" "}
          <button onClick={load} className="font-medium underline">
            retry
          </button>
        </div>
      )}

      <HowMemoryWorks />

      {empty ? (
        <EmptyStore />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Documents" value={stats?.documents} />
            <Stat label="Chunks" value={stats?.chunks} />
            <Stat label="Facts (nodes)" value={stats?.nodes} />
            <Stat label="Edges" value={stats?.edges} />
            <Stat label="Episodes" value={stats?.episodes} />
            <Stat
              label="Contested"
              value={stats?.contested}
              tone={stats && stats.contested > 0 ? "warn" : "good"}
            />
          </div>

          {sourceData.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Documents by source</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-44">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={sourceData} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                      <XAxis
                        dataKey="source"
                        tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                        axisLine={{ stroke: "var(--border)" }}
                        tickLine={false}
                      />
                      <YAxis
                        allowDecimals={false}
                        tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        cursor={{ fill: "var(--muted)" }}
                        contentStyle={{
                          background: "var(--card)",
                          border: "1px solid var(--border)",
                          borderRadius: 8,
                          fontSize: 12,
                          color: "var(--foreground)",
                        }}
                      />
                      <Bar dataKey="documents" fill="var(--accent)" radius={[3, 3, 0, 0]} maxBarSize={44} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {sourceData.map((s) => (
                    <span key={s.source} className="inline-flex items-center gap-1.5 text-xs">
                      <SourceBadge source={s.source} />
                      <span className="tabular-nums text-muted-foreground">{s.documents}</span>
                    </span>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Facts by scope & lifecycle</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <BreakdownRow label="Scope" entries={stats?.by_scope ?? {}} />
                <BreakdownRow
                  label="Lifecycle"
                  entries={stats?.by_status ?? {}}
                  variantFor={(k) => (k === "published" ? "success" : "secondary")}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-sm">Top workspaces</CardTitle>
                <Link to="/search" className="text-xs link-accent">
                  search a workspace
                </Link>
              </CardHeader>
              <CardContent className="flex flex-col gap-1.5">
                {(stats?.top_workspaces ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No workspace-faceted facts yet.</p>
                ) : (
                  (stats?.top_workspaces ?? []).map((w) => (
                    <div key={w.workspace} className="flex items-center gap-2.5 px-1 py-0.5 text-sm">
                      <code className="flex-1 truncate text-xs">{w.workspace}</code>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {plural(w.nodes, "fact")}
                      </span>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

/** First-user orientation: the whole system in three steps, each linking to
 * the page that shows it live. Dismissable and persisted. */
function HowMemoryWorks() {
  const [dismissed, dismiss] = useDismissed("mlpal.memory.how-it-works");
  if (dismissed) return null;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">How memory works</CardTitle>
        <button
          onClick={dismiss}
          aria-label="Dismiss"
          className="rounded-md p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-3">
          <HowStep
            n={1}
            icon={Inbox}
            title="Experience flows in"
            body="Sessions, docs and PDFs arrive through a governed pipeline — every event ledgered, curated, forgettable."
            to="/manage"
            linkLabel="Manage"
          />
          <HowStep
            n={2}
            icon={Layers}
            title="Two tiers, bitemporally stamped"
            body="Verbatim evidence plus typed facts derived from it — each stamped with when it was true and when memory learned it."
            to="/timeline"
            linkLabel="Timeline"
            LinkIcon={History}
          />
          <HowStep
            n={3}
            icon={MessageCircleQuestion}
            title="Ask any route"
            body="A free deterministic packet, a one-call answer, or a deep search that loops retrieval — all cited, all honest about gaps."
            to="/ask"
            linkLabel="Ask"
          />
        </div>
      </CardContent>
    </Card>
  );
}

function HowStep({
  n,
  icon: Icon,
  title,
  body,
  to,
  linkLabel,
  LinkIcon,
}: {
  n: number;
  icon: LucideIcon;
  title: string;
  body: string;
  to: string;
  linkLabel: string;
  LinkIcon?: LucideIcon;
}) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border p-3.5">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-[var(--accent)]" />
        <span className="text-sm font-semibold">
          {n}. {title}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{body}</p>
      <Link to={to} className="mt-auto inline-flex items-center gap-1 pt-1 text-xs link-accent">
        {LinkIcon && <LinkIcon className="size-3" />}
        {linkLabel} →
      </Link>
    </div>
  );
}

function EmptyStore() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
        <Database className="size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          Nothing in memory for this tenant yet. Ingest an episode or a document and the store
          composition appears here.
        </p>
        <pre className="w-full max-w-2xl overflow-auto rounded-lg bg-muted p-3 text-left text-[11px] leading-relaxed">
{`curl -X POST http://localhost:8000/api/v1/documents \\
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: user" \\
  -H "Content-Type: application/json" \\
  -d '{"scope": "org", "scope_id": "local", "source": "repo_doc",
       "title": "Hello memory", "content": "The first thing this org remembers."}'`}
        </pre>
        <div className="flex gap-2">
          <Link
            to="/ask"
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            <MessageCircleQuestion className="size-4" /> Ask memory
          </Link>
          <Link
            to="/search"
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted"
          >
            <SearchIcon className="size-4" /> Search
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function BreakdownRow({
  label,
  entries,
  variantFor,
}: {
  label: string;
  entries: Record<string, number>;
  variantFor?: (key: string) => "success" | "secondary";
}) {
  const sorted = Object.entries(entries).sort(([, a], [, b]) => b - a);
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</div>
      {sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">—</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {sorted.map(([key, count]) => (
            <span key={key} className="inline-flex items-center gap-1.5 text-xs">
              <Badge variant={variantFor ? variantFor(key) : "secondary"}>{key}</Badge>
              <span className="tabular-nums text-muted-foreground">{count}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | undefined;
  tone?: "good" | "warn";
}) {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div
          className={cn(
            "metric mt-1 tabular-nums",
            tone === "warn" && "text-[var(--warning)]",
            tone === "good" && "text-[var(--success)]",
          )}
        >
          {value != null ? value.toLocaleString() : "…"}
        </div>
      </CardContent>
    </Card>
  );
}
