"""
STEPS 4 & 5 — LLM Scorer (OpenAI-compatible, stdlib only)
=========================================================
Sends validated CV markdown to any OpenAI-compatible chat endpoint:
POST {LLM_BASE_URL}/chat/completions

Works with Meta Muse Spark, OpenAI (incl. Codex models), OpenRouter,
Ollama, vLLM, etc. No vendor SDK required — pure stdlib urllib.

Returns a typed ScoreResult with per-dimension scores + reasoning.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import Config
from validator import extract_contacts


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    filename: str
    candidate_name: str
    overall_score: float           # 0.0–10.0
    recommendation: str            # SHORTLIST | REVIEW | REJECT | ERROR
    summary: str
    dimensions: dict               # {dim: {score: int, reasoning: str}}
    strengths: list[str]
    red_flags: list[str]
    years_of_experience: int
    key_skills: list[str]
    raw_response: str
    error: Optional[str] = None
    location: str = ""             # domicile city as stated in CV
    meets_location: bool = True    # False → knockout REJECT (when filter set)
    meets_experience: bool = True  # False → knockout REJECT (when min set)
    is_cv: bool = True             # False → document is not a CV (auto-REJECT)
    fairness_ok: bool = True       # False → model flagged unfair-to-judge dimension
    fairness_note: str = ""        # short reason when fairness_ok is False
    contact_email: str = ""        # deterministic regex extraction (no LLM)
    contact_phone: str = ""        # deterministic regex extraction (no LLM)
    prompt_tokens: int = 0         # LLM usage tracking per candidate
    completion_tokens: int = 0
    reasoning_tokens: int = 0      # subset of completion (thinking models)
    total_tokens: int = 0


# ── System Prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior HR recruiter and talent acquisition specialist with 10+ years of experience across technology companies in Southeast Asia. You evaluate candidates fairly, accurately, and without bias.

Your responsibilities:
- Evaluate CVs strictly against provided job requirements
- Score based ONLY on evidence present in the CV — never assume or inflate
- Be consistent: same quality CV → same score range across all candidates
- A 7/10 means genuinely strong, a 5/10 means average — do not default to high scores
- Reference specific details (company names, years, technologies) in your reasoning
- Flag genuine concerns: employment gaps >1 year, excessive job-hopping (<1 year tenures), mismatched skills

Fairness (non-negotiable): base scores ONLY on job-relevant evidence from the CV. Ignore and NEVER mention, positively or negatively: gender, age, religion, ethnicity, marital status, pregnancy/family plans, disability, photo/appearance, name origin. If the CV lacks sufficient job-relevant evidence to judge a dimension fairly, say so in that dimension's reasoning and set "fairness_ok" to false with a short "fairness_note".

Output format: Return ONLY a valid JSON object. No markdown fences, no preamble, no explanation outside the JSON.

Language: ALL human-readable values (summary, every dimension reasoning, strengths, red_flags) MUST be written in professional Bahasa Indonesia. JSON keys stay in English. Proper nouns (names, companies, tech terms) stay as-is."""


# ── Prompt Builder ─────────────────────────────────────────────────────────────

def _build_dimensions_schema(dimensions: list[str]) -> str:
    """Generate the JSON schema example for scoring dimensions."""
    lines = []
    for dim in dimensions:
        lines.append(f'    "{dim}": {{"score": 7, "reasoning": "bukti spesifik dari CV dalam Bahasa Indonesia"}}')
    return ",\n".join(lines)


def _build_scale_section(job_config: dict) -> str:
    """Anchored per-dimension rubric when available, else the generic scale."""
    rubric = job_config.get("rubric") or {}
    dims = (rubric.get("dimensions") or {})
    if dims:
        parts = ["Gunakan anchor spesifik posisi ini untuk tiap dimensi (kutip bukti observable dari CV):", ""]
        for dim in job_config["dimensions"]:
            entry = dims.get(dim, {}) or {}
            indicators = entry.get("indicators", []) or []
            anchors = entry.get("anchors", {}) or {}
            parts.append(f"### {dim}")
            if indicators:
                parts.append("Cari bukti: " + "; ".join(str(x) for x in indicators))
            for band in ("9-10", "7-8", "5-6", "3-4", "1-2"):
                if anchors.get(band):
                    parts.append(f"- {band}: {anchors[band]}")
            parts.append("")
        parts.append("Patokan umum bila anchor tidak mencakup kasus ini:")
        parts.append("- 9–10 luar biasa | 7–8 kuat | 5–6 sebagian | 3–4 lemah | 1–2 buruk.")
        return "\n".join(parts)

    return """Scoring scale:
- 9–10 → Exceptional — exceeds all requirements
- 7–8  → Strong — meets most requirements clearly
- 5–6  → Partial — meets some but missing key areas
- 3–4  → Weak — significant gaps in core requirements
- 1–2  → Poor — fundamentally underqualified"""


