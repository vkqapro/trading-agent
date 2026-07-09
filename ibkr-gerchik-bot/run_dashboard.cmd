@echo off
REM Launch Vitaly's Trading Bot dashboard together with the order-execution worker.
REM The worker (paper-only, respects DRY_RUN) consumes Place/Close requests from
REM the dashboard and needs TWS / IB Gateway running to submit to the paper account.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

REM Start the order-execution worker in its own window (close it to stop executing).
REM Delegates to run_execute_worker.cmd, which cleanly restarts the worker
REM (stops any existing one, clears its heartbeat) on dedicated client id 11.
start "Vitaly's Execute Worker" cmd /k "%~dp0run_execute_worker.cmd"

REM Start the dashboard in this window.
python -m streamlit run dashboard\app.py
