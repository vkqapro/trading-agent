@echo off
REM Launch the Gerchik Bot dashboard.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python -m streamlit run dashboard\app.py
