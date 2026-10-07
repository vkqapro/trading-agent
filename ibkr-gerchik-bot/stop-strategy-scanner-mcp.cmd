@echo off
setlocal
set "ROOT=%~dp0"
set "PIDFILE=%ROOT%runtime\strategy_scanner_mcp.pid"
if not exist "%PIDFILE%" (
  echo Strategy Scanner MCP is not running.
  exit /b 0
)
set /p TARGET_PID=<"%PIDFILE%"
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%TARGET_PID%' -ErrorAction SilentlyContinue; if(-not $p){exit 2}; if($p.CommandLine -notlike '*scanner_mcp.server*'){exit 3}; Stop-Process -Id %TARGET_PID% -Force; exit 0"
set "STOP_RESULT=%ERRORLEVEL%"
if "%STOP_RESULT%"=="0" (
  echo Stopped Strategy Scanner MCP process %TARGET_PID%.
  del /q "%PIDFILE%" >nul 2>&1
  exit /b 0
)
if "%STOP_RESULT%"=="2" (
  echo Strategy Scanner MCP process %TARGET_PID% was not running.
  del /q "%PIDFILE%" >nul 2>&1
  exit /b 0
)
echo Refusing to stop PID %TARGET_PID% because it is not the Strategy Scanner MCP process.
exit /b 1
