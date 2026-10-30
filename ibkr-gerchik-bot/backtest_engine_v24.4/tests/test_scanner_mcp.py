from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
for path in (str(ENGINE_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from scanner_mcp import server
from scanner_mcp.schemas import ScannerMCPError
from scanner_mcp.service import StrategyScannerService

EXPECTED_TOOLS = {
    "list_strategies",
    "list_available_symbols",
    "run_universe_scan",
    "get_scan_summary",
    "get_strategy_candidates",
    "get_symbol_scan",
    "list_scan_runs",
    "get_candidate_proposals",
    "preview_live_setups",
    "get_live_candidate_proposals",
}


@pytest.fixture(scope="module")
def controlled_run():
    # The harness sandbox may deny the host temp root; keep this deterministic
    # fixture within the workspace instead.
    root = ENGINE_ROOT / "tests" / ".scanner-mcp-runs"
    root.mkdir(parents=True, exist_ok=True)
    service = StrategyScannerService(root)
    result = service.run_universe_scan(
        strategies=["lp1", "lp2", "prb1", "prb2"],
        symbols=["AAPL", "MAAS", "V"],
        direction="LONG",
        # Immutable historical regression window: the expected MAAS/V
        # explainability snapshot is the 2026-09-28 close, not today's
        # mutable persisted latest bar.
        as_of="2026-09-28",
    )
    return service, result, root / result["run_id"]


def test_tool_discovery_is_exact():
    assert {tool.name for tool in server.mcp._tool_manager.list_tools()} == EXPECTED_TOOLS


def test_list_strategies_uses_exact_production_registry():
    result = StrategyScannerService().list_strategies()
    assert [item["strategy_id"] for item in result["strategies"]] == ["lp1", "lp2", "prb1", "prb2"]
    assert all(item["production_enabled"] for item in result["strategies"])


def test_symbol_universe_is_delegated_not_hard_coded(monkeypatch):
    import scanner_mcp.service as service_module

    monkeypatch.setattr(service_module, "configured_symbols", lambda: ["ONE", "TWO"])
    monkeypatch.setattr(service_module, "available_daily_symbols", lambda symbols: [item for item in symbols if item == "TWO"])
    monkeypatch.setattr(service_module, "index_snapshot", lambda: {"TWO": {"daily": {"rows": 42, "last_bar": "2026-01-01", "updated_at": "now"}}})
    result = StrategyScannerService().list_available_symbols()
    assert result["requested_count"] == 2
    assert result["available_symbols"] == ["TWO"]
    assert result["missing_symbols"] == [{"symbol": "ONE", "reason": "NO_PERSISTED_DAILY_DATA"}]


def test_controlled_scan_creates_immutable_artifacts(controlled_run):
    _, result, run_dir = controlled_run
    assert result["status"] == "COMPLETED"
    assert result["symbols_scanned"] == 3
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "candidates.json").is_file()
    assert (run_dir / "symbols" / "AAPL.json").is_file()
    assert (run_dir / "symbols" / "MAAS.json").is_file()
    assert (run_dir / "symbols" / "V.json").is_file()


def test_summary_matches_saved_artifact(controlled_run):
    service, result, run_dir = controlled_run
    expected = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert service.get_scan_summary(result["run_id"]) == expected


def test_candidates_match_saved_artifact(controlled_run):
    service, result, run_dir = controlled_run
    raw = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    response = service.get_strategy_candidates(result["run_id"], limit=200)
    expected = [item for item in raw if item["matched"] is True and item["status"] == "ENTRY_SIGNAL"]
    assert response["total"] == len(expected)
    for compact, full in zip(response["candidates"], expected):
        assert compact["symbol"] == full["symbol"]
        assert compact["strategy_id"] == full["strategy_id"]
        assert compact["entry"] == full["trade_plan"]["entry"]
        assert compact["stop"] == full["trade_plan"]["stop"]
        assert compact["target"] == full["trade_plan"]["target"]
        assert compact["score"] == full["quality"]["score"]
        assert compact["pattern_definition"] == full["pattern_definition"]
        assert compact["pattern_bars"] == full["pattern_bars"]
        assert compact["trigger_evidence"] == full["trigger_evidence"]
        assert compact["chart_annotations"] == full["chart_annotations"]
        assert compact["canonical_identity_status"] == "missing"
        assert compact["setup_id"] is None
        assert compact["plan_hash"] is None


def test_maas_lp2_and_v_lp1_have_exact_explainability(controlled_run):
    service, result, _ = controlled_run
    maas = service.get_symbol_scan(result["run_id"], "MAAS")["strategies"]["lp2"]
    visa = service.get_symbol_scan(result["run_id"], "V")["strategies"]["lp1"]

    assert maas["status"] == "ENTRY_SIGNAL"
    assert maas["pattern_definition"]["pattern_family"] == "level_reclaim_two_bar"
    assert [(key, bar["date"], bar["role"]) for key, bar in maas["pattern_bars"].items()] == [
        ("bar_1", "2026-09-24", "penetration_bar"),
        ("bar_2", "2026-09-25", "reclaim_bar"),
        ("signal_bar", "2026-09-28", "trigger_bar"),
    ]
    assert maas["trigger_evidence"]["penetrated_below_level"] is True
    assert maas["trigger_evidence"]["reclaim_close_above_level"] is True
    assert maas["trigger_evidence"]["entry_triggered"] is True
    assert maas["level"]["price"] == pytest.approx(14.85)
    assert maas["market"]["atr"] == pytest.approx(1.7500656258615526)
    assert maas["trade_plan"]["entry"] == pytest.approx(15.725032812930776)
    assert maas["trade_plan"]["stop"] == pytest.approx(11.582483593534612)
    assert maas["trade_plan"]["target"] == pytest.approx(24.010131251723102)
    assert maas["quality"]["score"] == pytest.approx(75.99810695028297)

    assert visa["status"] == "ENTRY_SIGNAL"
    assert visa["pattern_definition"]["pattern_family"] == "level_reclaim"
    assert [(key, bar["date"], bar["role"]) for key, bar in visa["pattern_bars"].items()] == [
        ("bar_1", "2026-09-25", "penetration_reclaim_bar"),
        ("signal_bar", "2026-09-28", "trigger_bar"),
    ]
    assert visa["trigger_evidence"]["penetrated_below_level"] is True
    assert visa["trigger_evidence"]["closed_back_above_level"] is True
    assert visa["trigger_evidence"]["entry_triggered"] is True
    assert visa["level"]["price"] == pytest.approx(365.52)
    assert visa["market"]["atr"] == pytest.approx(5.952242841824288)
    assert visa["trade_plan"]["entry"] == pytest.approx(368.4961214209121)
    assert visa["trade_plan"]["stop"] == pytest.approx(362.30193928954395)
    assert visa["trade_plan"]["target"] == pytest.approx(380.88448568364845)
    assert visa["quality"]["score"] == pytest.approx(87.83998404000984)

    for detail in (maas, visa):
        prices = {item["label"]: item["price"] for item in detail["chart_annotations"] if "price" in item}
        assert prices == {
            "Level": detail["level"]["price"],
            "Entry": detail["trade_plan"]["entry"],
            "Stop": detail["trade_plan"]["stop"],
            "Target": detail["trade_plan"]["target"],
        }
        assert detail["rejection_evidence"] == []


def test_nonsignal_explainability_is_safe_and_noninvented(controlled_run):
    service, result, _ = controlled_run
    detail = service.get_symbol_scan(result["run_id"], "AAPL")["strategies"]["lp2"]
    assert detail["status"] == "NO_SIGNAL"
    assert detail["pattern_definition"]["strategy"] == "LP2"
    assert detail["pattern_bars"] == {}
    assert detail["trigger_evidence"] == {}
    assert detail["rejection_evidence"] == []
    assert detail["chart_annotations"] == []


def test_symbol_detail_matches_saved_artifact(controlled_run):
    service, result, run_dir = controlled_run
    expected = json.loads((run_dir / "symbols" / "AAPL.json").read_text(encoding="utf-8"))
    assert service.get_symbol_scan(result["run_id"], "aapl") == expected


def test_bad_run_id_is_structured_error(monkeypatch):
    monkeypatch.setattr(server, "service", StrategyScannerService(ENGINE_ROOT / "tests" / "_artifact_runs"))
    result = server.get_scan_summary("scan_20000101T000000Z_abcdef")
    assert result["error"]["code"] == "RUN_NOT_FOUND"


def test_bad_strategy_is_structured_error():
    with pytest.raises(ScannerMCPError) as caught:
        StrategyScannerService().run_universe_scan(strategies=["gaussian"], symbols=["AAPL"])
    assert caught.value.code == "INVALID_STRATEGY"


@pytest.mark.parametrize("run_id", ["../../", "..\\..\\", "scan_x", "scan_20260930T000000Z_abcdef/../../"])
def test_run_id_path_traversal_is_rejected(run_id):
    with pytest.raises(ScannerMCPError) as caught:
        StrategyScannerService().get_scan_summary(run_id)
    assert caught.value.code == "RUN_NOT_FOUND"


def test_symbol_path_traversal_is_rejected(controlled_run):
    service, result, _ = controlled_run
    with pytest.raises(ScannerMCPError) as caught:
        service.get_symbol_scan(result["run_id"], "../../")
    assert caught.value.code == "INVALID_SYMBOL"


def test_scan_run_listing_is_compact_and_newest_first(controlled_run):
    service, result, _ = controlled_run
    listing = service.list_scan_runs()
    assert listing["runs"][0]["run_id"] == result["run_id"]
    assert "symbols" not in listing["runs"][0]
    assert "candidate_count" in listing["runs"][0]


def test_scanner_mcp_has_no_broker_or_autonomous_execution_imports():
    package = ENGINE_ROOT / "scanner_mcp"
    forbidden_text = (
        "placeOrder", "cancelOrder", "place_market_order", "execute_trade",
        "paper_trading", "src.execution", "src.decision", "ib_insync", "IBKR",
    )
    imported = set()
    source = ""
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        source += "\n" + text
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(token in source for token in forbidden_text)
    assert not any(name.startswith(("src.execution", "src.decision", "src.paper_trading")) for name in imported)
