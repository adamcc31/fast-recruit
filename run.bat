@echo off
REM CV Screening Pipeline — double-click to run (Windows)
REM Runs the WSL venv Python in this folder. No install needed after setup.
chcp 65001 >nul
title CV Screening Pipeline
cd /d "%~dp0"

where wsl.exe >nul 2>&1
if errorlevel 1 (
  echo WSL tidak ditemukan. Install WSL dulu: wsl --install
  echo Lalu setup sekali: wsl bash -lc "uv venv /home/user/.venvs/ats-filter --python 3.14 && VIRTUAL_ENV=/home/user/.venvs/ats-filter uv pip install -r requirements.txt"
  pause
  exit /b 1
)

:menu
cls
echo ============================================================
echo   CV Screening Pipeline
echo   Folder: %~dp0
echo ============================================================
echo.
echo   [1] Test cepat (dry-run, GRATIS - tanpa LLM)
echo   [2] Score 2 CV pertama (test kecil, pakai LLM)
echo   [3] Full run semua CV (pakai LLM, hemat via cache)
echo   [4] Full run + score ulang semua (pakai LLM, abaikan cache)
echo   [0] Keluar
echo.
set /p choice="Pilih [0-4]: "

if "%choice%"=="1" goto dryrun
if "%choice%"=="2" goto limittest
if "%choice%"=="3" goto fullrun
if "%choice%"=="4" goto forcerun
if "%choice%"=="0" exit /b 0
echo Pilihan tidak dikenal.
pause
goto menu

:dryrun
echo.
echo Akan jalan DRY-RUN (gratis). Tekan Enter untuk lanjut, Ctrl+C untuk batal.
pause >nul
wsl /home/user/.venvs/ats-filter/bin/python main.py --dry-run
goto done

:limittest
echo.
echo Akan score 2 CV pertama (pakai LLM, biaya kecil). Tekan Enter untuk lanjut, Ctrl+C untuk batal.
pause >nul
wsl /home/user/.venvs/ats-filter/bin/python main.py --limit 2
goto done

:fullrun
echo.
echo Akan FULL RUN semua CV (pakai LLM). Tekan Enter untuk lanjut, Ctrl+C untuk batal.
pause >nul
wsl /home/user/.venvs/ats-filter/bin/python main.py
goto done

:forcerun
echo.
echo Akan FULL RUN + SCORE ULANG semua (biaya penuh). Tekan Enter untuk lanjut, Ctrl+C untuk batal.
pause >nul
wsl /home/user/.venvs/ats-filter/bin/python main.py --force
goto done

:done
echo.
echo Selesai. Hasil Excel ada di folder output\results\
pause
goto menu
