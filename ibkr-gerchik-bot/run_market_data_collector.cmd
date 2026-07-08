@echo off
title Vitaly's Trading Bot - Market Data Collector
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Clean restart: stop any existing market-data collector before starting a new one.
REM This prevents duplicate IBKR client id 17 sessions when the dashboard is restarted.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*--job market_data*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
del /q "memory\runtime\market_data_collector.lock" 2>nul

REM Data-only IBKR session. It never evaluates signals or places orders.
REM Client id 17 keeps it separate from trading and execution workers.
"%PYTHON%" -m src.main --job market_data --client-id 17
