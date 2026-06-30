@echo off
title Gerchik Crypto Bot - Local Analysis
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Analyze already-saved crypto candles and update memory\crypto\state.json.
"%PYTHON%" -m src.crypto.main --job analyze
