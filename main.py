#!/usr/bin/env python3
"""
CV Screening Pipeline — Main Orchestrator
=========================================
Usage:
    python main.py                    # full run (needs LLM_API_KEY)
    python main.py --dry-run          # parse + validate only, no LLM calls (free)
    python main.py --limit 5          # process first 5 PDFs only (smoke test)
    python main.py --force            # re-score even if cached JSON exists

Steps:
  1. Parse PDFs (pdfplumber → pymupdf fallback)
  2. Write .md file per candidate
  3. Validation Gate (3 gates)
  4. LLM Scoring (any OpenAI-compatible endpoint) — cached per candidate
  5. JSON output per candidate (output/scores/)
  6. Export ranked Excel
"""

import argparse
import dataclasses
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from config import Config
from exporter import export_to_excel
from markdown_writer import create_markdown
from parser import parse_pdf
from scorer import ScoreResult, score_cv
from validator import validate_cv


def print_header():
    print("=" * 60)
    print("  CV Screening Pipeline")
    print(f"  Posisi: {Config.JOB_TITLE}")
    print(f"  LLM: {Config.LLM_MODEL} @ {Config.LLM_BASE_URL}")
    print(f"  Mulai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")


def _score_cache_path(pdf_path: Path) -> Path:
    return Config.SCORES_DIR / f"{pdf_path.stem}.json"


def _load_cached_score(pdf_path: Path) -> ScoreResult | None:
    cache = _score_cache_path(pdf_path)
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        # Tolerant: ignore keys from older versions so old caches still load
        valid = {k: v for k, v in data.items() if k in ScoreResult.__dataclass_fields__}
        score = ScoreResult(**valid)
        # ERROR results are never reused — always retry them fresh
        if score.recommendation == "ERROR":
            return None
        return score
    except (json.JSONDecodeError, TypeError):
        return None


