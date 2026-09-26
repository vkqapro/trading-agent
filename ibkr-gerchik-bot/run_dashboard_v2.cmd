@echo off
REM Launch the Gerchik Bot dashboard (Luminous Obsidian v2).
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"
"%PYTHON%" -m streamlit run dashboard\app.py --server.port 8534
