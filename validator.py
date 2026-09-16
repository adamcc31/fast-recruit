"""
STEP 3 — First Validation Gate
Prevents wasted LLM calls on:
  - Scanned PDFs (FAIL_EXTRACTION)
  - Unstructured/unidentifiable documents (FAIL_STRUCTURE)
  - Too-short or clearly non-CV content (FAIL_CONTENT)

Supports both English and Bahasa Indonesia CV keywords.
"""

import re
from dataclasses import dataclass

from parser import ParseResult


# ── Keyword lists (EN + ID) ────────────────────────────────────────────────────

EDUCATION_KEYWORDS = {
    # English
    "university", "college", "bachelor", "master", "diploma", "degree",
    "certification", "institute", "faculty", "graduated", "gpa", "major",
    "school", "high school",
    # Bahasa Indonesia
    "universitas", "sarjana", "magister", "diploma", "pendidikan",
    "fakultas", "jurusan", "ipk", "akademi", "sekolah tinggi",
    "sekolah", "sma", "smk", "smp", "stm",
}

EXPERIENCE_KEYWORDS = {
    # English
    "experience", "work", "position", "role", "company", "responsibilities",
    "achievements", "project", "intern", "manager", "engineer", "developer",
    "analyst", "team", "led", "built", "designed", "implemented",
    "staff", "driver", "operator",
    # Bahasa Indonesia
    "pengalaman", "perusahaan", "jabatan", "proyek",
    "magang", "tanggung jawab", "memimpin", "membangun", "staf",
    "karyawan", "supir",
}

EMAIL_RE   = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE   = re.compile(r"(\+62|08|\+1|\+44|\+60|\+65|\+61|\+81|\+63|\+66|\+84|\+91|\+86|\+49|\+33|\+31)[0-9\s\-\.]{7,14}")
YEAR_RE    = re.compile(r"\b(19|20)\d{2}\b")

# ── Non-CV document signals (EN + ID) ──────────────────────────────────────────
# Only consulted when the doc has NEITHER education NOR experience keywords —
# a real CV almost always has at least one. Matched category names the Fail Type.

NON_CV_SIGNALS = {
    "invoice/kwitansi": {
        "invoice", "faktur", "kwitansi", "receipt", "tagihan", "pembayaran",
        "lunas", "total bayar", "purchase order", "purchase-order", "po number",
        "npwp", "ppn", "jatuh tempo",
    },
    "karya ilmiah (skripsi/tesis)": {
        "skripsi", "tesis", "disertasi", "daftar pustaka", "bab i", "bab ii",
        "abstrak", "abstract", "metodologi penelitian", "rumusan masalah",
    },
    "brosur/promo": {
        "brosur", "katalog", "price list", "promo", "diskon", "hubungi kami",
        "call center",
    },
    "iklan lowongan": {
        "lowongan", "job vacancy", "we are hiring", "kualifikasi", "kirim lamaran",
        "batas lamaran",
    },
}


def _detect_non_cv(text_lower: str) -> str | None:
    """Return matched non-CV category, or None."""
    for category, keywords in NON_CV_SIGNALS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return None


def extract_contacts(text: str) -> tuple[str, str]:
    """Return (email, phone) — first match each, "" if none. Deterministic (no LLM)."""
    email_m = EMAIL_RE.search(text or "")
    phone_m = PHONE_RE.search(text or "")
    email = email_m.group(0).strip() if email_m else ""
    phone = " ".join(phone_m.group(0).split()) if phone_m else ""
    return email, phone


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    filename: str
    status: str          # PASS | FAIL_EXTRACTION | FAIL_STRUCTURE | FAIL_CONTENT | FAIL_NON_CV
    reason: str
    structure_score: float   # 0.0–1.0
    has_email: bool
    has_phone: bool
    has_dates: bool
    has_education: bool
    has_experience: bool


# ── Main validator ─────────────────────────────────────────────────────────────

