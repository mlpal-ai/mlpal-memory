import { History, NotebookPen, Pencil, Plus, Save, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { NodeDetail } from "@/components/NodeDetail";
import { PacketMarkdown, type CitationKind } from "@/components/PacketMarkdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  MemoryApiError,
  NOTE_SECTIONS,
  type NoteOut,
  type NoteSummary,
  type NoteVersionOut,
  getNote,
  listNotes,
  loadIdentity,
  noteHistory,
  putNote,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, plural, timeAgo } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

const TEMPLATE = NOTE_SECTIONS.map((s) => `## ${s}\n`).join("\n");

type NoteRef = { scope: string; scopeId: string; workspace: string };

function sameRef(a: NoteRef | null, b: NoteSummary): boolean {
  return a !== null && a.scope === b.scope && a.scopeId === b.scope_id && a.workspace === b.workspace;
}

/** Workspace notes — the curated projection a workspace keeps for its people and
 * agents: five sections, versioned, citations back into the graph. Runs append to
 * Open threads; the nightly curation closes the stale ones and rebuilds the current
 * state block in Now; Decisions and Preferences are a person's alone. */
export function Notes() {
  const [workspace] = useWorkspace();
  const [notes, setNotes] = useState<NoteSummary[] | null>(null);
  const [selected, setSelected] = useState<NoteRef | null>(null);
  const [note, setNote] = useState<NoteOut | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [history, setHistory] = useState<NoteVersionOut[] | null>(null);
  const [openNode, setOpenNode] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    listNotes()
      .then((res) => {
        if (cancelled) return;
        setNotes(res.notes);
        setSelected((prev) => {
          if (prev && res.notes.some((n) => sameRef(prev, n))) return prev;
          const ws = workspace.trim();
          const match = res.notes.find((n) => n.workspace === ws) ?? res.notes[0];
          return match ? { scope: match.scope, scopeId: match.scope_id, workspace: match.workspace } : null;
        });
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) {
          toast.error(err.message);
          setNotes([]);
        }
      });
    return () => {
      cancelled = true;
    };
    // the sidebar workspace only picks the initial selection
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  useEffect(() => {
    window.addEventListener("mlpal:identity-changed", refresh);
    return () => window.removeEventListener("mlpal:identity-changed", refresh);
  }, [refresh]);

  useEffect(() => {
    if (!selected) {
      setNote(null);
      return;
    }
    let cancelled = false;
    setHistory(null);
    setEditing(false);
    getNote(selected.scope, selected.scopeId, selected.workspace)
      .then((n) => {
        if (!cancelled) setNote(n);
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) {
          toast.error(err.message);
          setNote(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selected, tick]);

  async function save() {
    if (!selected || !note) return;
    setSaving(true);
    try {
      const saved = await putNote(selected.scope, selected.scopeId, selected.workspace, {
        body: draft,
        reason: reason.trim() || undefined,
        base_version: note.version,
      });
      setNote(saved);
      setEditing(false);
      setReason("");
      toast.success(`Saved as version ${saved.version}.`);
      refresh();
    } catch (err) {
      const e = err as MemoryApiError;
      toast.error(e.status === 409 ? `${e.message} — reload and merge your change.` : e.message);
    } finally {
      setSaving(false);
    }
  }

  async function create() {
    const ws = workspace.trim();
    const orgId = notes?.find((n) => n.scope === "org")?.scope_id ?? loadIdentity().orgId;
    try {
      const created = await putNote("org", orgId, ws, {
        body: TEMPLATE,
        title: ws ? `${ws} notes` : "Workspace notes",
        reason: "created in the memory UI",
      });
      toast.success(`Note created for ${ws || "the org"}.`);
      setSelected({ scope: created.scope, scopeId: created.scope_id, workspace: created.workspace });
      refresh();
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    }
  }

  async function loadHistory() {
    if (!selected) return;
    try {
      const h = await noteHistory(selected.scope, selected.scopeId, selected.workspace);
      setHistory(h.versions);
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    }
  }

  function onCitation(kind: CitationKind, id: string) {
    if (kind === "node") {
      setOpenNode(id);
    } else {
      void navigator.clipboard?.writeText(`memory://chunk/${id}`);
      toast.message("Passage URI copied — open it from Search or Documents.");
    }
  }

  const ws = workspace.trim();
  const hasNoteForWorkspace = (notes ?? []).some((n) => n.workspace === ws);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <NotebookPen className="size-6" /> Notes
        </h1>
        <p className="text-sm text-muted-foreground">
          What a workspace keeps in front of its people and agents: Now, Decisions, Open threads,
          Preferences, Pointers. Runs append; the nightly curation closes stale threads and rebuilds
          the current state; Decisions and Preferences are yours alone.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_1fr]">
        <Card className="h-fit">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-sm">Workspaces</CardTitle>
            {!hasNoteForWorkspace && notes !== null && (
              <button
                onClick={() => void create()}
                title={`Create a note for ${ws || "the org"}`}
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Plus className="size-4" />
              </button>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-1">
            {notes === null ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : notes.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No notes yet.{" "}
                <button onClick={() => void create()} className="link-accent">
                  Create one for {ws || "the org"}
                </button>
                .
              </p>
            ) : (
              notes.map((n) => (
                <button
                  key={n.id}
                  onClick={() => setSelected({ scope: n.scope, scopeId: n.scope_id, workspace: n.workspace })}
                  className={cn(
                    "flex flex-col items-start gap-0.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                    sameRef(selected, n) ? "bg-accent/15 font-semibold" : "hover:bg-muted",
                  )}
                >
                  <span className="font-mono text-xs">{n.workspace || "(org)"}</span>
                  <span className="text-[11px] font-normal text-muted-foreground">
                    v{n.version} · {n.updated_at ? timeAgo(n.updated_at) : "—"} · {n.scope}
                  </span>
                </button>
              ))
            )}
          </CardContent>
        </Card>

        {note === null ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              {notes !== null && notes.length === 0
                ? "A note holds what a workspace wants every session to know."
                : "Select a workspace."}
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader className="flex-row flex-wrap items-center gap-2">
              <CardTitle className="text-base">{note.title ?? `${note.workspace || "org"} notes`}</CardTitle>
              <Badge variant="outline">v{note.version}</Badge>
              {note.stale_citations.length > 0 && (
                <Badge
                  variant="warning"
                  title={note.stale_citations.map((c) => `${c.citation}${c.reason ? ` (${c.reason})` : ""}`).join("\n")}
                >
                  {plural(note.stale_citations.length, "stale citation")}
                </Badge>
              )}
              <span className="text-xs text-muted-foreground">
                {note.updated_by && `by ${note.updated_by} · `}
                {fmtDate(note.updated_at)}
              </span>
              <div className="ml-auto flex gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => (history ? setHistory(null) : void loadHistory())}>
                  <History className="size-3.5" /> History
                </Button>
                {editing ? (
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                    <X className="size-3.5" /> Cancel
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setDraft(note.body);
                      setEditing(true);
                    }}
                  >
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {history && (
                <div className="rounded-lg border border-border">
                  <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                    {plural(history.length, "version")}
                  </div>
                  <div className="flex flex-col divide-y divide-border">
                    {history.map((v) => (
                      <div key={v.version} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-xs">
                        <Badge variant="outline">v{v.version}</Badge>
                        <span className="text-muted-foreground">{fmtDate(v.created_at)}</span>
                        {v.updated_by && <span>{v.updated_by}</span>}
                        {v.reason && <span className="text-muted-foreground">— {v.reason}</span>}
                        <span className="ml-auto tabular-nums text-muted-foreground">{v.chars} chars</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {editing ? (
                <div className="flex flex-col gap-2">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={18}
                    spellCheck={false}
                    className="w-full rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="reason (optional, kept in the history)"
                      className="h-8 flex-1 text-xs"
                    />
                    <Button size="sm" disabled={saving || draft === note.body} onClick={() => void save()}>
                      <Save className="size-3.5" /> Save as v{note.version + 1}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Keep the five <code>##</code> headings; the server rejects a body without them.
                    Saving checks that nobody wrote v{note.version + 1} first.
                  </p>
                </div>
              ) : (
                NOTE_SECTIONS.map((section) => (
                  <section key={section}>
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {section}
                    </h3>
                    {(note.sections[section] ?? "").trim() === "" ? (
                      <p className="text-sm text-muted-foreground">—</p>
                    ) : (
                      <PacketMarkdown markdown={note.sections[section]} onCitation={onCitation} />
                    )}
                  </section>
                ))
              )}
            </CardContent>
          </Card>
        )}
      </div>
      {openNode && <NodeDetail nodeId={openNode} onClose={() => setOpenNode(null)} />}
    </div>
  );
}
