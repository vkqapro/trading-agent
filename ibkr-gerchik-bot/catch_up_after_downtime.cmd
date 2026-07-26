@echo off
setlocal

cd /d "%~dp0"

set PYTHON_EXE=python
if exist "..\.venv\Scripts\python.exe" set PYTHON_EXE=..\.venv\Scripts\python.exe
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=.venv\Scripts\python.exe

echo.
echo ============================================================
echo IBKR bot catch-up after downtime
echo Working directory: %CD%
echo Python: %PYTHON_EXE%
echo ============================================================
echo.
echo Make sure IB Gateway or TWS is running and API is connected.
echo.

echo [1/4] Catching up candle cache...
%PYTHON_EXE% -m src.main --job market_data --force --once --client-id 111
if errorlevel 1 goto failed

echo.
echo [2/4] Recalculating premarket levels and trade zones...
%PYTHON_EXE% -m src.main --job premarket --client-id 101
if errorlevel 1 goto failed

echo.
echo [3/4] Hydrating IRS candles and running IRS premarket context scan...
%PYTHON_EXE% -m src.main --job irs_scan --irs-mode PREMARKET_CONTEXT --client-id 101
if errorlevel 1 goto failed

echo.
echo [4/4] Validating current watchlist signals...
%PYTHON_EXE% -m src.main --job validate_watchlist --client-id 102
if errorlevel 1 goto failed

echo.
echo ============================================================
echo Catch-up completed successfully.
echo ============================================================
pause
exit /b 0

:failed
echo.
echo ============================================================
echo Catch-up stopped because one step failed.
echo Check memory\runtime\application.log for details.
echo ============================================================
pause
exit /b 1
