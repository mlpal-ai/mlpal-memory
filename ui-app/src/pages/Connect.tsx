import { Check, Copy, Plug, ShieldCheck, TerminalSquare } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { loadIdentity } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Managed routes /mcp through the same ingress as this UI, so same-origin works.
// Local self-host runs the MCP server as its own process on 8011.
const ORIGIN = window.location.origin;
const IS_LOCAL = /localhost|127\.0\.0\.1/.test(ORIGIN);
const MCP_URL = IS_LOCAL ? "http://localhost:8011/mcp" : `${ORIGIN}/mcp`;

const READ_TOOLS: { name: string; desc: string }[] = [
  { name: "memory_answer", desc: "Grounded answer packet for a question: facts, citations, provenance. The tool agents should reach for first." },
  { name: "memory_search", desc: "Hybrid search over documents and facts (vector + lexical + graph)." },
  { name: "memory_get", desc: "Fetch a specific document or node by id for full context." },
  { name: "memory_document", desc: "A verbatim document with its chunks, by id." },
  { name: "memory_brief", desc: "The builder's brief for a HOP: its watched facts, learnings, scores and deviations." },
  { name: "memory_notes", desc: "The workspace notes (Now, Decisions, Open threads, Preferences, Pointers)." },
];
const WRITE_TOOLS: { name: string; desc: string }[] = [
  { name: "memory_write", desc: "A claim with evidence: a learning, a preference, a state value, a pinned fact. Learnings start on probation and earn trust from the runs that see them." },
  { name: "memory_endorse", desc: "A person's endorsement (or pin). Needs a person's identity; an agent's own endorsement is refused." },
  { name: "memory_retract", desc: "Soft-delete a memory with a reason; as-of reads keep the history." },
];

function CommandBlock({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative rounded-md border border-border bg-muted/40">
      <pre className="overflow-x-auto p-3 pr-12 text-[13px] leading-relaxed">
        <code>{command}</code>
      </pre>
      <button
        type="button"
        title="Copy"
        className="absolute right-2 top-2 rounded-md border border-border bg-card p-1.5 text-muted-foreground hover:text-foreground"
        onClick={() => {
          navigator.clipboard.writeText(command).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}

export function Connect() {
  // Canonical command — the platform dashboard ships the same string verbatim. A local
  // stack in dev mode needs no key; one with a key file (or the managed service) does.
  const keyed = !IS_LOCAL || Boolean(loadIdentity().apiKey);
  const addCommand = keyed
    ? `claude mcp add mlpal-memory --transport http ${MCP_URL} --header "X-API-Key: <your-key>"`
    : `claude mcp add mlpal-memory --transport http ${MCP_URL}`;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Plug className="size-5" /> Connect Claude Code
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Give any Claude Code session (or yodex) this memory. One command, then the agent
          answers org questions from memory instead of re-deriving them from files, and what
          it learns comes back as governed claims. Reads and export are always free.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TerminalSquare className="size-4" /> 1. Add the MCP server
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <CommandBlock command={addCommand} />
          {IS_LOCAL ? (
            <p className="text-sm text-muted-foreground">
              Dev mode needs no key: identity comes from the dev headers this UI already uses.
              For an instance other people reach, mint keys with{" "}
              <code>python -m mlpal_memory_graph.tools.api_keys new</code>, start the production
              overlay, and pass the key as the <code>X-API-Key</code> header above (see
              docs/SELF_HOSTING.md). Each key is pinned to one tenant.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Create an API key with the <code>memory.read</code> scope in your MLPal
              dashboard, then paste it into the header above. The key pins the memory to
              your org and user, so agents only ever see what you can see.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">2. Ask through memory</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Nothing else to configure. In any session, questions about your org's past
            decisions, configs, and numbers now resolve through memory:
          </p>
          <CommandBlock command={`claude -p "How much does the platform cost per day now, after the migration?"`} />
          <p className="text-sm text-muted-foreground">
            The agent calls <code>memory_answer</code> and gets a cited packet. In our
            measured study (10 org questions, same model and cost per task) this turned
            6/10 correct into 10/10, and 0/3 into 3/3 on questions whose answer had
            changed over time.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="size-4" /> What the agent can do
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">
            Reads are free and deterministic. Writes are <strong>governed</strong>: every claim
            carries evidence, a learning is on probation until runs that saw it succeed, a
            person's endorsement is the one signal an agent cannot compute, and a stop-hook's
            unprompted learnings reach only their author and HOP. Deletion stays with this UI's
            curation tools.
          </p>
          <div className="mt-1 text-xs font-medium text-muted-foreground">Reads</div>
          <div className="flex flex-col gap-2">
            {READ_TOOLS.map((t) => (
              <div key={t.name} className="flex items-start gap-2">
                <Badge variant="outline" className="mt-0.5 shrink-0 font-mono text-[11px]">
                  {t.name}
                </Badge>
                <span className="text-sm text-muted-foreground">{t.desc}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 text-xs font-medium text-muted-foreground">Governed writes</div>
          <div className="flex flex-col gap-2">
            {WRITE_TOOLS.map((t) => (
              <div key={t.name} className="flex items-start gap-2">
                <Badge variant="info" className="mt-0.5 shrink-0 font-mono text-[11px]">
                  {t.name}
                </Badge>
                <span className="text-sm text-muted-foreground">{t.desc}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
