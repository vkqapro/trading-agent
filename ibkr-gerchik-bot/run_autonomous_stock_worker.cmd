@echo off
setlocal
title Vitaly's Trading Bot - Autonomous Stock Worker
cd /d "%~dp0"

set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found for Autonomous Stock Worker.
    exit /b 1
)

echo  [START] Autonomous Stock Worker (persistent lifecycle supervisor)
"%PYTHON%" -m src.jobs.autonomous_stock_worker
set "WORKER_EXIT=%ERRORLEVEL%"
if not "%WORKER_EXIT%"=="0" echo  [ERROR] Autonomous Stock Worker exited with code %WORKER_EXIT%.
endlocal & exit /b %WORKER_EXIT%
