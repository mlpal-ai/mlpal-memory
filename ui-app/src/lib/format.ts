/** Shared formatting utilities — one time/duration/score treatment everywhere. */

export function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

export function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "undated";
}

/** Retrieval scores are small RRF-style fractions — 3 significant digits reads
 * better than a wall of zeros. */
export function fmtScore(score: number): string {
  if (!score) return "0";
  return score.toPrecision(3);
}
