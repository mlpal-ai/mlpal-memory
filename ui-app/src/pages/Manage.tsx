import { Archive, ShieldCheck, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { SourceBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type CuratePreviewResponse,
  type DocumentOut,
  MemoryApiError,
  curateConfirm,
  curatePreview,
  forgetDocument,
  listDocuments,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, plural, timeAgo } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

const INSTRUCTION_PLACEHOLDER =
  "e.g. 'the migration is complete — forget the play-by-play, keep only key facts'";

/** Curation — what memory should stop carrying. Two surfaces: natural-language
 * curation (model proposes a forget-list, the human confirms exactly those
 * ids) and per-document direct forget. Deletion is real, scoped, and audited. */
export function Manage() {
  const [workspace, setWorkspace] = useWorkspace();
  const [docs, setDocs] = useState<DocumentOut[] | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    listDocuments({ workspace: workspace.trim() || undefined, limit: 50 })
      .then((res) => {
        if (!cancelled) setDocs(res.documents);
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) {
          toast.error(err.message);
          setDocs([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workspace, refreshTick]);

  useEffect(() => {
    window.addEventListener("mlpal:identity-changed", refresh);
    return () => window.removeEventListener("mlpal:identity-changed", refresh);
  }, [refresh]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <Archive className="size-6" /> Manage
        </h1>
        <p className="text-sm text-muted-foreground">
          What memory should stop carrying. The model proposes; you confirm; the server only
          ever deletes what you confirmed — with an audit trail.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder="workspace — required for curation"
          aria-label="Workspace"
          className="w-64 font-mono text-xs"
        />
      </div>

      <Curation workspace={workspace.trim()} onExecuted={refresh} />

      <DirectForget docs={docs} onForgotten={refresh} />

      <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-xs text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-4 shrink-0" />
        <span>
          Forgetting is real deletion of verbatim content, scope-authorized and audited
          (episode <code>memory.forgotten</code>). Derived facts are governed separately.
        </span>
      </div>
    </div>
  );
}

// ── natural-language curation (two-phase) ───────────────────────────────────

function Curation({ workspace, onExecuted }: { workspace: string; onExecuted: () => void }) {
  const [instruction, setInstruction] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<CuratePreviewResponse | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [forgetting, setForgetting] = useState(false);

  // a preview is only meaningful against the workspace it was built for
  useEffect(() => {
    setPreview(null);
  }, [workspace]);

  async function runPreview() {
    setPreviewing(true);
    setPreview(null);
    try {
      const res = await curatePreview(instruction.trim(), workspace);
      setPreview(res);
      setChecked(new Set(res.candidates.map((c) => c.id)));
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setPreviewing(false);
    }
  }

  async function runForget() {
    const ids = [...checked];
    setForgetting(true);
    try {
      const res = await curateConfirm(workspace, ids);
      toast.success(
        `Forgot ${plural(res.forgotten, "document")} · ${res.purged_chunks} chunks purged — audited`,
      );
      setPreview(null);
      setInstruction("");
      onExecuted();
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setForgetting(false);
    }
  }

  function toggle(id: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Natural-language curation</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={INSTRUCTION_PLACEHOLDER}
          rows={2}
          aria-label="Curation instruction"
          className="w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            onClick={() => void runPreview()}
            disabled={previewing || !instruction.trim() || !workspace}
          >
            <Sparkles className="size-3.5" />
            {previewing ? "Consulting the curator…" : "Preview"}
          </Button>
          {!workspace && (
            <span className="text-xs text-muted-foreground">
              Set a workspace above — curation is always workspace-bounded.
            </span>
          )}
        </div>

        {preview && preview.candidates.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Nothing to forget — the curator kept all {plural(preview.keep_count, "document")}.
          </p>
        )}

        {preview && preview.candidates.length > 0 && (
          <>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    <th className="w-8 py-2.5 pl-3" aria-label="Select" />
                    <th className="py-2.5 font-medium">Document</th>
                    <th className="py-2.5 font-medium">Date</th>
                    <th className="py-2.5 font-medium">Usage</th>
                    <th className="py-2.5 pr-3 font-medium">Why forget it</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.candidates.map((c) => (
                    <tr key={c.id} className="border-b border-border/50 last:border-0">
                      <td className="py-2.5 pl-3">
                        <input
                          type="checkbox"
                          checked={checked.has(c.id)}
                          onChange={() => toggle(c.id)}
                          aria-label={`Forget ${c.title ?? c.id}`}
                          className="size-3.5 accent-[var(--accent)]"
                        />
                      </td>
                      <td className="max-w-0 w-2/5 truncate py-2.5 pr-3 font-medium">
                        {c.title ?? "untitled"}
                      </td>
                      <td className="py-2.5 pr-3 font-mono text-xs tabular-nums text-muted-foreground">
                        {fmtDate(c.valid_at)}
                      </td>
                      <td className="py-2.5 pr-3">
                        {c.served_count === 0 ? (
                          <Badge variant="warning">never served</Badge>
                        ) : (
                          <Badge variant="secondary">served ×{c.served_count}</Badge>
                        )}
                      </td>
                      <td className="py-2.5 pr-3 text-xs italic text-muted-foreground">
                        {c.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                Keeping {plural(preview.keep_count, "document")} · {preview.note}
              </span>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => void runForget()}
                disabled={forgetting || checked.size === 0}
              >
                <Trash2 className="size-3.5" />
                {forgetting ? "Forgetting…" : `Forget ${checked.size} selected`}
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── direct forget ───────────────────────────────────────────────────────────

function DirectForget({
  docs,
  onForgotten,
}: {
  docs: DocumentOut[] | null;
  onForgotten: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Direct forget</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {docs === null ? (
          <p className="px-4 pb-4 text-sm text-muted-foreground">Loading…</p>
        ) : docs.length === 0 ? (
          <p className="px-4 pb-4 text-sm text-muted-foreground">
            No documents in this scope — nothing to forget.
          </p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className="border-b border-border/50 last:border-0">
                  <td className="max-w-0 w-1/2 truncate py-2 pl-4 pr-3 font-medium">
                    {d.title ?? d.uri ?? "untitled"}
                  </td>
                  <td className="py-2 pr-3">
                    <SourceBadge source={d.source} />
                  </td>
                  <td className="py-2 pr-3 text-right text-xs tabular-nums text-muted-foreground">
                    {plural(d.chunks, "chunk")}
                  </td>
                  <td
                    className="py-2 pr-3 text-right text-xs text-muted-foreground"
                    title={new Date(d.ingested_at).toLocaleString()}
                  >
                    {timeAgo(d.ingested_at)}
                  </td>
                  <td className="w-36 py-2 pr-4 text-right">
                    <ForgetButton doc={d} onForgotten={onForgotten} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

/** Two-step inline confirm — no blocking window dialogs. The armed state
 * disarms itself after a short window so a stray click can't linger. */
function ForgetButton({ doc, onForgotten }: { doc: DocumentOut; onForgotten: () => void }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);

  async function run() {
    setBusy(true);
    try {
      const res = await forgetDocument(doc.id);
      toast.success(
        `Forgot "${res.title ?? "untitled"}" · ${res.purged_chunks} chunks purged — audited`,
      );
      onForgotten();
    } catch (err) {
      toast.error((err as MemoryApiError).message);
      setBusy(false);
      setArmed(false);
    }
  }

  return (
    <button
      onClick={() => (armed ? void run() : setArmed(true))}
      disabled={busy}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        armed
          ? "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/90"
          : "border-border text-muted-foreground hover:bg-[var(--destructive-bg)] hover:text-[var(--destructive)]",
      )}
    >
      <Trash2 className="size-3" />
      {busy ? "Forgetting…" : armed ? "Confirm forget?" : "Forget"}
    </button>
  );
}