def build_user_prompt(cv_markdown: str, job_config: dict) -> str:
    dim_schema = _build_dimensions_schema(job_config["dimensions"])

    # Truncate long CVs: keep head (experience usually first) + tail (education/contact at end)
    head, tail = Config.CV_HEAD_CHARS, Config.CV_TAIL_CHARS
    cv_content = cv_markdown
    if len(cv_markdown) > head + tail + 100:
        cv_content = cv_markdown[:head] + "\n\n[... content truncated ...]\n\n" + cv_markdown[-tail:]

    # Knockout block: only the criteria the user actually set
    knockout_lines = []
    location_filter = (job_config.get("location_filter") or "").strip()
    min_years = int(job_config.get("min_years") or 0)
    if location_filter:
        knockout_lines.append(
            f"- DOMICILE must be one of: {location_filter}. "
            "Read the candidate's CURRENT address/domicile (not birthplace, not company HQ). "
            "If domicile city is unclear or outside the list → meets_location=false."
        )
    if min_years > 0:
        knockout_lines.append(
            f"- EXPERIENCE must be >= {min_years} years total relevant work "
            f"(internships excluded unless stated otherwise). If below {min_years} → meets_min_experience=false."
        )
    knockout_section = (
        "## Hard Requirements (knockout)\n\n" + "\n".join(knockout_lines) + "\n\n---\n\n"
        if knockout_lines else ""
    )
    scale_section = _build_scale_section(job_config)

    return f"""## Job Requirements

**Position**: {job_config['title']}

{job_config['description']}

---

{knockout_section}## Candidate CV

{cv_content}

---

## Evaluation Task

Score this candidate and return this EXACT JSON structure (no markdown fences).
First check document type: if this is clearly NOT a CV/resume, set "is_cv" to false
(the pipeline will reject it) but still fill the other fields with your best reading.

{{
  "candidate_name": "Nama lengkap dari CV, atau 'Unknown' bila tidak ditemukan",
  "overall_score": 7.5,
  "recommendation": "SHORTLIST",
  "summary": "Penilaian keseluruhan 2-3 kalimat dalam Bahasa Indonesia, sebut detail spesifik CV",
  "dimensions": {{
{dim_schema}
  }},
  "strengths": ["Kelebihan spesifik beserta bukti dalam Bahasa Indonesia", "Kelebihan lain"],
  "red_flags": ["Kekhawatiran spesifik bila ada dalam Bahasa Indonesia", "Atau list kosong []"],
  "years_of_experience": "total tahun kerja relevan sebagai integer (konsisten dengan summary; 0 hanya bila CV benar-benar tanpa pengalaman kerja)",
  "key_skills": ["Python", "PostgreSQL"],
  "location": "Kota domisili saat ini sesuai tulisan di CV, atau 'Unknown'",
  "meets_location": true,
  "meets_min_experience": true,
  "is_cv": "true, atau false bila dokumen ini jelas BUKAN CV/resume (invoice, skripsi, brosur, iklan lowongan, ...)",
  "fairness_ok": true,
  "fairness_note": "kosongkan bila penilaian adil; isi alasan singkat dalam Bahasa Indonesia bila ada dimensi yang tidak bisa dinilai adil"
}}

{scale_section}

Recommendation rules:
- overall_score >= {Config.SHORTLIST_MIN_SCORE} → "SHORTLIST"
- overall_score >= {Config.REVIEW_MIN_SCORE}    → "REVIEW"
- otherwise                                     → "REJECT"
"""


