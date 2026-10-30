@echo off
title Vitaly's Trading Bot - Crypto Worker
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Single-instance ownership is enforced inside src.crypto.worker.
REM Do not kill existing workers or delete locks here; duplicate cleanup requires
REM operator approval after inspecting process ancestry and heartbeat ownership.

REM 24/7 public OKX candle collection + Gerchik crypto analysis.
REM Cadence defaults to CRYPTO_COLLECT_INTERVAL_SECONDS in .env, or 300 seconds.
"%PYTHON%" -m src.crypto.main --job worker
