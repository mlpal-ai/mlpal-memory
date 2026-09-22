import { Bot, Check, Copy, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type HopOut,
  MemoryApiError,
  type TuneProposal,
  listHops,
  tuneDecide,
  tuneProposals,
} from "@/lib/api";
import { fmtDate, plural } from "@/lib/format";

/** HOPs — the harness profiles registered in this tenant and the owner's door on
 * their tuning loop (memory v10). A tune turn writes a proposal; it waits here (and
 * in `yodex tune list`) until a person decides. Rejecting with a reason teaches the
 * next turn; approving installs the candidate where it lives, so that verb stays
 * with the engine. */
export function Hops() {
  const [hops, setHops] = useState<HopOut[] | null>(null);
  const [proposals, setProposals] = useState<TuneProposal[] | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listHops(), tuneProposals()])
      .then(([h, p]) => {
        if (cancelled) return;
        setHops(h.hops);
        setProposals([...p.pending].reverse());
      })
      .catch((err: MemoryApiError) => {
        if (!cancelled) {
          toast.error(err.message);
          setHops([]);
          setProposals([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  useEffect(() => {
    window.addEventListener("mlpal:identity-changed", refresh);
    return () => window.removeEventListener("mlpal:identity-changed", refresh);
  }, [refresh]);

  const pending = (proposals ?? []).filter((p) => p.status === "pending");
  const decided = (proposals ?? []).filter((p) => p.status !== "pending");

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display flex items-center gap-2 text-4xl">
          <Bot className="size-6" /> HOPs
        </h1>
        <p className="text-sm text-muted-foreground">
          The harness profiles that read and write this memory, and the proposals their tuning
          loop is waiting on you to decide.
        </p>
      </div>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-muted-foreground">
          Pending your approval · {pending.length}
        </h2>
        {proposals === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : pending.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              Nothing waiting. A tune turn writes a proposal here when it has a candidate; until
              then the HOP keeps running on its installed version.
            </CardContent>
          </Card>
        ) : (
          pending.map((p) => <ProposalCard key={p.id} proposal={p} onDecided={refresh} />)
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-muted-foreground">
          Registered HOPs · {hops?.length ?? "…"}
        </h2>
        {hops === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : hops.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No HOP has registered in this tenant yet. A HOP registers itself at session start
              (<code className="font-mono text-xs">PUT /api/v1/memory/hops/&lt;name&gt;</code>) with
              its version, workspace and the topics it injects.
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr className="border-b border-border">
                    <th className="px-4 py-2 font-medium">HOP</th>
                    <th className="px-4 py-2 font-medium">Version</th>
                    <th className="px-4 py-2 font-medium">Owner</th>
                    <th className="px-4 py-2 font-medium">Tuning</th>
                    <th className="px-4 py-2 font-medium">Workspace</th>
                    <th className="px-4 py-2 font-medium">Topics</th>
                  </tr>
                </thead>
                <tbody>
                  {hops.map((h) => (
                    <tr key={h.name} className="border-b border-border last:border-0">
                      <td className="px-4 py-2 font-mono text-xs">{h.name}</td>
                      <td className="px-4 py-2 font-mono text-xs">{h.version ?? "—"}</td>
                      <td className="px-4 py-2 text-xs">{h.owner ?? "—"}</td>
                      <td className="px-4 py-2">
                        <Badge variant={h.mode === "auto" ? "warning" : "secondary"}>{h.mode}</Badge>{" "}
                        <Badge variant="outline">{h.sharing}</Badge>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs">{h.workspace ?? "—"}</td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">
                        {plural(h.topics.length, "topic")}
                        {h.topics.some((t) => t.inject === true) &&
                          ` · ${h.topics.filter((t) => t.inject === true).length} injected`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}
      </section>

      {decided.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground">Decided · {decided.length}</h2>
          <Card>
            <CardContent className="flex flex-col divide-y divide-border p-0">
              {decided.map((p) => (
                <div key={p.id} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
                  <Badge variant={p.status === "reject" ? "destructive" : "success"}>{p.status}</Badge>
                  <span className="font-mono text-xs">{p.hop}</span>
                  <span className="font-mono text-xs text-muted-foreground">{p.turn}</span>
                  <span className="text-xs text-muted-foreground">
                    {p.from_version} → {p.candidate_version ?? "advisory"}
                  </span>
                  {p.decision?.reason && (
                    <span className="basis-full text-xs text-muted-foreground">
                      “{p.decision.reason}”
                      {p.status === "reject" && " — folded as a build learning for the next turn"}
                    </span>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        </section>
      )}

      <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-xs text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-4 shrink-0" />
        <span>
          A decision needs a person's identity; an agent never approves its own tuning. A rejection's
          reason is written as a build learning the next turn reads first.
        </span>
      </div>
    </div>
  );
}

function ProposalCard({ proposal: p, onDecided }: { proposal: TuneProposal; onDecided: () => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const verdict = p.verdict as { final?: { score?: number; runs?: number }; disposition?: string } | null;
  const approveCommand = `yodex tune approve ${p.turn}`;  // the engine's shape: the turn id alone

  async function reject() {
    setBusy(true);
    try {
      const res = await tuneDecide({ hop: p.hop, turn: p.turn, decision: "reject", reason: reason.trim() });
      toast.success(
        res.learning_written
          ? "Rejected — the reason is a build learning for the next turn."
          : "Rejected.",
      );
      onDecided();
    } catch (err) {
      toast.error((err as MemoryApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <CardTitle className="text-sm">
          <span className="font-mono">{p.hop}</span>{" "}
          <span className="text-muted-foreground">
            {p.from_version} → {p.candidate_version ?? "advisory only"}
          </span>
        </CardTitle>
        <span className="ml-auto font-mono text-xs text-muted-foreground">{p.turn}</span>
        {verdict?.final?.score != null && (
          <Badge variant={verdict.disposition === "pass" ? "success" : "warning"}>
            golden {Math.round((verdict.final.score ?? 0) * 100)}%
            {verdict.final.runs != null && ` · ${verdict.final.runs} runs`}
          </Badge>
        )}
        {p.cost_usd != null && <Badge variant="outline">${p.cost_usd.toFixed(2)}</Badge>}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm leading-relaxed">{p.summary}</p>
        {p.proposals.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {p.proposals.map((it, i) => (
              <div key={i} className="rounded-md border border-border px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-1.5">
                  {it.kind && <Badge variant="secondary">{it.kind}</Badge>}
                  {it.knob && <code className="font-mono">{it.knob}</code>}
                  {it.change && (
                    <code className="font-mono text-muted-foreground">{JSON.stringify(it.change)}</code>
                  )}
                  <Badge variant={it.applicability === "enactable" ? "info" : "outline"} className="ml-auto">
                    {it.applicability ?? "proposal"}
                  </Badge>
                </div>
                {it.rationale && <p className="mt-1 text-muted-foreground">{it.rationale}</p>}
              </div>
            ))}
          </div>
        )}
        {p.facts.length > 0 && (
          <div className="text-xs text-muted-foreground">
            Cites {plural(p.facts.length, "distilled fact")}:{" "}
            {p.facts
              .slice(0, 4)
              .map((f) => String(f.line ?? f.key ?? ""))
              .filter(Boolean)
              .join(" · ")}
          </div>
        )}
        <div className="text-xs text-muted-foreground">
          proposed {fmtDate(p.at)}
          {p.actor && ` by ${p.actor}`}
          {p.candidate_path && (
            <>
              {" "}· candidate at <code className="font-mono">{p.candidate_path}</code>
            </>
          )}
          {p.twins.length > 0 && ` · ${plural(p.twins.length, "twin")}`}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {p.candidate_version && (
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5 text-xs">
              <Check className="size-3.5 text-[var(--success)]" />
              <span>
                To approve, install the candidate where it lives:{" "}
                <code className="font-mono">{approveCommand}</code>
              </span>
              <button
                type="button"
                title="Copy"
                className="rounded-md p-1 text-muted-foreground hover:text-foreground"
                onClick={() => {
                  void navigator.clipboard?.writeText(approveCommand).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  });
                }}
              >
                {copied ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
              </button>
            </div>
          )}
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setRejecting((v) => !v)}>
            <X className="size-3.5" /> Reject…
          </Button>
        </div>
        {rejecting && (
          <div className="flex flex-col gap-2 rounded-lg border border-[var(--destructive)]/40 p-2.5">
            <p className="text-xs text-muted-foreground">
              Say why. The reason is folded as a build learning the next tune turn reads before it
              proposes again.
            </p>
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. those anti-churn firings were the migration week; ignore that window"
              className="h-8 text-xs"
            />
            <div className="flex gap-2">
              <Button size="sm" variant="destructive" disabled={busy || reason.trim().length < 3} onClick={() => void reject()}>
                Reject this proposal
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
