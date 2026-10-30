@echo off
setlocal EnableDelayedExpansion
set "ROOT=%~dp0"
set "PIDFILE=%ROOT%runtime\trading_bot_mcp.pid"
set "OUTLOG=%ROOT%runtime\trading_bot_mcp.stdout.log"
set "ERRLOG=%ROOT%runtime\trading_bot_mcp.stderr.log"
cd /d "%ROOT%"
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
for %%I in ("%ROOT%..\.venv\Scripts\python.exe") do set "PYTHON_EXE=%%~fI"
if not exist "%PYTHON_EXE%" (
  echo Expected project virtualenv Python was not found: %PYTHON_EXE%
  echo Refusing to fall back to system Python.
  exit /b 1
)
"%PYTHON_EXE%" -c "import importlib.metadata as md; import sys; required=('fastapi','uvicorn','mcp'); versions={}; [versions.setdefault(name, md.version(name)) for name in required]; major=int(versions['mcp'].split('.',1)[0]); sys.exit(2 if major >= 2 else 0)" >nul 2>&1
if errorlevel 2 (
  echo Incompatible MCP dependency. Trading Bot MCP requires mcp ^>=1,^<2.
  echo Install the project requirements into %PYTHON_EXE%.
  exit /b 1
)
if errorlevel 1 (
  echo Missing required dependencies for Trading Bot MCP in %PYTHON_EXE%.
  echo Required imports: fastapi, uvicorn, mcp ^(mcp ^>=1,^<2^).
  exit /b 1
)
if exist "%PIDFILE%" (
  set /p EXISTING_PID=<"%PIDFILE%"
  set "PID_OWNS_PORT="
  for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8765 .*LISTENING"') do if "%%P"=="!EXISTING_PID!" set "PID_OWNS_PORT=1"
  if defined PID_OWNS_PORT (
    powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 3; if($r.status -eq 'ok' -and $r.service -eq 'Trading Bot Data MCP'){exit 0} } catch {}; exit 1"
    if not errorlevel 1 (
      echo Trading Bot Data MCP is already running with PID !EXISTING_PID!.
      echo Health: http://127.0.0.1:8765/health
      exit /b 0
    )
    echo Trading Bot MCP PID !EXISTING_PID! owns port 8765 but readiness failed.
    echo Refusing to start a duplicate process. Check %ERRLOG%
    exit /b 1
  )
  del /q "%PIDFILE%" >nul 2>&1
)
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8765 .*LISTENING"') do set "PORT_PID=%%P"
if defined PORT_PID (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 3; if($r.status -eq 'ok' -and $r.service -eq 'Trading Bot Data MCP'){exit 0} } catch {}; exit 1"
  if not errorlevel 1 (
    >"%PIDFILE%" echo !PORT_PID!
    echo Trading Bot Data MCP is already running with PID !PORT_PID!.
    echo Health: http://127.0.0.1:8765/health
    exit /b 0
  )
  echo Port 8765 is already in use by PID !PORT_PID!, but Trading Bot MCP readiness failed.
  echo Refusing to start a duplicate process. Check %ERRLOG%
  exit /b 1
)
set "TRADING_BOT_MCP_HOST=127.0.0.1"
set "TRADING_BOT_MCP_PORT=8765"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%scripts\start_detached_python_service.ps1' -PythonExe '%PYTHON_EXE%' -WorkingDirectory '%ROOT%' -PidFile '%PIDFILE%' -StdoutLog '%OUTLOG%' -StderrLog '%ERRLOG%' -Environment @('TRADING_BOT_MCP_HOST=127.0.0.1','TRADING_BOT_MCP_PORT=8765') -Arguments @('-m','src.mcp.trading_bot_server')"
if errorlevel 1 (
  echo Failed to launch Trading Bot MCP process. Check %ERRLOG%
  exit /b 1
)
for /l %%I in (1,1,40) do (
  if exist "%PIDFILE%" (
    powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 2; if($r.status -eq 'ok' -and $r.service -eq 'Trading Bot Data MCP'){exit 0} } catch {}; exit 1"
    if not errorlevel 1 goto :started
  )
  powershell -NoProfile -Command "Start-Sleep -Milliseconds 250"
)
echo Failed to start ready Trading Bot MCP. Check %ERRLOG%
exit /b 1

:started
set /p STARTED_PID=<"%PIDFILE%"
echo Trading Bot Data MCP started with PID %STARTED_PID%.
echo MCP URL: http://127.0.0.1:8765/mcp
echo Health:  http://127.0.0.1:8765/health
echo Logs:    %OUTLOG% and %ERRLOG%
