@echo off
title Gerchik Bot · React Dashboard
echo.
echo  ============================================================
echo   Gerchik Bot  React Dashboard
echo   http://127.0.0.1:8550
echo  ============================================================
echo.

cd /d "%~dp0"

REM Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] python not found on PATH.
    pause
    exit /b 1
)

REM Install deps silently if missing
python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Installing fastapi + uvicorn...
    python -m pip install fastapi uvicorn --quiet
)

echo  [OK] Starting server on http://127.0.0.1:8550 ...
echo  [OK] Press Ctrl+C to stop.
echo.

REM Start the independent data-only collector. The launcher performs a clean restart
REM and uses dedicated client id 17.
start "Gerchik Market Data Collector" /min cmd /c "%~dp0run_market_data_collector.cmd"

REM Start the paper order-execution worker in its own window.
REM The launcher performs a clean restart and uses dedicated client id 11.
echo  [OK] Starting execute worker via run_execute_worker.cmd ...
start "Gerchik Execute Worker" cmd /k "%~dp0run_execute_worker.cmd"

REM Start the 24/7 OKX crypto candle collector + analyzer.
REM This uses public OKX market-data endpoints for candles.
start "Gerchik Crypto Worker" /min cmd /k "%~dp0run_crypto_worker.cmd"

REM Give the server 2 seconds then open browser
start "" /min cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8550"

python -m uvicorn dashboard_react.server:app --host 127.0.0.1 --port 8550 --reload
