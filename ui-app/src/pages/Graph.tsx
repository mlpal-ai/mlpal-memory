import type { Core } from "cytoscape";
import { Waypoints } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { NodeDetail } from "@/components/NodeDetail";
import { PacketMarkdown } from "@/components/PacketMarkdown";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  MemoryApiError,
  type ProjectionResponse,
  type SearchResponse,
  getNode,
  getProjection,
  searchMemory,
} from "@/lib/api";

/** Resolve the design tokens cytoscape needs — canvas styles can't read CSS
 * vars, so they are materialized per render and re-applied on theme flips.
 * Tokens chain (--border → var(--n200)), so each is resolved through a probe
 * element's computed color rather than getPropertyValue's as-authored text. */
function tokens() {
  const probe = document.createElement("div");
  probe.style.display = "none";
  document.body.appendChild(probe);
  const v = (name: string) => {
    probe.style.color = `var(${name})`;
    return getComputedStyle(probe).color;
  };
  // scope → a step on the warm neutral scale: narrower scope = stronger ink
  // against the page ground, so the steps flip with the theme.
  const dark = document.documentElement.classList.contains("dark");
  const steps = dark
    ? { strong: "--n100", mid: "--n200", soft: "--n400", faint: "--n600" }
    : { strong: "--n600", mid: "--n500", soft: "--n400", faint: "--n200" };
  const resolved = {
    scopeFill: {
      user: v(steps.strong),
      team: v(steps.mid),
      org: v(steps.mid),
      repo: v(steps.soft),
      service: v(steps.soft),
      agent: v(steps.soft),
      global: v(steps.faint),
    } as Record<string, string>,
    fallbackFill: v(steps.soft),
    label: v("--foreground"),
    edge: v("--border"),
    edgeLabel: v("--muted-foreground"),
    accent: v("--accent"),
    destructive: v("--destructive"),
  };
  probe.remove();
  return resolved;
}

function applyStyle(cy: Core) {
  const t = tokens();
  cy.style()
    .resetToDefault()
    .selector("node")
    .style({
      label: "data(label)",
      color: t.label,
      "font-size": "10px",
      "font-family": "Outfit, system-ui, sans-serif",
      "text-wrap": "ellipsis",
      "text-max-width": "140px",
      "text-valign": "bottom",
      "text-margin-y": 6,
      "background-color": (el) => t.scopeFill[el.data("scope") as string] ?? t.fallbackFill,
      width: "data(size)",
      height: "data(size)",
      "border-width": (el) => (el.data("contested") ? 3 : 0),
      "border-color": t.destructive,
    })
    .selector("node:selected")
    .style({
      "background-color": t.accent,
      "border-width": 2,
      "border-color": t.accent,
    })
    .selector("edge")
    .style({
      label: "data(label)",
      "font-size": "8px",
      "font-family": "JetBrains Mono, monospace",
      color: t.edgeLabel,
      "line-color": t.edge,
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "target-arrow-color": t.edge,
      width: 1.2,
    })
    .update();
}

