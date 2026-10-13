@echo off
setlocal
set "ROOT=%~dp0"
set "PIDFILE=%ROOT%runtime\strategy_scanner_mcp.pid"
if not exist "%PIDFILE%" (
  echo Strategy Scanner MCP is not running.
  exit /b 0
)
set /p TARGET_PID=<"%PIDFILE%"
set "PID_OWNS_PORT="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8766 .*LISTENING"') do if "%%P"=="%TARGET_PID%" set "PID_OWNS_PORT=1"
if not defined PID_OWNS_PORT (
  tasklist /FI "PID eq %TARGET_PID%" 2>nul | findstr /R /C:"[ ]%TARGET_PID% " >nul
  if errorlevel 1 (
    echo Strategy Scanner MCP process %TARGET_PID% was not running.
    del /q "%PIDFILE%" >nul 2>&1
    exit /b 0
  )
  echo Refusing to stop PID %TARGET_PID% because it does not own Strategy Scanner MCP port 8766.
  exit /b 1
)
powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 3; if($r.status -eq 'ok' -and $r.service -eq 'Strategy Scanner MCP'){Stop-Process -Id %TARGET_PID% -Force; exit 0} } catch {}; exit 3"
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
echo Refusing to stop PID %TARGET_PID% because Strategy Scanner MCP readiness could not be verified.
exit /b 1
