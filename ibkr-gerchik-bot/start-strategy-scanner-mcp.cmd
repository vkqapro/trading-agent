@echo off
setlocal EnableDelayedExpansion
set "ROOT=%~dp0"
set "ENGINE=%ROOT%backtest_engine_v24.4"
set "PIDFILE=%ROOT%runtime\strategy_scanner_mcp.pid"
set "OUTLOG=%ROOT%runtime\strategy_scanner_mcp.stdout.log"
set "ERRLOG=%ROOT%runtime\strategy_scanner_mcp.stderr.log"
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
for %%I in ("%ROOT%..\.venv\Scripts\python.exe") do set "PYTHON_EXE=%%~fI"
if not exist "%PYTHON_EXE%" (
  echo Expected project virtualenv Python was not found: %PYTHON_EXE%
  echo Refusing to fall back to system Python.
  exit /b 1
)
"%PYTHON_EXE%" -c "import importlib.metadata as md; import sys; required=('pandas','uvicorn','mcp'); missing=[]; versions={}; [versions.setdefault(name, md.version(name)) if md.version(name) else None for name in required]; major=int(versions['mcp'].split('.',1)[0]); sys.exit(2 if major >= 2 else 0)" >nul 2>&1
if errorlevel 2 (
  echo Incompatible MCP dependency. Strategy Scanner MCP requires mcp ^>=1,^<2.
  echo Install the project requirements into %PYTHON_EXE%.
  exit /b 1
)
if errorlevel 1 (
  echo Missing required dependencies for Strategy Scanner MCP in %PYTHON_EXE%.
  echo Required imports: pandas, uvicorn, mcp ^(mcp ^>=1,^<2^).
  exit /b 1
)
if exist "%PIDFILE%" (
  set /p EXISTING_PID=<"%PIDFILE%"
  set "PID_OWNS_PORT="
  for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8766 .*LISTENING"') do if "%%P"=="!EXISTING_PID!" set "PID_OWNS_PORT=1"
  if defined PID_OWNS_PORT (
    powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 3; if($r.status -eq 'ok' -and $r.service -eq 'Strategy Scanner MCP'){exit 0} } catch {}; exit 1"
    if not errorlevel 1 (
      echo Strategy Scanner MCP is already running with PID !EXISTING_PID!.
      echo Health: http://127.0.0.1:8766/health
      exit /b 0
    )
    echo Strategy Scanner MCP PID !EXISTING_PID! exists but readiness check failed.
    echo Refusing to start a duplicate process. Check %ERRLOG%
    exit /b 1
  )
  del /q "%PIDFILE%" >nul 2>&1
)
rem Recover a scanner process launched without its PID file. This prevents a
rem duplicate start attempt from failing with WinError 10048 while the existing
rem canonical scanner MCP is already serving on port 8766.
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8766 .*LISTENING"') do set "PORT_PID=%%P"
if defined PORT_PID (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 3; if($r.status -eq 'ok' -and $r.service -eq 'Strategy Scanner MCP'){exit 0} } catch {}; exit 1"
  if not errorlevel 1 (
    >"%PIDFILE%" echo !PORT_PID!
    echo Strategy Scanner MCP is already running with PID !PORT_PID!.
    echo Health: http://127.0.0.1:8766/health
    exit /b 0
  )
  echo Port 8766 is already in use by PID !PORT_PID!, but Strategy Scanner MCP readiness failed.
  echo Refusing to start a duplicate process. Check %ERRLOG%
  exit /b 1
)
set "PYTHONUNBUFFERED=1"
set "STRATEGY_SCANNER_MCP_HOST=127.0.0.1"
set "STRATEGY_SCANNER_MCP_PORT=8766"
set "STRATEGY_SCANNER_MCP_PID_FILE=%PIDFILE%"
pushd "%ENGINE%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start_strategy_scanner_mcp_process.ps1" -PythonExe "%PYTHON_EXE%" -EngineDir "%ENGINE%" -PidFile "%PIDFILE%" -StdoutLog "%OUTLOG%" -StderrLog "%ERRLOG%"
if errorlevel 1 (
  popd
  echo Failed to launch Strategy Scanner MCP process. Check %ERRLOG%
  exit /b 1
)
popd
for /l %%I in (1,1,40) do (
  if exist "%PIDFILE%" (
    powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 2; if($r.status -eq 'ok' -and $r.service -eq 'Strategy Scanner MCP'){exit 0} } catch {}; exit 1"
    if not errorlevel 1 goto :started
  )
  powershell -NoProfile -Command "Start-Sleep -Milliseconds 250"
)
echo Failed to start ready Strategy Scanner MCP. Check %ERRLOG%
exit /b 1

:started
set /p STARTED_PID=<"%PIDFILE%"
echo Strategy Scanner MCP started with PID %STARTED_PID%.
echo MCP URL: http://127.0.0.1:8766/mcp
echo Health:  http://127.0.0.1:8766/health
echo Logs:    %OUTLOG% and %ERRLOG%
