@echo off
title Vitaly's Trading Bot - Hard Reset
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Vitaly's Trading Bot  HARD RESET
echo   Closing dashboard services and restarting the stack...
echo  ============================================================
echo.

REM Give the API response a moment to return before stopping this server.
timeout /t 2 /nobreak >nul

REM Close dashboard helper windows first. Ignore errors because some may not be open.
taskkill /FI "WINDOWTITLE eq Gerchik Execute Worker*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Gerchik Market Data Collector*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Gerchik Crypto Worker*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Gerchik Bot * React Dashboard*" /T /F >nul 2>nul

REM Stop Python services used by the dashboard stack.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and ( $_.CommandLine -like '*dashboard_react.server:app*' -or $_.CommandLine -like '*--job execute_requests*' -or $_.CommandLine -like '*--job market_data*' -or $_.CommandLine -like '*src.crypto.main --job worker*' -or $_.CommandLine -like '*-m src.crypto.main --job worker*' ) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul

REM Clear transient single-instance files so workers can restart immediately.
del /q "memory\runtime\execute_worker.json" >nul 2>nul
del /q "memory\runtime\market_data_collector.lock" >nul 2>nul
del /q "memory\crypto\runtime\worker.lock" >nul 2>nul

timeout /t 1 /nobreak >nul

REM Reopen the full dashboard bundle: API, market-data collector, execute worker,
REM and crypto worker. This script returns after launching the new dashboard window.
start "Gerchik Bot React Dashboard" cmd /k "%~dp0run_react_dashboard.cmd"

exit /b 0
