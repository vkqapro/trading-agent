@echo off
title Gerchik Bot - Market Data Collector
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Data-only IBKR session. It never evaluates signals or places orders.
REM Client id 17 keeps it separate from trading and execution workers.
"%PYTHON%" -m src.main --job market_data --client-id 17
