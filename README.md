# CV Screening Pipeline

Filter kandidat otomatis bertenaga AI untuk bulk export JobStreet.
1.400+ CV diproses lewat pipeline 6 tahap → shortlist Excel berperingkat.

LLM bebas: endpoint apa pun yang OpenAI-compatible (POST {BASE_URL}/chat/completions)
— Meta Muse Spark, OpenAI/Codex, OpenRouter, Ollama lokal. Tanpa vendor SDK.
Seluruh hasil (skor, ringkasan, Excel, console) dalam Bahasa Indonesia.

---

## Arsitektur

```text
cv/ (PDF CV, atau atur INPUT_DIR di .env)
        │
        ▼
[1] PDF Parser        pdfplumber → pymupdf fallback
        │             ParseResult{text, chars, method, pages_parsed, success}
        │             PDF_MAX_PAGES=0 (semua) atau N (N halaman pertama)
        ▼
[2] Markdown Writer   Satu .md per kandidat (audit trail)
        │             output/markdown/nama_kandidat.md
        ▼
[3] Validation Gate   EXTRACTION → NON_CV → STRUCTURE → CONTENT
        │             PASS → lanjut | FAIL → sheet Gagal Validasi (tanpa biaya LLM)
        ▼
[4] Rubrik Anchor     1x per posisi dari lowongan → indikator observable +
        │             anchor tiap band skor (cache output/rubrics/)
        ▼
[5] LLM Scoring       OpenAI-compatible API + rubrik anchor + fairness check
        │             JSON: skor per dimensi + reasoning (ID) + token usage
        │             Retry + backoff 429/5xx; 401/403/404 fail-fast berpesan jelas
        ▼
[6] Agregasi + Excel  JSON per kandidat (output/scores/, resume cache) +
                      knockout filter + dedup → 3 sheet: Ranking | Gagal | Ringkasan
```

---

## Setup (sekali saja)

```bash
# 1. Masuk folder project
cd 32-ATS-FILTER

# 2. Buat venv + install (WSL/Linux — butuh uv: https://docs.astral.sh/uv/)
uv venv /home/user/.venvs/ats-filter --python 3.14
VIRTUAL_ENV=/home/user/.venvs/ats-filter uv pip install -r requirements.txt

# 3. Konfigurasi (WAJIB untuk full run)
cp .env.example .env
# lalu isi di .env: LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
#   + kriteria: JOB_TITLE, JOB_LOCATION, MIN_YEARS_EXPERIENCE
#   + lowongan: JOB_DESCRIPTION_FILE=lowongan.txt
```

## Pemakaian harian

```text
Windows : double-click run.bat → menu 1-4 + konfirmasi Enter
WSL     : ./run.sh (menu) atau ./run.sh 3 (langsung full run)
Manual  : /home/user/.venvs/ats-filter/bin/python main.py [opsi]
```

Opsi:

```text
--dry-run    parse + validasi saja, tanpa panggilan LLM (gratis).
             File output ditandai _DRYRUN_ agar tak tertukar.
--limit N    proses N PDF pertama saja (smoke test, mis. --limit 2)
--force      score ulang semua + bangun ulang rubrik (biaya penuh)
```

Tanpa flag: full run hemat — yang sudah ada cache JSON-nya dilewati
(kecuali hasil ERROR, selalu dicoba ulang).

---

## Konfigurasi LLM (custom base URL + key + model)

Cukup 3 variabel di `.env`:

```text
LLM_BASE_URL=https://api.meta.ai/v1
LLM_MODEL=muse-spark-1.3-contributor
LLM_API_KEY=...
```

Contoh siap pakai:

```text
# Meta Muse Spark
LLM_BASE_URL=https://api.meta.ai/v1
LLM_MODEL=muse-spark-1.3-contributor

# OpenAI / Codex (model via API OpenAI)
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-5-codex            # atau nama model codex lain di akun Anda

# OpenRouter (satu key untuk banyak model)
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-4

# Ollama lokal (gratis, tanpa key)
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:14b
LLM_API_KEY=ollama
```

Catatan: Codex CLI (terminal agent) bukan endpoint HTTP — yang dipakai di sini
adalah model Codex via API OpenAI yang kompatibel seperti contoh di atas.

---

## Kriteria Knockout (filter keras, via `.env`)

```text
JOB_LOCATION=Bekasi, Jakarta   # hanya domisili ini yang lolos (kosong = mati)
MIN_YEARS_EXPERIENCE=3         # minimal pengalaman tahun (0 = mati)
JOB_DESCRIPTION_FILE=lowongan.txt   # deskripsi lowongan panjang via file
```

