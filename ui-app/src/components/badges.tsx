import { Badge } from "@/components/ui/badge";

/** Soft-tinted source pills. Sources observed in the store: claude_code,
 * memory_file, repo_doc, skill, yodex, yodex_failed — unknowns fall back to
 * the neutral tint so a new collector never renders unstyled. */
const SOURCE_VARIANT: Record<string, "info" | "success" | "warning" | "destructive" | "secondary"> =
  {
    claude_code: "info",
    memory_file: "success",
    md_file: "success",
    repo_doc: "secondary",
    repo: "secondary",
    skill: "warning",
    yodex: "info",
    yodex_failed: "destructive",
  };

export function SourceBadge({ source }: { source: string | null }) {
  if (!source) return null;
  return <Badge variant={SOURCE_VARIANT[source] ?? "muted"}>{source}</Badge>;
}

export function ScopeBadge({ scope, scopeId }: { scope: string; scopeId?: string | null }) {
  return (
    <Badge variant="outline" title={scopeId ? `${scope}:${scopeId}` : scope}>
      {scope}
      {scopeId ? `:${scopeId}` : ""}
    </Badge>
  );
}
