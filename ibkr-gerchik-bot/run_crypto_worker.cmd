@echo off
title Gerchik Crypto Worker - OKX 24/7 Collector
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM 24/7 public OKX candle collection + Gerchik crypto analysis.
REM Cadence defaults to CRYPTO_COLLECT_INTERVAL_SECONDS in .env, or 300 seconds.
"%PYTHON%" -m src.crypto.main --job worker