Cara kerja: model mengekstrak domisili saat ini + total pengalaman tiap CV,
kode verifikasi silang. Gagal salah satu → REJECT otomatis berlabel
[KNOCKOUT] di kolom Ringkasan + "GUGUR: ..." di kolom Knockout + red flag
"Gugur knockout: ...". Skor asli tetap tercatat untuk audit.
Domisili tidak jelas = gagal (tidak diasumsikan lolos).

---

## Rubrik Anchor (akurasi scoring)

Sebelum scoring, pipeline membangun rubrik 1x per posisi dari lowongan:
indikator observable + deskripsi tiap band skor (9-10 … 1-2) yang spesifik
untuk posisi Anda — bukan skala generik. Disimpan di output/rubrics/
(1 panggilan LLM, dipakai ulang semua CV). Ganti lowongan → rubrik baru
otomatis. `--force` membangun ulang. Bila gagal, scoring lanjut dengan
skala generik tanpa memblokir batch.

Teknik diadaptasi dari hr-skills (tuanductran, MIT) — anchored scales,
observable indicators, fairness review.

## Fairness & Bahasa

- Model dilarang memakai sinyal SARA/usia/foto/penampilan; bila suatu dimensi
  tak bisa dinilai adil, ia wajib menandainya (`fairness_ok: false`) dan
  catatan masuk kolom Kekurangan — skor tetap jalan.
- Email + No. HP/WA diekstrak deterministik via regex (tanpa LLM, tanpa
  halusinasi) ke kolom sendiri. Kosong → "—", bukan dikarang.

---

## Output

```text
output/
├── markdown/   ← satu .md per CV (audit trail + info halaman terparse)
├── scores/     ← satu .json per CV (hasil LLM + resume cache)
├── rubrics/    ← rubrik anchor per posisi (cache, 1x LLM call)
└── results/
    └── cv_screening_HR_Manager_20250915_151500.xlsx
```

Sheet Excel (semua Bahasa Indonesia):

- **Ranking Kandidat** — urut skor, color-coded (hijau/kuning/merah), filterable.
  Kolom: Nama File, Nama Kandidat, Skor, Rekomendasi, Lokasi, No. HP/WA,
  Email, Knockout, Pengalaman (th), Token Masuk/Keluar/Reasoning/Total,
  Skill Utama, Ringkasan, Kelebihan, Kekurangan, Skor+Alasan per dimensi.
  (Kode status SHORTLIST/REVIEW/REJECT/ERROR dan FAIL_* sengaja Inggris —
  dipakai logika program dan resume cache.)
- **Gagal Validasi** — file tak lolos gate + alasan + checklist komponen.
- **Ringkasan** — statistik pipeline, rincian gagal, total token,
  estimasi biaya (bila LLM_COST_*_PER_1M diisi).

Hemat biaya & tahan crash:

- Gate validasi menggugurkan file rusak/salah sebelum bakar token LLM.
- CV duplikat (teks identik) di-score sekali saja.
- Run terputus di CV ke-900? Jalankan lagi — cache JSON dilewati.
  Hasil ERROR tidak pernah di-cache, selalu dicoba ulang.
- Satu file crash tidak membunuh batch — dicatat ERROR, run lanjut.
- Retry + backoff untuk 429/5xx/timeout; 401/403/404 dan budget token
  habis langsung fail-fast berpesan jelas.

---

## Tabel Konfigurasi (config.py / `.env`)

| Key | Keterangan | Default |
|---|---|---|
| `LLM_BASE_URL` | Endpoint OpenAI-compatible | `https://api.meta.ai/v1` |
| `LLM_API_KEY` | API key (atau `META_API_KEY`) | (kosong) |
| `LLM_MODEL` | Nama model | `muse-spark-1.3-contributor` |
| `LLM_REASONING_EFFORT` | `low` / `medium` / `high` (kosong = mati) | `medium` |
| `LLM_MAX_TOKENS` | Budget token per scoring (reasoning ikut makan!) | `6000` |
| `RUBRIC_MAX_TOKENS` | Budget token pembuatan rubrik | `8000` |
| `LLM_TIMEOUT_SECONDS` | Timeout HTTP per panggilan | `120` |
| `LLM_MAX_RETRIES` | Retry untuk 429/5xx | `4` |
| `API_DELAY_SECONDS` | Jeda antar panggilan LLM | `0.5` |
| `LLM_COST_INPUT_PER_1M` | Tarif input USD/1jt token (0 = sembunyikan biaya) | `0` |
| `LLM_COST_OUTPUT_PER_1M` | Tarif output USD/1jt token | `0` |
| `INPUT_DIR` | Folder PDF CV | `cv` (otomatis bila ada) |
| `JOB_TITLE` | Nama posisi | `"Software Engineer"` |
| `JOB_DESCRIPTION` | Syarat posisi (kalah bila file di bawah ada) | contoh bawaan |
| `JOB_DESCRIPTION_FILE` | File deskripsi lowongan, mis. `lowongan.txt` | (kosong) |
| `JOB_LOCATION` | Domisili diterima, koma. Kosong = mati | (kosong) |
| `MIN_YEARS_EXPERIENCE` | Minimal pengalaman tahun. 0 = mati | `0` |
| `SCORING_DIMENSIONS` | Dimensi skor (hanya via config.py) | 5 dimensi |
| `SHORTLIST_MIN_SCORE` | Batas SHORTLIST | `7.0` |
| `REVIEW_MIN_SCORE` | Batas REVIEW | `5.0` |
| `MIN_CHAR_COUNT` | Min karakter lolos gate ekstraksi | `200` |
| `MIN_STRUCTURE_SCORE` | Min skor struktur 0-1 | `0.4` |
| `CV_HEAD_CHARS` / `CV_TAIL_CHARS` | Truncation CV panjang (kepala+ekor) | `6000` / `1000` |
| `PDF_MAX_PAGES` | 0 = semua halaman (disarankan), N = N halaman pertama | `0` |

