@echo off
setlocal
title Vitaly's Trading Bot - Stop React Dashboard
cd /d "%~dp0"

set "NO_PAUSE="
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"

echo.
echo  ============================================================
echo   Vitaly's Trading Bot  STOP REACT DASHBOARD
echo  ============================================================
echo   Stopping:
echo     - React dashboard API on 127.0.0.1:8550
echo     - IBKR market-data collector
echo     - Paper execute worker
echo     - OKX crypto collector/analyzer
echo.
echo   ngrok, TWS/IB Gateway, browsers, scheduled tasks, and
echo   unrelated Python processes will not be targeted.
echo  ============================================================
echo.

if not exist "%~dp0run_dashboard_stop_services.ps1" (
    echo  [ERROR] Missing run_dashboard_stop_services.ps1
    set "STOP_EXIT=1"
    goto :finish
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_dashboard_stop_services.ps1" -ClearRuntime
set "STOP_EXIT=%ERRORLEVEL%"

if not "%STOP_EXIT%"=="0" (
    echo.
    echo  [ERROR] One or more dashboard services could not be terminated.
    echo          Review the remaining process IDs above.
    goto :finish
)

echo.
echo  [OK] All React dashboard services were stopped and verified.

:finish
echo.
if not defined NO_PAUSE pause
endlocal & exit /b %STOP_EXIT%
