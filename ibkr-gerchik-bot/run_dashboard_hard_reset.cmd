@echo off
title Vitaly's Trading Bot - Hard Reset
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Vitaly's Trading Bot  HARD RESET
echo   Closing dashboard services and restarting the stack...
echo   ngrok is intentionally left running.
echo  ============================================================
echo.

REM Give the API response a moment to return before stopping this server.
timeout /t 2 /nobreak >nul

REM Stop only this dashboard stack. This script does not target ngrok.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_dashboard_stop_services.ps1" -ClearRuntime >nul 2>nul

timeout /t 2 /nobreak >nul

REM Reopen the full dashboard bundle: API, market-data collector, execute worker,
REM and crypto worker. This script returns after launching the new dashboard window.
start "Vitaly's Trading Bot - React Dashboard" cmd /k "%~dp0run_react_dashboard.cmd"

exit /b 0