---

## Logika Validation Gate

```text
FAIL_EXTRACTION  → pdfplumber + pymupdf < 200 karakter
                   (PDF scan, file rusak, terproteksi password)

FAIL_NON_CV      → cocok sinyal non-CV (invoice/kwitansi, skripsi/tesis,
                   brosur/promo, iklan lowongan) DAN tanpa bagian
                   pendidikan/pengalaman. Dicek sebelum structure gate
                   agar labelnya presisi.

FAIL_STRUCTURE   → skor struktur < 40%.
                   Yang hilang: email, nomor telepon, referensi tahun/tanggal,
                   bagian pendidikan, bagian pengalaman.

FAIL_CONTENT     → tanpa bagian pendidikan maupun pengalaman, sepanjang apa pun
                   (cover letter, salah file, upload tidak lengkap).
                   Dokumen panjang non-CV yang lolos masih dijaring lagi oleh
                   model ("is_cv": false → auto-REJECT).

PASS             → semua gate lolos → dikirim ke LLM.
```

---

## Estimasi Biaya

Per kandidat ≈ 8.000–9.000 token total (prompt ber-anchor + reasoning medium).
Rumus: `jumlah_CV × ~8.500 × tarif_provider_Anda`.
Isi LLM_COST_INPUT/OUTPUT_PER_1M di .env agar sheet Ringkasan menampilkan
estimasi biaya otomatis. Ordo: belasan USD untuk 1.400 CV di model kelas menengah.

---

## Struktur File

```text
main.py            orkestrasi + CLI (--dry-run/--limit/--force) + resume cache
config.py          semua konfigurasi + load .env
parser.py          PDF → teks (pdfplumber → pymupdf, batas halaman)
validator.py       gate 4 tingkat + deteksi non-CV + ekstrak kontak (regex)
rubric.py          bangun rubrik anchor per posisi (1x LLM, cache)
scorer.py          klien HTTP OpenAI-compatible + prompt + parsing JSON + knockout
exporter.py        Excel 3 sheet Bahasa Indonesia + token + estimasi biaya
markdown_writer.py .md audit trail per kandidat
run.bat / run.sh  launcher menu (Windows / WSL)
```

---

## Troubleshooting

```text
LLM_API_KEY belum diisi   → cp .env.example .env, isi key; atau --dry-run
auth 401/403              → key salah / tak cocok dengan BASE_URL
404 model tidak ada       → cek LLM_MODEL, tiap provider beda penamaan
429 / timeout berulang    → naikkan API_DELAY_SECONDS / LLM_TIMEOUT_SECONDS
empty content (length)    → naikkan LLM_MAX_TOKENS (reasoning makan budget)
Skor 0.0 + ERROR terus    → baca kolom Ringkasan baris itu (pesan jelas di sana)
Excel kosong (0 baris)    → itu file _DRYRUN_ (sengaja tanpa scoring)
ModuleNotFoundError       → venv salah; pakai /home/user/.venvs/ats-filter
```

---

## Extending the Pipeline

- **Multi-position**: buat beberapa `job_config` dan panggil `score_cv()` per posisi
  (rubrik terpisah otomatis per hash konfigurasi).
- **Batch API**: ganti `_chat_complete()` di `scorer.py` dengan batch endpoint provider Anda.
- **OCR fallback**: tambah Tesseract/AWS Textract di `parser.py` saat `success=False`
  (kasus Eggy: PDF scan 15 halaman, 0 karakter).
- **Google Sheets**: ganti `export_to_excel()` dengan `gspread` di `exporter.py`.
- **Normalisasi HP**: samakan format `08…` → `+62…` di `extract_contacts()`.
