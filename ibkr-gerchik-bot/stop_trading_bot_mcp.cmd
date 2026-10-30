@echo off
setlocal
set "ROOT=%~dp0"
set "PIDFILE=%ROOT%runtime\trading_bot_mcp.pid"
if not exist "%PIDFILE%" (
  echo Trading Bot Data MCP is not running.
  exit /b 0
)
for /f "usebackq delims=" %%P in ("%PIDFILE%") do (
  taskkill /PID %%P /T /F >nul 2>&1
  if errorlevel 1 (echo MCP process %%P was not running.) else (echo Stopped Trading Bot Data MCP process %%P.)
)
del /q "%PIDFILE%" >nul 2>&1
