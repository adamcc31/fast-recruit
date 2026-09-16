"""
STEP 2 — Markdown Writer
Creates one .md file per candidate. Acts as a clean audit trail and
is the document fed to the LLM (not the raw PDF bytes).
"""

from datetime import datetime
from pathlib import Path

from parser import ParseResult


def create_markdown(parse_result: ParseResult, output_dir: Path) -> Path:
    """
    Write a structured markdown file from the ParseResult.
    Returns the path to the written file.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(parse_result.filename).stem
    md_path = output_dir / f"{stem}.md"

    status_line = "✅ SUCCESS" if parse_result.success else f"❌ FAILED — {parse_result.error}"

    content = f"""# CV: {parse_result.filename}

## Metadata
| Field | Value |
|---|---|
| Source File | `{parse_result.filename}` |
| Pages | {parse_result.page_count} (parsed {parse_result.pages_parsed}) |
| Extraction Method | {parse_result.extraction_method} |
| Characters Extracted | {parse_result.char_count:,} |
| Parse Status | {status_line} |
| Processed At | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |

---

## Extracted Content

{parse_result.raw_text if parse_result.raw_text else "_No text extracted_"}
"""

    md_path.write_text(content, encoding="utf-8")
    return md_path
