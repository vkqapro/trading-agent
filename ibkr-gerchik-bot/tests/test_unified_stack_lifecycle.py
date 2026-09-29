from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import dashboard_react.server as dashboard_server


ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


def test_start_launcher_contains_complete_stack_in_required_order():
    text = _text("run_react_dashboard.cmd")
    positions = [
        text.index("run_market_data_collector.cmd"),
        text.index("run_execute_worker.cmd"),
        text.index("run_crypto_worker.cmd"),
        text.index("run_autonomous_stock_worker.cmd"),
        text.index("uvicorn dashboard_react.server:app"),
    ]
    assert positions == sorted(positions)
    assert "autonomous stock worker" in text.lower()
    assert "report_autonomous_stock_worker_start.py" in text


def test_stop_script_has_exact_worker_signal_and_required_shutdown_order():
    text = _text("run_dashboard_stop_services.ps1")
    assert "autonomous_stock_worker.stop" in text
    assert "src.jobs.autonomous_stock_worker" in text
    assert "ngrok" in text
    assert text.index("Signal-AutonomousWorker") < text.index("--job execute_requests")
    assert text.index("--job execute_requests") < text.index("--job market_data")
    assert text.index("--job market_data") < text.index("src.crypto.main --job worker")
    assert text.index("src.crypto.main --job worker") < text.index("dashboard API port 8550")
    assert "Stop-Process -Id" in text


def test_hard_reset_reuses_complete_stack_and_documents_protection():
    hard_reset = _text("run_dashboard_hard_reset.cmd")
    dashboard = _text("dashboard_react/index.html")
    assert "run_react_dashboard.cmd" in hard_reset
    assert "autonomous stock worker" in hard_reset.lower()
    assert "protective brackets" in dashboard.lower()


def test_logout_uses_fixed_stop_script_without_arbitrary_command_input():
    server = _text("dashboard_react/server.py")
    dashboard = _text("dashboard_react/index.html")
    assert '@app.post("/api/system/logout")' in server
    assert 'ROOT / "stop_react_dashboard.cmd"' in server
    assert '["cmd", "/c", str(stop_script), "--no-pause"]' in server
    assert "request" not in server[server.index('@app.post("/api/system/logout")'):server.index('@app.post("/api/system/fetch-ibkr-candles")')]
    assert "`${API}/system/logout`" in dashboard
    assert "window.close()" in dashboard
    assert "about:blank" in dashboard
    assert dashboard.index("Settings") < dashboard.index("Logout")


def test_logout_endpoint_only_launches_the_fixed_stop_script():
    expected_script = dashboard_server.ROOT / "stop_react_dashboard.cmd"
    with patch.object(dashboard_server.subprocess, "Popen") as popen:
        payload = dashboard_server.api_system_logout()

    assert payload["ok"] is True
    args, kwargs = popen.call_args
    assert args[0] == ["cmd", "/c", str(expected_script), "--no-pause"]
    assert kwargs["cwd"] == str(dashboard_server.ROOT)
    assert kwargs["stdin"] is dashboard_server.subprocess.DEVNULL
    assert kwargs["stdout"] is dashboard_server.subprocess.DEVNULL
    assert kwargs["stderr"] is dashboard_server.subprocess.DEVNULL


def test_dashboard_service_contract_exposes_worker_fields_without_account_id():
    server = _text("dashboard_react/server.py")
    for field in (
        "autonomous_stock_worker",
        "current_session",
        "llm_mode",
        "provider_status",
        "broker_status",
        "account_status",
        "allowlist_status",
        "broker_positions_readable",
        "broker_open_orders_readable",
        "broker_executions_readable",
        "autonomous_entry_enabled",
        "blocked_reason",
        "last_preflight_at",
    ):
        assert field in server
    assert "account_id" not in server[server.index("def _autonomous_stock_worker_status"):server.index("def api_services")]


def test_startup_report_is_read_only_and_requires_heartbeat_lock_ownership():
    report = _text("scripts/report_autonomous_stock_worker_start.py")
    assert "autonomous_stock_worker.json" in report
    assert "autonomous_stock_worker.lock" in report
    assert "os.kill" in report
    assert "IBKRClient" not in report
