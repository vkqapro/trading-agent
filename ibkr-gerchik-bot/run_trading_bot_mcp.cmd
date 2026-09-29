@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONUNBUFFERED=1"
if exist "%ROOT%runtime\trading_bot_mcp.pid" (
  for /f "usebackq delims=" %%P in ("%ROOT%runtime\trading_bot_mcp.pid") do tasklist /FI "PID eq %%P" | findstr /R /C:" %%P " >nul && (
    echo Trading Bot Data MCP is already running with PID %%P
    exit /b 1
  )
  del /q "%ROOT%runtime\trading_bot_mcp.pid" >nul 2>&1
)
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
for /f %%P in ('powershell -NoProfile -Command "$p=Start-Process -FilePath python -ArgumentList '-m','src.mcp.trading_bot_server' -WorkingDirectory '%ROOT%' -PassThru; $p.Id"') do echo %%P>"%ROOT%runtime\trading_bot_mcp.pid"
echo Trading Bot Data MCP started. PID is recorded in runtime\trading_bot_mcp.pid
