@echo off
REM Restart the dashboard order-execution worker as a CLEAN restart:
REM   1) stop any worker that is already running,
REM   2) clear its heartbeat so the single-instance guard won't block the new one,
REM   3) start a fresh worker (paper-only, respects DRY_RUN_MODE, client id 11).
REM Requires TWS / IB Gateway running.
cd /d "%~dp0"

REM 1) Stop any existing execute_requests worker(s). Single quotes only inside the
REM    -Command string so there is no fragile cmd/powershell quote escaping.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*execute_requests*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul

REM 2) Clear the stale heartbeat so the new worker is allowed to start immediately.
del /q "memory\runtime\execute_worker.json" 2>nul

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

REM 3) Start the fresh worker.
python -m src.main --job execute_requests --client-id 11