# ── JSON Extractor ─────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """
    Robustly extract JSON from LLM response.
    Handles: raw JSON, ```json fences, embedded JSON in prose.
    """

    # Strip markdown fences if present
    cleaned = raw.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find the outermost { } block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from response:\n{raw[:500]}")


def _safe_float(value, default: float = 0.0) -> float:
    """LLMs sometimes return '7.5/10' or 'approx 7' — extract first number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+(\.\d+)?", str(value))
        return float(match.group()) if match else default


def _safe_int(value, default: int = 0) -> int:
    """LLMs sometimes return '4+', '3-5 years' — extract first integer."""
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value))
        return int(match.group()) if match else default


def _safe_str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# ── OpenAI-compatible HTTP client (stdlib, retry + backoff) ────────────────────

class _FatalLLMError(RuntimeError):
    """Config/payload problem — retrying is pointless, fail fast."""


def _build_payload(system: str, user: str, include_reasoning: bool = True, max_tokens: int | None = None) -> bytes:
    """Build the chat-completions request body."""
    body: dict = {
        "model": Config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens or Config.MAX_TOKENS,
        "temperature": Config.LLM_TEMPERATURE,
    }
    if include_reasoning and Config.LLM_REASONING_EFFORT:
        body["reasoning_effort"] = Config.LLM_REASONING_EFFORT
    return json.dumps(body).encode("utf-8")


def _extract_usage(resp_body: dict) -> dict:
    """Pull token counts from a chat-completions response (missing → 0)."""
    usage = resp_body.get("usage", {}) or {}
    details = usage.get("completion_tokens_details", {}) or {}
    return {
        "prompt": int(usage.get("prompt_tokens", 0) or 0),
        "completion": int(usage.get("completion_tokens", 0) or 0),
        "reasoning": int(details.get("reasoning_tokens", 0) or 0),
        "total": int(usage.get("total_tokens", 0) or 0),
    }


def _chat_complete(system: str, user: str, max_tokens: int | None = None) -> tuple[str, dict]:
    """
    POST {LLM_BASE_URL}/chat/completions and return (assistant text, usage dict).
    Retries 429/5xx/timeouts with exponential backoff; fails fast on 401/403/404
    with a human-readable message so a bad key/URL/model is obvious immediately.
    """
    url = f"{Config.LLM_BASE_URL}/chat/completions"
    budget = max_tokens or Config.MAX_TOKENS
    payload = _build_payload(system, user, max_tokens=budget)
    reasoning_stripped = False

    def _do_request(body: bytes) -> tuple[str, dict]:
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {Config.LLM_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=Config.LLM_TIMEOUT_SECONDS) as resp:
            resp_body = json.loads(resp.read().decode("utf-8"))
        usage = _extract_usage(resp_body)
        try:
            choice = resp_body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise _FatalLLMError(f"Unexpected chat-completions shape: {str(resp_body)[:300]}")
        if not content:
            finish = choice.get("finish_reason", "?")
            raise _FatalLLMError(
                f"LLM returned empty content (finish_reason={finish}). "
                f"The model spent the token budget on reasoning — "
                f"raise the token budget (now {budget})."
            )
        return content, usage

    attempts = max(1, Config.LLM_MAX_RETRIES)
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _do_request(payload)
        except _FatalLLMError:
            raise  # config/payload issue — no point retrying
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            if e.code == 400 and not reasoning_stripped and "reasoning_effort" in detail.lower():
                payload = _build_payload(system, user, include_reasoning=False, max_tokens=budget)
                reasoning_stripped = True
                print(" [provider rejects reasoning_effort — retrying without it]", end="", flush=True)
                continue
            if e.code in (401, 403):
                raise RuntimeError(
                    f"LLM auth failed ({e.code}). Check LLM_API_KEY for {Config.LLM_BASE_URL}. "
                    f"Provider says: {detail}"
                )
            if e.code == 404:
                raise RuntimeError(
                    f"LLM endpoint/model not found (404) at {url} "
                    f"with model '{Config.LLM_MODEL}'. Check LLM_BASE_URL and LLM_MODEL. "
                    f"Provider says: {detail}"
                )
            last_err = RuntimeError(f"LLM HTTP {e.code}: {detail}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, RuntimeError) as e:
            last_err = e

        if attempt < attempts:
            wait = 2 ** attempt  # 2s, 4s, 8s, ...
            print(f" [retry {attempt}/{attempts} in {wait}s: {last_err}]", end="", flush=True)
            time.sleep(wait)

    raise RuntimeError(f"LLM request failed after {attempts} attempts: {last_err}")


# ── Main Scorer ────────────────────────────────────────────────────────────────

def score_cv(
    filename: str,
    md_path: Path,
    job_config: dict,
) -> ScoreResult:
    """
    Send CV markdown to the configured LLM and return a structured ScoreResult.
    Handles API errors and JSON parse failures gracefully (never raises).
    """

    cv_markdown = md_path.read_text(encoding="utf-8")
    prompt = build_user_prompt(cv_markdown, job_config)
    contact_email, contact_phone = extract_contacts(cv_markdown)

    raw_response = ""
    usage = {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0}

    try:
        raw_response, usage = _chat_complete(SYSTEM_PROMPT, prompt)
        data = _extract_json(raw_response)

        # Document check: wrong upload that slipped past the validator
        # (long thesis, brochure...) → auto-REJECT, no further review
        if data.get("is_cv", True) is False:
            return ScoreResult(
                filename=filename,
                candidate_name=str(data.get("candidate_name", "Unknown") or "Unknown"),
                overall_score=0.0,
                recommendation="REJECT",
                summary="[NON-CV] " + (str(data.get("summary", "") or "Dokumen ini bukan CV/resume.")),
                dimensions={},
                strengths=[],
                red_flags=["Dokumen yang diupload bukan CV/resume"],
                years_of_experience=0,
                key_skills=[],
                raw_response=raw_response,
                is_cv=False,
                contact_email=contact_email,
                contact_phone=contact_phone,
                prompt_tokens=usage["prompt"],
                completion_tokens=usage["completion"],
                reasoning_tokens=usage["reasoning"],
                total_tokens=usage["total"],
            )

        # Enforce recommendation based on thresholds (override LLM if needed)
        score = _safe_float(data.get("overall_score", 0))
        score = max(0.0, min(10.0, score))
        if score >= Config.SHORTLIST_MIN_SCORE:
            recommendation = "SHORTLIST"
        elif score >= Config.REVIEW_MIN_SCORE:
            recommendation = "REVIEW"
        else:
            recommendation = "REJECT"

        location = str(data.get("location", "") or "")
        meets_location = bool(data.get("meets_location", True))
        years_exp = _safe_int(data.get("years_of_experience", 0))
        meets_experience = bool(data.get("meets_min_experience", True))
        summary = str(data.get("summary", "") or "")
        red_flags = _safe_str_list(data.get("red_flags", []))
        fairness_ok = bool(data.get("fairness_ok", True))
        fairness_note = str(data.get("fairness_note", "") or "")
        if not fairness_ok:
            note = fairness_note or "model menandai ada dimensi yang tidak bisa dinilai adil"
            red_flags = [f"Catatan fairness: {note}"] + red_flags

        # Knockout: hard filter set by user in .env — fail = REJECT regardless of score
        knockout_reasons = []
        if Config.JOB_LOCATION and not meets_location:
            knockout_reasons.append(
                f"domisili '{location or 'Unknown'}' tidak termasuk [{Config.JOB_LOCATION}]"
            )
        if Config.MIN_YEARS_EXPERIENCE > 0 and (not meets_experience or years_exp < Config.MIN_YEARS_EXPERIENCE):
            knockout_reasons.append(
                f"pengalaman {years_exp} th di bawah minimum {Config.MIN_YEARS_EXPERIENCE} th"
            )
        if knockout_reasons:
            recommendation = "REJECT"
            reason_text = "; ".join(knockout_reasons)
            summary = f"[KNOCKOUT] {reason_text}. {summary}".strip()
            red_flags = [f"Gugur knockout: {reason_text}"] + red_flags

        return ScoreResult(
            filename=filename,
            candidate_name=str(data.get("candidate_name", "Unknown") or "Unknown"),
            overall_score=score,
            recommendation=recommendation,
            summary=summary,
            dimensions=data.get("dimensions", {}) if isinstance(data.get("dimensions"), dict) else {},
            strengths=_safe_str_list(data.get("strengths", [])),
            red_flags=red_flags,
            years_of_experience=years_exp,
            key_skills=_safe_str_list(data.get("key_skills", [])),
            raw_response=raw_response,
            location=location,
            meets_location=meets_location,
            meets_experience=meets_experience,
            is_cv=True,
            fairness_ok=fairness_ok,
            fairness_note=fairness_note,
            contact_email=contact_email,
            contact_phone=contact_phone,
            prompt_tokens=usage["prompt"],
            completion_tokens=usage["completion"],
            reasoning_tokens=usage["reasoning"],
            total_tokens=usage["total"],
        )

    except ValueError as e:
        return ScoreResult(
            filename=filename,
            candidate_name="JSON Parse Error",
            overall_score=0.0,
            recommendation="ERROR",
            summary=str(e),
            dimensions={},
            strengths=[],
            red_flags=["Respons LLM tidak bisa diparse sebagai JSON"],
            years_of_experience=0,
            key_skills=[],
            raw_response=raw_response,
            error=str(e),
            contact_email=contact_email,
            contact_phone=contact_phone,
        )

    except Exception as e:
        return ScoreResult(
            filename=filename,
            candidate_name="API Error",
            overall_score=0.0,
            recommendation="ERROR",
            summary=f"Panggilan LLM gagal: {e}",
            dimensions={},
            strengths=[],
            red_flags=["Panggilan LLM gagal — cek LLM_BASE_URL, LLM_API_KEY, LLM_MODEL"],
            years_of_experience=0,
            key_skills=[],
            raw_response="",
            error=str(e),
            contact_email=contact_email,
            contact_phone=contact_phone,
        )
