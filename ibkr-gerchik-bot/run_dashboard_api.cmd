@echo off
setlocal EnableDelayedExpansion
set "ROOT=%~dp0"
set "PIDFILE=%ROOT%runtime\dashboard_api.pid"
set "OUTLOG=%ROOT%runtime\dashboard_8550.stdout.log"
set "ERRLOG=%ROOT%runtime\dashboard_8550.stderr.log"
cd /d "%ROOT%"
if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
for %%I in ("%ROOT%..\.venv\Scripts\python.exe") do set "PYTHON_EXE=%%~fI"
if not exist "%PYTHON_EXE%" (
  echo Expected project virtualenv Python was not found: %PYTHON_EXE%
  echo Refusing to fall back to system Python.
  exit /b 1
)
"%PYTHON_EXE%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo Missing required dependencies for Dashboard API in %PYTHON_EXE%.
  echo Required imports: fastapi, uvicorn.
  exit /b 1
)
if exist "%PIDFILE%" (
  set /p EXISTING_PID=<"%PIDFILE%"
  set "PID_OWNS_PORT="
  for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8550 .*LISTENING"') do if "%%P"=="!EXISTING_PID!" set "PID_OWNS_PORT=1"
  if defined PID_OWNS_PORT (
    powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8550/api/radar/top?limit=1' -TimeoutSec 3 | Out-Null; exit 0 } catch {}; exit 1"
    if not errorlevel 1 (
      echo Dashboard API is already running with PID !EXISTING_PID!.
      echo Health: http://127.0.0.1:8550/api/radar/top?limit=1
      exit /b 0
    )
    echo Dashboard API PID !EXISTING_PID! owns port 8550 but readiness failed.
    echo Refusing to start a duplicate process. Check %ERRLOG%
    exit /b 1
  )
  del /q "%PIDFILE%" >nul 2>&1
)
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8550 .*LISTENING"') do set "PORT_PID=%%P"
if defined PORT_PID (
  powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8550/api/radar/top?limit=1' -TimeoutSec 3 | Out-Null; exit 0 } catch {}; exit 1"
  if not errorlevel 1 (
    >"%PIDFILE%" echo !PORT_PID!
    echo Dashboard API is already running with PID !PORT_PID!.
    echo Health: http://127.0.0.1:8550/api/radar/top?limit=1
    exit /b 0
  )
  echo Port 8550 is already in use by PID !PORT_PID!, but Dashboard API readiness failed.
  echo Refusing to start a duplicate process. Check %ERRLOG%
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%scripts\start_detached_python_service.ps1' -PythonExe '%PYTHON_EXE%' -WorkingDirectory '%ROOT%' -PidFile '%PIDFILE%' -StdoutLog '%OUTLOG%' -StderrLog '%ERRLOG%' -Arguments @('-m','uvicorn','dashboard_react.server:app','--host','127.0.0.1','--port','8550')"
if errorlevel 1 (
  echo Failed to launch Dashboard API process. Check %ERRLOG%
  exit /b 1
)
for /l %%I in (1,1,40) do (
  if exist "%PIDFILE%" (
    powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8550/api/radar/top?limit=1' -TimeoutSec 2 | Out-Null; exit 0 } catch {}; exit 1"
    if not errorlevel 1 goto :started
  )
  powershell -NoProfile -Command "Start-Sleep -Milliseconds 250"
)
echo Failed to start ready Dashboard API. Check %ERRLOG%
exit /b 1

:started
set /p STARTED_PID=<"%PIDFILE%"
echo Dashboard API started with PID %STARTED_PID%.
echo URL:    http://127.0.0.1:8550
echo Health: http://127.0.0.1:8550/api/radar/top?limit=1
echo Logs:   %OUTLOG% and %ERRLOG%
