from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _launcher_text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _helper_invocation(name: str) -> str:
    lines = [
        line.strip()
        for line in _launcher_text(name).splitlines()
        if "scripts\\start_detached_python_service.ps1" in line
    ]
    assert len(lines) == 1
    return lines[0]


def _sq(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _write_binding_probe(path: Path) -> None:
    path.write_text(
        """
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory,
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments,
    [Parameter(Mandatory = $true)]
    [string]$PidFile,
    [Parameter(Mandatory = $true)]
    [string]$StdoutLog,
    [Parameter(Mandatory = $true)]
    [string]$StderrLog,
    [string[]]$Environment = @()
)

[pscustomobject]@{
    PythonExe = $PythonExe
    WorkingDirectory = $WorkingDirectory
    Arguments = $Arguments
    PidFile = $PidFile
    StdoutLog = $StdoutLog
    StderrLog = $StderrLog
    Environment = $Environment
} | ConvertTo-Json -Compress
""".strip(),
        encoding="utf-8",
    )


def _run_powershell_binding(command: str) -> dict[str, object]:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("powershell is required for launcher binding tests")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_dashboard_launcher_uses_safe_powershell_array_binding() -> None:
    invocation = _helper_invocation("run_dashboard_api.cmd")

    assert "-Command \"& '%ROOT%scripts\\start_detached_python_service.ps1'" in invocation
    assert "-File " not in invocation
    assert '-Arguments "-m","uvicorn"' not in invocation
    assert "-PidFile '%LAUNCHER_PIDFILE%'" in invocation
    assert "-StdoutLog '%OUTLOG%'" in invocation
    assert "-StderrLog '%ERRLOG%'" in invocation
    assert "-Arguments @('-m','uvicorn','dashboard_react.server:app','--host','127.0.0.1','--port','8550')" in invocation


def test_trading_bot_mcp_launcher_uses_safe_powershell_array_binding() -> None:
    invocation = _helper_invocation("run_trading_bot_mcp.cmd")

    assert "-Command \"& '%ROOT%scripts\\start_detached_python_service.ps1'" in invocation
    assert "-File " not in invocation
    assert '-Arguments "-m","src.mcp.trading_bot_server"' not in invocation
    assert "-PidFile '%PIDFILE%'" in invocation
    assert "-StdoutLog '%OUTLOG%'" in invocation
    assert "-StderrLog '%ERRLOG%'" in invocation
    assert "-Environment @('TRADING_BOT_MCP_HOST=127.0.0.1','TRADING_BOT_MCP_PORT=8765')" in invocation
    assert "-Arguments @('-m','src.mcp.trading_bot_server')" in invocation


def test_repaired_contract_binds_paths_with_spaces_and_dashboard_arguments(tmp_path: Path) -> None:
    probe = tmp_path / "repo root with spaces" / "scripts" / "probe.ps1"
    probe.parent.mkdir(parents=True, exist_ok=True)
    _write_binding_probe(probe)
    runtime = tmp_path / "repo root with spaces" / "runtime logs"

    payload = _run_powershell_binding(
        " ".join(
            [
                f"& {_sq(str(probe))}",
                f"-PythonExe {_sq(sys.executable)}",
                f"-WorkingDirectory {_sq(str(probe.parents[1]))}",
                f"-PidFile {_sq(str(runtime / 'dashboard api.pid'))}",
                f"-StdoutLog {_sq(str(runtime / 'dashboard 8550.stdout.log'))}",
                f"-StderrLog {_sq(str(runtime / 'dashboard 8550.stderr.log'))}",
                "-Arguments @('-m','uvicorn','dashboard_react.server:app','--host','127.0.0.1','--port','8550')",
            ]
        )
    )

    assert payload["WorkingDirectory"].endswith("repo root with spaces")
    assert payload["PidFile"].endswith("runtime logs\\dashboard api.pid")
    assert payload["StdoutLog"].endswith("runtime logs\\dashboard 8550.stdout.log")
    assert payload["StderrLog"].endswith("runtime logs\\dashboard 8550.stderr.log")
    assert payload["Arguments"] == ["-m", "uvicorn", "dashboard_react.server:app", "--host", "127.0.0.1", "--port", "8550"]


def test_repaired_contract_binds_trading_bot_argument_and_environment_arrays(tmp_path: Path) -> None:
    probe = tmp_path / "repo root with spaces" / "scripts" / "probe.ps1"
    probe.parent.mkdir(parents=True, exist_ok=True)
    _write_binding_probe(probe)
    runtime = tmp_path / "repo root with spaces" / "runtime logs"

    payload = _run_powershell_binding(
        " ".join(
            [
                f"& {_sq(str(probe))}",
                f"-PythonExe {_sq(sys.executable)}",
                f"-WorkingDirectory {_sq(str(probe.parents[1]))}",
                f"-PidFile {_sq(str(runtime / 'trading bot mcp.pid'))}",
                f"-StdoutLog {_sq(str(runtime / 'trading bot mcp.stdout.log'))}",
                f"-StderrLog {_sq(str(runtime / 'trading bot mcp.stderr.log'))}",
                "-Environment @('TRADING_BOT_MCP_HOST=127.0.0.1','TRADING_BOT_MCP_PORT=8765')",
                "-Arguments @('-m','src.mcp.trading_bot_server')",
            ]
        )
    )

    assert payload["Arguments"] == ["-m", "src.mcp.trading_bot_server"]
    assert payload["Environment"] == ["TRADING_BOT_MCP_HOST=127.0.0.1", "TRADING_BOT_MCP_PORT=8765"]


def test_legacy_file_contract_fails_before_safe_binding(tmp_path: Path) -> None:
    powershell = shutil.which("powershell")
    cmd = shutil.which("cmd")
    if powershell is None or cmd is None:
        pytest.skip("cmd.exe and powershell are required for launcher binding tests")
    probe = tmp_path / "probe.ps1"
    probe.parent.mkdir(parents=True, exist_ok=True)
    _write_binding_probe(probe)
    runtime = tmp_path / "runtime"
    legacy = (
        f'powershell -NoProfile -ExecutionPolicy Bypass -File {probe} '
        f'-PythonExe "{sys.executable}" -WorkingDirectory "{tmp_path}" '
        f'-Arguments "-m","uvicorn","dashboard_react.server:app","--host","127.0.0.1","--port","8550" '
        f'-PidFile "{runtime / "dashboard api.pid"}" '
        f'-StdoutLog "{runtime / "dashboard 8550.stdout.log"}" '
        f'-StderrLog "{runtime / "dashboard 8550.stderr.log"}"'
    )

    completed = subprocess.run([cmd, "/d", "/c", legacy], text=True, capture_output=True, timeout=10)

    error = completed.stderr + completed.stdout
    assert completed.returncode != 0
    assert "PositionalParameterNotFound" in error or "missing mandatory parameters" in error


def test_missing_mandatory_parameters_still_fail_closed(tmp_path: Path) -> None:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("powershell is required for launcher binding tests")
    probe = tmp_path / "probe.ps1"
    _write_binding_probe(probe)
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& {_sq(str(probe))} -PythonExe {_sq(sys.executable)} -WorkingDirectory {_sq(str(tmp_path))} -Arguments @('-m','uvicorn')",
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert completed.returncode != 0
    error = completed.stderr + completed.stdout
    assert "missing mandatory parameters" in error
    assert "PidFile" in error
    assert "StdoutLog" in error
    assert "StderrLog" in error


def test_launchers_preserve_duplicate_and_ambiguous_port_guards_before_start() -> None:
    for launcher, port in (("run_dashboard_api.cmd", "8550"), ("run_trading_bot_mcp.cmd", "8765")):
        text = _launcher_text(launcher)
        helper_index = text.index("scripts\\start_detached_python_service.ps1")

        assert text.count("scripts\\start_detached_python_service.ps1") == 1
        assert text.index(f'findstr /R /C:"127.0.0.1:{port} .*LISTENING"') < helper_index
        assert text.index("Refusing to start a duplicate process.") < helper_index
        assert text.index("PID_OWNS_PORT") < helper_index
        assert text.index("if defined PORT_PID") < helper_index


def test_launchers_do_not_contain_trading_side_effects() -> None:
    forbidden = (
        "placeOrder",
        "submit_order",
        "orders_submitted",
        "approvals_created",
        "execution_intents_created",
        "/api/daily-trader/scan",
        "run_market_screener",
        "Enable-ScheduledTask",
        "Start-ScheduledTask",
    )

    combined = _launcher_text("run_dashboard_api.cmd") + "\n" + _launcher_text("run_trading_bot_mcp.cmd")

    for token in forbidden:
        assert token not in combined
