@echo off
title Vitaly's Trading Bot - Market Data Collector
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Single-instance ownership is enforced by job_loop_lock("market_data_collector").
REM Do not kill existing collectors or delete locks here; duplicate cleanup requires
REM operator approval after inspecting process ancestry and heartbeat ownership.

REM Data-only IBKR session. It never evaluates signals or places orders.
REM Client id 17 keeps it separate from trading and execution workers.
"%PYTHON%" -m src.main --job market_data --client-id 17
