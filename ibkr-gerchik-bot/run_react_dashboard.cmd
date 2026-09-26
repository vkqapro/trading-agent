@echo off
title Vitaly's Trading Bot - React Dashboard
echo.
echo  ============================================================
echo   Vitaly's Trading Bot  React Dashboard
echo   http://127.0.0.1:8550
echo  ============================================================
echo.

cd /d "%~dp0"

set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

REM Check Python
"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Expected shared venv at ..\.venv\Scripts\python.exe.
    pause
    exit /b 1
)

REM Install deps silently if missing
"%PYTHON%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Installing fastapi + uvicorn...
    "%PYTHON%" -m pip install fastapi uvicorn --quiet
)

echo  [OK] Starting server on http://127.0.0.1:8550 ...
echo  [OK] Press Ctrl+C to stop.
echo.

REM Start the independent data-only collector. The launcher performs a clean restart
REM and uses dedicated client id 17.
start "Vitaly's Trading Bot - Market Data Collector" /min cmd /c "%~dp0run_market_data_collector.cmd"

REM Start the paper order-execution worker in its own window.
REM The launcher performs a clean restart and uses dedicated client id 11.
echo  [OK] Starting execute worker via run_execute_worker.cmd ...
start "Vitaly's Trading Bot - Execute Worker" cmd /k "%~dp0run_execute_worker.cmd"

REM Start the 24/7 OKX crypto candle collector + analyzer.
REM This uses public OKX market-data endpoints for candles.
start "Vitaly's Trading Bot - Crypto Worker" /min cmd /k "%~dp0run_crypto_worker.cmd"

REM Give the server 2 seconds then open browser
start "" /min cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8550"

"%PYTHON%" -m uvicorn dashboard_react.server:app --host 127.0.0.1 --port 8550
