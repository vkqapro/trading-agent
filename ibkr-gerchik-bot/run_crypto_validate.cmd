@echo off
title Gerchik Crypto Bot - OKX Symbol Validation
cd /d "%~dp0"
set "PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON=..\.venv\Scripts\python.exe"

"%PYTHON%" -m src.crypto.main --job validate
