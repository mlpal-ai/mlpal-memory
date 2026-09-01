"""PDF collector — the first non-text lane of the data plane.

Extracts text per page (pypdf, optional extra ``pdf``), emits one document per
PDF with page markers preserved (``[page N]``) so citations can carry page
locality. Scanned/image-only PDFs yield no text and are reported as skipped —
OCR is a separate, costed lane (design note in v5 program), not silently faked.
valid_at = file mtime, consistent with the markdown collector.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

MAX_CHARS = 200_000
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "build"}


def extract_text(path: Path, max_chars: int = MAX_CHARS) -> str:
    from pypdf import PdfReader  # optional extra: mlpal-memory[pdf]

    reader = PdfReader(str(path))
    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        parts.append(f"[page {i}]\n{text}")
        total += len(text)
        if total >= max_chars:
            parts.append("[... truncated ...]")
            break
    return "\n\n".join(parts)


def iter_pdfs(root: Path) -> Iterator[dict]:
    """Yield ingestable PDF documents under ``root`` (skips extraction failures
    and text-free scans, reporting them via the 'skipped' marker)."""
    for path in sorted(root.rglob("*.pdf")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = extract_text(path)
        except Exception as exc:  # noqa: BLE001 — a corrupt PDF must not kill the sweep
            yield {"path": path, "skipped": f"extract failed: {exc}"}
            continue
        if len(text) < 200:
            yield {"path": path, "skipped": "no extractable text (scanned/image PDF?)"}
            continue
        yield {
            "path": path,
            "content": text,
            "title": path.stem,
            "valid_at": datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        }
