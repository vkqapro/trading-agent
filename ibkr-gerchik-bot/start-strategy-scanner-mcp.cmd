@echo off
setlocal EnableDelayedExpansion
set "ROOT=%~dp0"
set "ENGINE=%ROOT%backtest_engine_v24.4"
set "PIDFILE=%ROOT%runtime\strategy_scanner_mcp.pid"
set "OUTLOG=%ROOT%runtime\strategy_scanner_mcp.stdout.log"
set "ERRLOG=%ROOT%runtime\strategy_scanner_mcp.stderr.log"
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
if exist "%PIDFILE%" (
  set /p EXISTING_PID=<"%PIDFILE%"
  powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=!EXISTING_PID!' -ErrorAction SilentlyContinue; if($p -and $p.CommandLine -like '*scanner_mcp.server*'){exit 0}else{exit 1}"
  if not errorlevel 1 (
    echo Strategy Scanner MCP is already running with PID !EXISTING_PID!.
    echo Health: http://127.0.0.1:8766/health
    exit /b 0
  )
  del /q "%PIDFILE%" >nul 2>&1
)
rem Recover a scanner process launched without its PID file. This prevents a
rem duplicate start attempt from failing with WinError 10048 while the existing
rem canonical scanner MCP is already serving on port 8766.
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8766 .*LISTENING"') do set "PORT_PID=%%P"
if defined PORT_PID (
  powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'ProcessId=!PORT_PID!' | Select-Object -ExpandProperty CommandLine" | findstr /I /C:"scanner_mcp.server" >nul
  if not errorlevel 1 (
    >"%PIDFILE%" echo !PORT_PID!
    echo Strategy Scanner MCP is already running with PID !PORT_PID!.
    echo Health: http://127.0.0.1:8766/health
    exit /b 0
  )
)
set "PYTHONUNBUFFERED=1"
set "STRATEGY_SCANNER_MCP_HOST=127.0.0.1"
set "STRATEGY_SCANNER_MCP_PORT=8766"
set "STRATEGY_SCANNER_MCP_PID_FILE=%PIDFILE%"
pushd "%ENGINE%"
start "" /b python -m scanner_mcp.server 1>>"%OUTLOG%" 2>>"%ERRLOG%"
popd
for /l %%I in (1,1,40) do (
  if exist "%PIDFILE%" goto :started
  powershell -NoProfile -Command "Start-Sleep -Milliseconds 250"
)
echo Failed to start Strategy Scanner MCP. Check %ERRLOG%
exit /b 1

:started
set /p STARTED_PID=<"%PIDFILE%"
echo Strategy Scanner MCP started with PID %STARTED_PID%.
echo MCP URL: http://127.0.0.1:8766/mcp
echo Health:  http://127.0.0.1:8766/health
echo Logs:    %OUTLOG% and %ERRLOG%
