@echo off
title Vitaly's Trading Bot - Crypto Worker
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Clean restart: stop any existing crypto worker before starting a new one.
REM This prevents duplicate OKX collector/analyzer loops when the dashboard is restarted.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and ( $_.CommandLine -like '*src.crypto.main*--job worker*' -or $_.CommandLine -like '*-m src.crypto.main --job worker*' ) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
del /q "memory\crypto\runtime\worker.lock" 2>nul

REM 24/7 public OKX candle collection + Gerchik crypto analysis.
REM Cadence defaults to CRYPTO_COLLECT_INTERVAL_SECONDS in .env, or 300 seconds.
"%PYTHON%" -m src.crypto.main --job worker
