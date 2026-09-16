"""
STEP 1 — CV Parser
Extracts raw text from PDF using pdfplumber (primary) → pymupdf (fallback).
Returns a typed ParseResult regardless of success/failure.

Page policy (Config.PDF_MAX_PAGES): 0 = all pages (default, recommended),
N = first N pages only. ParseResult reports both total pages and pages parsed.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import Config


@dataclass
class ParseResult:
    filename: str
    filepath: str
    raw_text: str
    page_count: int          # total pages in the PDF
    extraction_method: str   # 'pdfplumber' | 'pymupdf' | 'none'
    char_count: int
    success: bool
    error: Optional[str] = None
    pages_parsed: int = 0    # pages actually read (<= page_count when limited)


def _limit_note() -> str:
    limit = Config.PDF_MAX_PAGES
    return f" (limited to first {limit} page(s))" if limit > 0 else ""


def _apply_limit(items: list, total: int) -> tuple[list, int]:
    """Slice a per-page list to the configured page limit."""
    limit = Config.PDF_MAX_PAGES
    if limit > 0:
        items = items[:limit]
    return items, min(len(items), total) if limit <= 0 else min(limit, total)


def parse_pdf(filepath: Path) -> ParseResult:
    """
    Try pdfplumber → fallback to pymupdf.
    Returns ParseResult with success=False if both fail or text is empty.
    """

    filename = filepath.name

    # ── Primary: pdfplumber ────────────────────────────────────
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            total = len(pdf.pages)
            pages, parsed = _apply_limit(list(pdf.pages), total)
            pages_text = []
            for page in pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)

            full_text = "\n\n".join(pages_text).strip()

            if len(full_text) >= 50:
                return ParseResult(
                    filename=filename,
                    filepath=str(filepath),
                    raw_text=full_text,
                    page_count=total,
                    extraction_method="pdfplumber",
                    char_count=len(full_text),
                    success=True,
                    error=None,
                    pages_parsed=parsed,
                )
    except Exception:
        pass  # fall through to pymupdf; every return path below sets error explicitly

    # ── Fallback: pymupdf ──────────────────────────────────────
    try:
        import pymupdf  # aka fitz
        doc = pymupdf.open(str(filepath))
        total = doc.page_count
        limit = Config.PDF_MAX_PAGES
        pages_text = []
        parsed = 0
        for i, page in enumerate(doc):
            if limit > 0 and i >= limit:
                break
            text = page.get_text()
            if text:
                pages_text.append(text)
            parsed += 1
        page_count = total
        doc.close()

        full_text = "\n\n".join(pages_text).strip()

        if len(full_text) >= 50:
            return ParseResult(
                filename=filename,
                filepath=str(filepath),
                raw_text=full_text,
                page_count=page_count,
                extraction_method="pymupdf",
                char_count=len(full_text),
                success=True,
                error=None,
                pages_parsed=parsed,
            )
        else:
            return ParseResult(
                filename=filename,
                filepath=str(filepath),
                raw_text=full_text,
                page_count=page_count,
                extraction_method="pymupdf",
                char_count=len(full_text),
                success=False,
                error="Likely scanned PDF — no extractable text layer" + _limit_note(),
                pages_parsed=parsed,
            )

    except Exception as e:
        return ParseResult(
            filename=filename,
            filepath=str(filepath),
            raw_text="",
            page_count=0,
            char_count=0,
            extraction_method="none",
            success=False,
            error=f"Both parsers failed. Last error: {e}",
            pages_parsed=0,
        )
