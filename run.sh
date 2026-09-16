#!/usr/bin/env bash
# CV Screening Pipeline — launcher (WSL / Linux / macOS)
# Usage: ./run.sh [1|2|3|4]   (no arg = interactive menu)
set -u
cd "$(dirname "$0")"

PY="/home/user/.venvs/ats-filter/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Venv tidak ditemukan di $PY"
  echo "Setup sekali:"
  echo "  uv venv /home/user/.venvs/ats-filter --python 3.14"
  echo "  VIRTUAL_ENV=/home/user/.venvs/ats-filter uv pip install -r requirements.txt"
  exit 1
fi

confirm() { read -rp "$1 (Enter=lanjut, Ctrl+C=batal) " _; }

menu() {
  echo "============================================================"
  echo "  CV Screening Pipeline"
  echo "============================================================"
  echo "  [1] Test cepat (dry-run, GRATIS - tanpa LLM)"
  echo "  [2] Score 2 CV pertama (test kecil, pakai LLM)"
  echo "  [3] Full run semua CV (pakai LLM, hemat via cache)"
  echo "  [4] Full run + score ulang semua (pakai LLM, abaikan cache)"
  echo "  [0] Keluar"
  read -rp "Pilih [0-4]: " choice
}

run_choice() {
  case "$1" in
    1) "$PY" main.py --dry-run ;;
    2) confirm "Akan score 2 CV pertama (pakai LLM)."; "$PY" main.py --limit 2 ;;
    3) confirm "Akan FULL RUN semua CV (pakai LLM)."; "$PY" main.py ;;
    4) confirm "Akan FULL RUN + SCORE ULANG (biaya penuh)."; "$PY" main.py --force ;;
    0) exit 0 ;;
    *) echo "Pilihan tidak dikenal."; return 1 ;;
  esac
}

if [[ $# -ge 1 ]]; then
  run_choice "$1"
else
  while true; do menu; run_choice "${choice:-}" && { echo; echo "Hasil Excel: output/results/"; read -rp "Enter = kembali ke menu, Ctrl+C = keluar. " _; }; done
fi
