@echo off
setlocal EnableExtensions EnableDelayedExpansion

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
if exist "memory\runtime\market_data_collector.lock" (
    set /p COLLECTOR_LOCK=<"memory\runtime\market_data_collector.lock"
    echo Existing market-data collector lock: !COLLECTOR_LOCK!
    echo If step 1 says the collector is already active, catch-up will continue with the active collector.
)
echo.

call :stamp "Starting step 1/4: candle cache catch-up"
echo [1/4] Catching up candle cache...
%PYTHON_EXE% -m src.main --job market_data --force --once --client-id 111
if errorlevel 1 goto failed
call :stamp "Finished step 1/4"

echo.
call :stamp "Starting step 2/4: premarket levels and trade zones"
echo [2/4] Recalculating premarket levels and trade zones...
echo This step can take several minutes for a large watchlist.
%PYTHON_EXE% -m src.main --job premarket --client-id 101
if errorlevel 1 goto failed
call :stamp "Finished step 2/4"

echo.
call :stamp "Starting step 3/4: IRS hydration and scan"
echo [3/4] Hydrating IRS candles and running IRS premarket context scan...
echo This step may wait on IBKR historical-data pacing.
%PYTHON_EXE% -m src.main --job irs_scan --irs-mode PREMARKET_CONTEXT --client-id 101
if errorlevel 1 goto failed
call :stamp "Finished step 3/4"

echo.
call :stamp "Starting step 4/4: watchlist validation"
echo [4/4] Validating current watchlist signals...
%PYTHON_EXE% -m src.main --job validate_watchlist --client-id 102
if errorlevel 1 goto failed
call :stamp "Finished step 4/4"

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

:stamp
echo [%date% %time%] %~1
exit /b 0
