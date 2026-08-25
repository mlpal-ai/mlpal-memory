import Markdown, { defaultUrlTransform } from "react-markdown";

import { cn } from "@/lib/cn";

const MEMORY_URI = /^memory:\/\/(node|chunk)\/([\w-]+)$/;

export type CitationKind = "node" | "chunk";

/** Renders a memory packet (the /memory/answer llms.txt-style markdown) in the
 * design system. memory:// citations become clickable badges that open the
 * cited node/chunk in a slide-over. */
export function PacketMarkdown({
  markdown,
  onCitation,
}: {
  markdown: string;
  onCitation: (kind: CitationKind, id: string) => void;
}) {
  return (
    <div className="text-sm leading-relaxed">
      <Markdown
        // default transform strips unknown protocols — memory:// must survive.
        urlTransform={(url) => (MEMORY_URI.test(url) ? url : defaultUrlTransform(url))}
        components={{
          h1: ({ children }) => <h1 className="display mb-2 text-3xl">{children}</h1>,
          h2: ({ children }) => (
            <h2 className="mb-2 mt-6 border-b border-border pb-1.5 font-display text-xl font-[600]">
              {children}
            </h2>
          ),
          p: ({ children }) => <p className="my-2">{children}</p>,
          ul: ({ children }) => <ul className="my-2 flex list-none flex-col gap-1.5 pl-0">{children}</ul>,
          li: ({ children }) => (
            <li className="rounded-md border border-border bg-background px-3 py-2">{children}</li>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-[var(--accent)] bg-muted/50 py-1.5 pl-3.5 pr-3 text-[13px] text-muted-foreground [&_p]:my-1">
              {children}
            </blockquote>
          ),
          em: ({ children }) => <em className="not-italic text-xs text-muted-foreground">{children}</em>,
          code: ({ children }) => (
            <code className="rounded bg-muted px-1 py-0.5 text-[12px]">{children}</code>
          ),
          a: ({ href, children }) => {
            const m = href ? MEMORY_URI.exec(href) : null;
            if (m) {
              const kind = m[1] as CitationKind;
              const id = m[2];
              return (
                <button
                  onClick={() => onCitation(kind, id)}
                  title={href}
                  className={cn(
                    "inline-flex max-w-full items-center gap-1 truncate rounded-md border border-transparent px-1.5 py-0.5 align-baseline text-xs font-medium transition-colors",
                    kind === "node"
                      ? "bg-[var(--info-bg)] text-[var(--info)] hover:border-[var(--info)]/40"
                      : "bg-[var(--success-bg)] text-[var(--success)] hover:border-[var(--success)]/40",
                  )}
                >
                  {children}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="link-accent">
                {children}
              </a>
            );
          },
        }}
      >
        {markdown}
      </Markdown>
    </div>
  );
}
