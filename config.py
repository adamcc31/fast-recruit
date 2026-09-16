"""
CV Screening Pipeline — Configuration
=====================================
Customize job requirements, scoring, and LLM connection here or via .env.

LLM is OpenAI-compatible: any endpoint serving POST {BASE_URL}/chat/completions
works — Meta Muse Spark, OpenAI (incl. Codex models), OpenRouter, Ollama, etc.

Copy .env.example to .env and fill in your values.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _default_input_dir() -> str:
    explicit = os.getenv("INPUT_DIR", "").strip()
    if explicit:
        return explicit
    if Path("cv").exists():
        return "cv"
    return "input/cvs"


class Config:
    # ─── Paths ────────────────────────────────────────────────
    INPUT_DIR    = Path(_default_input_dir())
    MARKDOWN_DIR = Path(os.getenv("MARKDOWN_DIR", "output/markdown"))
    RESULTS_DIR  = Path(os.getenv("RESULTS_DIR", "output/results"))
    SCORES_DIR   = Path(os.getenv("SCORES_DIR", "output/scores"))  # per-candidate JSON (resume cache)

    # ─── LLM Connection (OpenAI-compatible) ───────────────────
    # Examples:
    #   Meta Muse Spark : LLM_BASE_URL=https://api.meta.ai/v1  LLM_MODEL=muse-spark-1.3-contributor
    #   OpenAI / Codex  : LLM_BASE_URL=https://api.openai.com/v1  LLM_MODEL=gpt-5-codex (or your codex model)
    #   Ollama (local)  : LLM_BASE_URL=http://localhost:11434/v1  LLM_MODEL=qwen2.5:14b
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
    LLM_API_KEY  = os.getenv("LLM_API_KEY", "") or os.getenv("META_API_KEY", "")
    LLM_MODEL    = os.getenv("LLM_MODEL", "muse-spark-1.3-contributor")

    LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    LLM_MAX_RETRIES     = int(os.getenv("LLM_MAX_RETRIES", "4"))
    LLM_TEMPERATURE     = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    # Reasoning effort: low | medium | high (some providers also accept "none").
    # Sent as `reasoning_effort` in the chat-completions payload. Providers that
    # don't support it get an automatic retry WITHOUT the parameter. "" = off.
    LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "medium").strip().lower()
    MAX_TOKENS          = int(os.getenv("LLM_MAX_TOKENS", "6000"))  # anchored prompts are long; reasoning eats budget too
    RUBRIC_MAX_TOKENS   = int(os.getenv("RUBRIC_MAX_TOKENS", "8000"))  # rubric task output is long
    API_DELAY_SECONDS   = float(os.getenv("API_DELAY_SECONDS", "0.5"))  # pause between calls

    # ─── CV truncation (long CVs) ─────────────────────────────
    CV_HEAD_CHARS = int(os.getenv("CV_HEAD_CHARS", "6000"))
    CV_TAIL_CHARS = int(os.getenv("CV_TAIL_CHARS", "1000"))

    # ─── PDF page limit ─────────────────────────────────────────
    # 0 = parse SEMUA halaman (default, direkomendasikan).
    # N = hanya N halaman pertama. Cara paksa 1 halaman: PDF_MAX_PAGES=1.
    # Bukti (7 CV nyata, Sep 2026): halaman 1 hanya memuat 35–80% teks;
    # riwayat kerja sering lanjut di hlm 2–3 (satu CV: hlm2 > hlm1).
    # Parse lokal itu gratis; token LLM sudah dibatasi CV_HEAD+TAIL_CHARS.
    # Jadi batasi halaman hanya bila format Anda selalu 1 hlm + lampiran.
    PDF_MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "0"))

    # ─── Token cost (USD per 1 juta token; 0 = tampilkan token saja) ──
    # Contoh bila provider Anda $3/$15 per 1M: isi 3 dan 15.
    LLM_COST_INPUT_PER_1M  = float(os.getenv("LLM_COST_INPUT_PER_1M", "0"))
    LLM_COST_OUTPUT_PER_1M = float(os.getenv("LLM_COST_OUTPUT_PER_1M", "0"))

    # ─── Validation Thresholds ────────────────────────────────
    MIN_CHAR_COUNT      = int(os.getenv("MIN_CHAR_COUNT", "200"))    # below this → FAIL_EXTRACTION
    MIN_STRUCTURE_SCORE = float(os.getenv("MIN_STRUCTURE_SCORE", "0.4"))  # 0-1, below → FAIL_STRUCTURE

    # ─── Job Position — CUSTOMIZE THIS ────────────────────────
    JOB_TITLE = os.getenv("JOB_TITLE", "Software Engineer")

    JOB_DESCRIPTION = os.getenv("JOB_DESCRIPTION", """
    We are looking for a Software Engineer with:
    - 2+ years of hands-on experience in backend or fullstack development
    - Proficiency in Python, JavaScript, or Java
    - Experience with relational databases (PostgreSQL, MySQL) or NoSQL
    - Familiarity with REST API design and version control (Git)
    - Bachelor's degree in Computer Science or equivalent (preferred)
    - Strong problem-solving skills and ability to work in a team
    """).strip()

    # Long job ads are easier as a file: set JOB_DESCRIPTION_FILE=lowongan.txt
    # (file content wins over JOB_DESCRIPTION above when the file exists).
    _JD_FILE = os.getenv("JOB_DESCRIPTION_FILE", "").strip()
    if _JD_FILE and Path(_JD_FILE).exists():
        JOB_DESCRIPTION = Path(_JD_FILE).read_text(encoding="utf-8").strip()

    # ─── Knockout Criteria (hard filter — fail = REJECT) ──────
    # WHERE: comma-separated allowed domicile cities, e.g. "Bekasi, Jakarta".
    #   Empty = no location filter. Match is case-insensitive, substring-based
    #   ("Jakarta Selatan" matches "Jakarta"). Unclear domicile = FAIL.
    #   Set in .env:  JOB_LOCATION=Bekasi, Jakarta
    JOB_LOCATION = os.getenv("JOB_LOCATION", "").strip()

    # HOW LONG: minimum years of experience, e.g. "3". 0 = no filter.
    #   Set in .env:  MIN_YEARS_EXPERIENCE=3
    MIN_YEARS_EXPERIENCE = int(os.getenv("MIN_YEARS_EXPERIENCE", "0"))

    # ─── Scoring Dimensions — CUSTOMIZE THIS ──────────────────
    # Each dimension will be scored 0–10 by the LLM.
    SCORING_DIMENSIONS = [
        "experience_relevance",   # How well work history matches the role
        "technical_skills",       # Stack and tool fit
        "education",              # Degree, major, GPA signals
        "career_stability",       # Job-hopping, tenure patterns
        "communication_clarity",  # Resume structure, language quality
    ]

    # ─── Recommendation Thresholds ────────────────────────────
    SHORTLIST_MIN_SCORE = float(os.getenv("SHORTLIST_MIN_SCORE", "7.0"))  # >= SHORTLIST
    REVIEW_MIN_SCORE    = float(os.getenv("REVIEW_MIN_SCORE", "5.0"))     # >= REVIEW, else REJECT
