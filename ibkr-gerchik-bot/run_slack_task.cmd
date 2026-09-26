@echo off
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"
"%PYTHON%" -m src.main --job slack --client-id 71
