import { Copy } from "lucide-react";
import { toast } from "sonner";

import { ScopeBadge, SourceBadge } from "@/components/badges";
import { Field, SlideOver } from "@/components/SlideOver";
import { Badge } from "@/components/ui/badge";
import { type PassageOut } from "@/lib/api";
import { fmtDate, fmtScore } from "@/lib/format";

/** Slide-over for a direct-memory passage: verbatim chunk content + the
 * parent-document context the search API resolves for citations. */
export function PassageDetail({ passage, onClose }: { passage: PassageOut; onClose: () => void }) {
  function copyUri() {
    void navigator.clipboard?.writeText(`memory://chunk/${passage.id}`);
    toast.success("memory:// URI copied.");
  }

  return (
    <SlideOver
      title={
        <>
          <span className="size-2.5 rounded-full bg-[var(--success)]" />
          Passage
        </>
      }
      onClose={onClose}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <SourceBadge source={passage.source} />
        <ScopeBadge scope={passage.scope} scopeId={passage.scope_id} />
        {passage.workspace && <Badge variant="muted">ws:{passage.workspace}</Badge>}
      </div>

      <div>
        <div className="text-xs text-muted-foreground">memory:// URI</div>
        <div className="mt-1 flex items-center gap-2">
          <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-xs">
            memory://chunk/{passage.id}
          </code>
          <button onClick={copyUri} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted">
            <Copy className="size-3.5" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <Field label="Document">{passage.document_title ?? "—"}</Field>
        <Field label="Valid at">{fmtDate(passage.valid_at)}</Field>
        <Field label="Score">
          <span className="tabular-nums">{fmtScore(passage.score)}</span>
        </Field>
        <Field label="Ordinal">#{passage.ordinal}</Field>
      </div>

      {passage.document_uri && (
        <div>
          <div className="text-xs text-muted-foreground">Document URI</div>
          <code className="mt-1 block truncate rounded bg-muted px-2 py-1 text-xs">
            {passage.document_uri}
          </code>
        </div>
      )}

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted-foreground">Verbatim content</div>
        <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-[11px] leading-relaxed">
          {passage.content}
        </pre>
      </div>
    </SlideOver>
  );
}