def _save_score(pdf_path: Path, score: ScoreResult) -> None:
    Config.SCORES_DIR.mkdir(parents=True, exist_ok=True)
    _score_cache_path(pdf_path).write_text(
        json.dumps(dataclasses.asdict(score), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_pipeline(dry_run: bool = False, limit: int = 0, force: bool = False):
    print_header()

    # ── Preflight checks ───────────────────────────────────────
    if not dry_run and not Config.LLM_API_KEY:
        print("LLM_API_KEY belum diisi. Pilihan:")
        print("  1. Copy .env.example menjadi .env lalu isi LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
        print("  2. Export langsung:  export LLM_API_KEY=...")
        print("  3. Atau jalan tanpa LLM:  python main.py --dry-run\n")
        sys.exit(1)

    if not Config.INPUT_DIR.exists():
        Config.INPUT_DIR.mkdir(parents=True)
        print(f"Folder input dibuat: {Config.INPUT_DIR}")
        print("Taruh PDF CV JobStreet di sana lalu jalankan ulang.\n")
        sys.exit(0)

    pdf_files = sorted(Config.INPUT_DIR.glob("*.pdf"))
    if limit > 0:
        pdf_files = pdf_files[:limit]
    if not pdf_files:
        print(f"Tidak ada file PDF di {Config.INPUT_DIR}\n")
        sys.exit(0)

    print(f"Ditemukan {len(pdf_files)} file PDF" + ("  [DRY-RUN: tanpa panggilan LLM]" if dry_run else "") + "\n")

    # ── Setup dirs ─────────────────────────────────────────────
    Config.MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    Config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    Config.SCORES_DIR.mkdir(parents=True, exist_ok=True)

    job_config = {
        "title":           Config.JOB_TITLE,
        "description":     Config.JOB_DESCRIPTION,
        "dimensions":      Config.SCORING_DIMENSIONS,
        "location_filter": Config.JOB_LOCATION,
        "min_years":       Config.MIN_YEARS_EXPERIENCE,
    }

    # ── Anchored rubric (1 LLM call per position, cached; fallback = generic)
    if not dry_run:
        from rubric import ensure_rubric
        job_config["rubric"] = ensure_rubric(job_config, force=force)
    else:
        job_config["rubric"] = {"dimensions": {}, "fallback": True}

    score_results      = []
    failed_validations = []
    seen_hashes: dict[str, str] = {}  # sha256(text) -> first filename
    duplicates   = 0
    cache_hits   = 0

    # ── Main loop ──────────────────────────────────────────────
    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"[{i:>4}/{len(pdf_files)}] {pdf_path.name}")
        try:
            # STEP 1 — Parse
            parse_result = parse_pdf(pdf_path)
            method_tag = f"{parse_result.extraction_method} | {parse_result.char_count:,} chars"
            print(f"         Parse    : {method_tag}")

            # STEP 2 — Markdown
            md_path = create_markdown(parse_result, Config.MARKDOWN_DIR)
            print(f"         Markdown : {md_path.name}")

            # STEP 3 — Validate
            validation = validate_cv(parse_result)
            print(f"         Validate : [{validation.status}] {validation.reason[:70]}")

            if validation.status != "PASS":
                failed_validations.append(validation)
                print("         DILEWATI\n")
                continue

            # Dedup: identical CV text (candidate applied twice) → score once
            text_hash = hashlib.sha256(parse_result.raw_text.encode("utf-8")).hexdigest()
            if text_hash in seen_hashes:
                duplicates += 1
                print(f"         DUPLIKAT dari {seen_hashes[text_hash]} — dilewati (tanpa biaya LLM)\n")
                continue
            seen_hashes[text_hash] = pdf_path.name

            if dry_run:
                print("         (dry-run: scoring dilewati)\n")
                continue

            # STEP 4 & 5 — LLM Score (with resume cache)
            cached = None if force else _load_cached_score(pdf_path)
            if cached is not None:
                score = cached
                cache_hits += 1
                print(f"         Cache    : {score.overall_score:.1f}/10 [{score.recommendation}] — {score.candidate_name}")
            else:
                print("         Scoring  : memanggil LLM...", end="", flush=True)
                score = score_cv(parse_result.filename, md_path, job_config)
                _save_score(pdf_path, score)
                tok = f" | {score.total_tokens:,} tok (in {score.prompt_tokens:,}/out {score.completion_tokens:,}/reason {score.reasoning_tokens:,})" if score.total_tokens else ""
                print(f" {score.overall_score:.1f}/10 [{score.recommendation}] — {score.candidate_name}{tok}")
            score_results.append(score)
            print()

            time.sleep(Config.API_DELAY_SECONDS)
        except Exception as e:
            # One bad file must never kill a 1,400-CV batch — record and move on
            print(f"\n         ERROR TAK TERDUGA: {e}")
            err = ScoreResult(
                filename=pdf_path.name,
                candidate_name="Pipeline Error",
                overall_score=0.0,
                recommendation="ERROR",
                summary=f"Kegagalan pipeline tak terduga: {e}",
                dimensions={},
                strengths=[],
                red_flags=["Pipeline crash pada file ini — lihat summary"],
                years_of_experience=0,
                key_skills=[],
                raw_response="",
                error=str(e),
            )
            score_results.append(err)
            _save_score(pdf_path, err)
            print("         dicatat sebagai ERROR, lanjut\n")

    # ── STEP 6 — Export ───────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in Config.JOB_TITLE).strip("_")
    tag = "DRYRUN_" if dry_run else ""
    output_path = Config.RESULTS_DIR / f"cv_screening_{safe_title or 'Job'}_{tag}{timestamp}.xlsx"

    export_to_excel(score_results, failed_validations, output_path, Config.SCORING_DIMENSIONS)

    # ── Summary ────────────────────────────────────────────────
    shortlisted = sum(1 for r in score_results if r.recommendation == "SHORTLIST")
    reviewed    = sum(1 for r in score_results if r.recommendation == "REVIEW")
    rejected    = sum(1 for r in score_results if r.recommendation == "REJECT")
    errors      = sum(1 for r in score_results if r.recommendation == "ERROR")

    print("\n" + "=" * 60)
    print("  PIPELINE SELESAI")
    print("=" * 60)
    print(f"  Total CV        : {len(pdf_files)}")
    print(f"  Lolos Gate      : {len(score_results)}")
    print(f"  Gagal Gate      : {len(failed_validations)}")
    print(f"  Duplikat        : {duplicates}")
    print(f"  Dari Cache      : {cache_hits}")
    print("  ── Hasil ───────")
    print(f"  SHORTLIST       : {shortlisted}")
    print(f"  REVIEW          : {reviewed}")
    print(f"  REJECT          : {rejected}")
    print(f"  Error           : {errors}")
    print(f"\n  Output          : {output_path}")
    if dry_run:
        print("  CATATAN: dry-run — sheet Ranking sengaja KOSONG (tanpa panggilan LLM).")
        print("           Jalan tanpa --dry-run untuk scoring.\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CV Screening Pipeline")
    ap.add_argument("--dry-run", action="store_true", help="parse + validate only, no LLM calls")
    ap.add_argument("--limit", type=int, default=0, help="process only first N PDFs")
    ap.add_argument("--force", action="store_true", help="re-score even if cached JSON exists")
    args = ap.parse_args()
    run_pipeline(dry_run=args.dry_run, limit=args.limit, force=args.force)
