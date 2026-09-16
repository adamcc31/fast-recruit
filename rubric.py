"""
Rubric Builder — Anchored scoring rubric per position (built ONCE, reused)
============================================================================
Technique distilled from structured candidate-assessment methodology
(anchored scales: "a 4 looks like..." beats bare numeric scales for consistency):

  1. One LLM call per position from the job ad → observable indicators +
     anchored score-band descriptions per dimension, specific to THIS role.
  2. Cached to output/rubrics/ (keyed by job config hash) — batch of 1,400
     CVs costs exactly ONE extra call.
  3. Injected into every scoring prompt. If generation fails, scoring falls
     back to the generic scale — the batch is never blocked.

All rubric text is in Bahasa Indonesia (it is model-facing output content).
"""

import hashlib
import json
from pathlib import Path

from config import Config
from scorer import _chat_complete, _extract_json


RUBRIC_DIR = Path("output/rubrics")

RUBRIC_INSTRUCTIONS = """You are a senior HR recruiter and competency-framework specialist with 10+ years of experience in Southeast Asia. Your task: turn the job ad below into a FAIR, OBSERVABLE scoring rubric.

Rules:
- For EACH dimension, list 3–5 observable indicators: concrete evidence a rater can FIND in a CV (job titles, years, tools, degrees, quantified achievements). Never traits ("good attitude").
- For EACH dimension, write anchored descriptions for all 5 bands (9–10, 7–8, 5–6, 3–4, 1–2) SPECIFIC to this role and industry. "A 7 looks like..." with role examples.
- Anchors must be realistic for the local talent market (Indonesia): do not demand unicorns at 7–8.
- Fairness: indicators and anchors must rest ONLY on job-relevant evidence. Never use gender, age, religion, ethnicity, marital status, photo/appearance as signals.
- Write ALL values in professional Bahasa Indonesia. JSON keys stay in English.

Return ONLY a valid JSON object. No markdown fences, no preamble."""


def _rubric_user_prompt(job_config: dict) -> str:
    dims = "\n".join(f"- {d}" for d in job_config["dimensions"])
    extras = []
    if job_config.get("location_filter"):
        extras.append(f"Domicile must be one of: {job_config['location_filter']}.")
    if int(job_config.get("min_years") or 0) > 0:
        extras.append(f"Minimum experience: {job_config['min_years']} years.")
    extra_block = ("\nHard requirements:\n" + "\n".join(extras) + "\n") if extras else ""

    return f"""## Position

**{job_config['title']}**

{job_config['description']}
{extra_block}
## Scoring dimensions

{dims}

## Required JSON structure (no markdown fences)

{{
  "dimensions": {{
    "<dimension_name>": {{
      "indicators": ["indikator observable 1 (ID)", "indikator 2", "indikator 3"],
      "anchors": {{
        "9-10": "deskripsi band 9-10 spesifik peran ini (ID)",
        "7-8": "deskripsi band 7-8 (ID)",
        "5-6": "deskripsi band 5-6 (ID)",
        "3-4": "deskripsi band 3-4 (ID)",
        "1-2": "deskripsi band 1-2 (ID)"
      }}
    }}
  }}
}}"""


def _config_hash(job_config: dict) -> str:
    blob = json.dumps({
        "title": job_config["title"],
        "description": job_config["description"],
        "dimensions": job_config["dimensions"],
        "location": job_config.get("location_filter", ""),
        "min_years": job_config.get("min_years", 0),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:10]


def _rubric_path(job_config: dict) -> Path:
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in job_config["title"]).strip("_")
    return RUBRIC_DIR / f"rubric_{safe or 'Job'}_{_config_hash(job_config)}.json"


def _fallback_rubric(job_config: dict) -> dict:
    """Empty rubric → scorer uses the generic scale. Never blocks the batch."""
    return {"dimensions": {}, "fallback": True}


def ensure_rubric(job_config: dict, force: bool = False) -> dict:
    """
    Return the anchored rubric for this position (cached; 1 LLM call max).
    Prints status; on any failure returns a fallback (generic scale).
    """
    RUBRIC_DIR.mkdir(parents=True, exist_ok=True)
    path = _rubric_path(job_config)

    if path.exists() and not force:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("dimensions"), dict) and data["dimensions"]:
                print(f"         Rubrik   : dari cache ({path.name})")
                return data
        except (json.JSONDecodeError, KeyError):
            pass

    print("         Rubrik   : membuat anchor spesifik posisi (1x panggilan LLM)...", end="", flush=True)
    try:
        raw, _usage = _chat_complete(
            RUBRIC_INSTRUCTIONS, _rubric_user_prompt(job_config),
            max_tokens=Config.RUBRIC_MAX_TOKENS,
        )
        data = _extract_json(raw)
        if not isinstance(data.get("dimensions"), dict) or not data["dimensions"]:
            raise ValueError("rubric JSON missing 'dimensions'")
        data["fallback"] = False
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f" OK → {path.name}")
        return data
    except Exception as e:
        print(f" GAGAL ({e}) — lanjut dengan skala generik")
        return _fallback_rubric(job_config)
