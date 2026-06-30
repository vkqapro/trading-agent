@echo off
title Gerchik Crypto Bot - OKX Candle Collector
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Public OKX candle collection. No API key required for market data.
"%PYTHON%" -m src.crypto.main --job collect_analyze
