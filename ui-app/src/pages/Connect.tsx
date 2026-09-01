import { Check, Copy, Plug, ShieldCheck, TerminalSquare } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Managed routes /mcp through the same ingress as this UI, so same-origin works.
// Local self-host runs the MCP server as its own process on 8011.
const ORIGIN = window.location.origin;
const IS_LOCAL = /localhost|127\.0\.0\.1/.test(ORIGIN);
const MCP_URL = IS_LOCAL ? "http://localhost:8011/mcp" : `${ORIGIN}/mcp`;

const TOOLS: { name: string; desc: string }[] = [
  { name: "memory_answer", desc: "Grounded answer packet for a question: facts, citations, provenance. The tool agents should reach for first." },
  { name: "memory_search", desc: "Hybrid search over documents and facts (vector + lexical + graph)." },
  { name: "memory_get", desc: "Fetch a specific document or node by id for full context." },
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
  // Canonical command — the platform dashboard ships the same string verbatim.
  const addCommand = IS_LOCAL
    ? `claude mcp add mlpal-memory --transport http ${MCP_URL}`
    : `claude mcp add mlpal-memory --transport http ${MCP_URL} --header "X-API-Key: <your-key>"`;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Plug className="size-5" /> Connect Claude Code
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Give any Claude Code session read access to this memory. One command, then the
          agent answers org questions from memory instead of re-deriving them from files.
          Reads and export are always free.
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
              Self-hosted dev mode needs no key. Identity comes from the dev headers this
              UI already uses. For a production self-host, front the service with your auth
              and pass the key as an <code>X-API-Key</code> header.
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
            The agent-facing connector is <strong>read-only</strong>. Writing to memory
            happens through ingestion and this UI's curation tools, never through a tool
            an agent could be prompt-injected into calling.
          </p>
          <div className="mt-1 flex flex-col gap-2">
            {TOOLS.map((t) => (
              <div key={t.name} className="flex items-start gap-2">
                <Badge variant="outline" className="mt-0.5 shrink-0 font-mono text-[11px]">
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