def validate_cv(parse_result: ParseResult) -> ValidationResult:
    """
    Three-gate validation pipeline.
    Returns ValidationResult with status PASS or a specific FAIL code.
    """

    base = {"filename": parse_result.filename}

    # ── GATE 1: Extraction quality ─────────────────────────────
    if not parse_result.success or parse_result.char_count < 200:
        return ValidationResult(
            **base,
            status="FAIL_EXTRACTION",
            reason=(
                f"Teks tidak cukup ({parse_result.char_count} karakter). "
                f"Kemungkinan PDF hasil scan atau file rusak. "
                f"Error: {parse_result.error or 'tidak diketahui'}"
            ),
            structure_score=0.0,
            has_email=False, has_phone=False, has_dates=False,
            has_education=False, has_experience=False,
        )

    text = parse_result.raw_text
    text_lower = text.lower()

    # ── Component checks ───────────────────────────────────────
    has_email     = bool(EMAIL_RE.search(text))
    has_phone     = bool(PHONE_RE.search(text))
    years_found   = YEAR_RE.findall(text)
    has_dates     = len(years_found) >= 2
    has_education = any(kw in text_lower for kw in EDUCATION_KEYWORDS)
    has_experience= any(kw in text_lower for kw in EXPERIENCE_KEYWORDS)

    # Structure score: weighted presence of key components
    weights = [
        (has_email,      0.15),
        (has_phone,      0.15),
        (has_dates,      0.20),
        (has_education,  0.25),
        (has_experience, 0.25),
    ]
    structure_score = sum(w for flag, w in weights if flag)

    # ── GATE 1.5: Non-CV document (before structure gate for precise labels) ──
    # Checked only when the doc has NEITHER education NOR experience keywords —
    # a real CV almost always has at least one, so this never blocks real CVs.
    if not has_education and not has_experience:
        non_cv = _detect_non_cv(text_lower)
        if non_cv is not None:
            return ValidationResult(
                **base,
                status="FAIL_NON_CV",
                reason=(
                    f"Dokumen terlihat seperti {non_cv} ({parse_result.char_count} karakter), "
                    f"bukan CV: tidak ada bagian pendidikan atau pengalaman."
                ),
                structure_score=structure_score,
                has_email=has_email, has_phone=has_phone, has_dates=has_dates,
                has_education=has_education, has_experience=has_experience,
            )

    # ── GATE 2: Structure integrity ────────────────────────────
    if structure_score < 0.4:
        missing = []
        if not has_email:      missing.append("email")
        if not has_phone:      missing.append("nomor telepon")
        if not has_dates:      missing.append("referensi tahun/tanggal")
        if not has_education:  missing.append("bagian pendidikan")
        if not has_experience: missing.append("bagian pengalaman")

        return ValidationResult(
            **base,
            status="FAIL_STRUCTURE",
            reason=(
                f"Skor struktur CV {structure_score:.0%} — terlalu rendah. "
                f"Komponen yang hilang: {', '.join(missing)}"
            ),
            structure_score=structure_score,
            has_email=has_email, has_phone=has_phone, has_dates=has_dates,
            has_education=has_education, has_experience=has_experience,
        )

    # ── GATE 3: Content sanity (backstop for junk without non-CV signals) ──
    # Missing both edu and exp = not a CV (cover letter, wrong upload,
    # blank form) — regardless of length. Waste no LLM call on it.
    # (Signalled non-CV docs were already caught by GATE 1.5 above.)
    if not has_education and not has_experience:
        return ValidationResult(
            **base,
            status="FAIL_CONTENT",
            reason=(
                f"Dokumen ({parse_result.char_count} karakter) tidak memiliki "
                f"bagian pendidikan maupun pengalaman. Kemungkinan cover letter, "
                f"salah file, atau upload tidak lengkap."
            ),
            structure_score=structure_score,
            has_email=has_email, has_phone=has_phone, has_dates=has_dates,
            has_education=has_education, has_experience=has_experience,
        )

    # ── PASS ───────────────────────────────────────────────────
    return ValidationResult(
        **base,
        status="PASS",
        reason=f"CV valid terdeteksi. Skor struktur: {structure_score:.0%}",
        structure_score=structure_score,
        has_email=has_email, has_phone=has_phone, has_dates=has_dates,
        has_education=has_education, has_experience=has_experience,
    )
