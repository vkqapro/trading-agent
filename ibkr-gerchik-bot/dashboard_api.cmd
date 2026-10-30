@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: dashboard_api.cmd status^|start^|stop^|restart
  exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dashboard_api_lifecycle.ps1" -Action "%~1"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