export function Graph() {
  const [q, setQ] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [drawn, setDrawn] = useState<{ nodes: number; edges: number } | null>(null);
  const [projection, setProjection] = useState<ProjectionResponse | null>(null);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  // the always-on markdown tier — what an agent session gets injected
  useEffect(() => {
    getProjection(4000).then(setProjection).catch(() => null);
  }, []);

  // restyle the canvas when the theme flips (tokens are materialized colors)
  useEffect(() => {
    const observer = new MutationObserver(() => {
      if (cyRef.current) applyStyle(cyRef.current);
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(
    () => () => {
      cyRef.current?.destroy();
      cyRef.current = null;
    },
    [],
  );

  async function explore() {
    const trimmed = q.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      const res = await searchMemory({
        q: trimmed,
        workspace: workspace.trim() || undefined,
        limit: 20,
        depth: 1,
      });
      setResult(res);
      // cytoscape is heavy — load it only when a graph is actually drawn
      const { default: cytoscape } = await import("cytoscape");
      if (!containerRef.current) return;
      if (!cyRef.current) {
        cyRef.current = cytoscape({ container: containerRef.current });
        cyRef.current.on("tap", "node", (ev) => setOpenNode(ev.target.id()));
        applyStyle(cyRef.current);
      }
      const cy = cyRef.current;
      cy.elements().remove();
      const seen = new Set<string>();
      for (const n of res.nodes) {
        seen.add(n.id);
        cy.add({
          data: {
            id: n.id,
            label: n.name.slice(0, 60),
            scope: n.scope,
            size: 22 + Math.min(24, (n.observed_count - 1) * 6),
            contested: n.contested,
          },
        });
      }
      // depth-1 edges mostly point at neighbors OUTSIDE the ranked node set
      // (e.g. the session hub every fact hangs off) — hydrate a bounded batch
      // of those endpoints so the neighborhood actually connects.
      const missing = new Set<string>();
      for (const e of res.edges) {
        if (!seen.has(e.src_id)) missing.add(e.src_id);
        if (!seen.has(e.dst_id)) missing.add(e.dst_id);
      }
      const neighbors = await Promise.all(
        [...missing].slice(0, 25).map((id) =>
          getNode(id, 0)
            .then((r) => r.nodes[0] ?? null)
            // inaccessible scopes 403 — the edge simply stays undrawn
            .catch(() => null),
        ),
      );
      for (const n of neighbors) {
        if (n && !seen.has(n.id)) {
          seen.add(n.id);
          cy.add({
            data: { id: n.id, label: n.name.slice(0, 60), scope: n.scope, size: 20, contested: false },
          });
        }
      }
      for (const e of res.edges) {
        if (seen.has(e.src_id) && seen.has(e.dst_id)) {
          cy.add({ data: { id: e.id, source: e.src_id, target: e.dst_id, label: e.type } });
        }
      }
      cy.layout({ name: "cose", animate: false, padding: 30 }).run();
      setDrawn({ nodes: cy.nodes().length, edges: cy.edges().length });
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <Waypoints className="size-6" /> Graph
        </h1>
        <p className="text-sm text-muted-foreground">
          The derived-memory neighborhood for a query — node size is how often a fact was
          observed, a red ring means it is contested. Click a node for its typed props.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void explore()}
          placeholder="Explore memory around…"
          className="min-w-64 flex-1"
        />
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void explore()}
          placeholder="workspace (optional)"
          className="w-52 font-mono text-xs"
        />
        <button
          onClick={() => void explore()}
          disabled={loading || !q.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          Explore
        </button>
      </div>

      <Card>
        <CardContent className="relative p-0">
          <div ref={containerRef} className="h-[26rem] w-full rounded-xl" />
          {result === null && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <p className="text-sm text-muted-foreground">
                {loading ? "Resolving…" : "Run a query to draw its neighborhood."}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {result !== null && drawn !== null && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="secondary">{drawn.nodes} nodes</Badge>
          <Badge variant="secondary">{drawn.edges} edges</Badge>
          <span>Scope shades: narrower memory renders stronger; selected turns amber.</span>
        </div>
      )}

      {projection && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-sm">Projection — the always-on tier</CardTitle>
            <span className="text-xs text-muted-foreground">
              {projection.fact_count} facts · ~{projection.estimated_tokens} tokens
              {projection.truncated && " · truncated"}
            </span>
          </CardHeader>
          <CardContent>
            <PacketMarkdown
              markdown={projection.markdown}
              onCitation={(kind, id) => kind === "node" && setOpenNode(id)}
            />
          </CardContent>
        </Card>
      )}

      {openNode && <NodeDetail nodeId={openNode} onClose={() => setOpenNode(null)} />}
    </div>
  );
}
