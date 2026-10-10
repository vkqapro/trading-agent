from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from trading_research import cli
from trading_research.artifacts import write_review_artifact
from trading_research.config import DEFAULT_RESEARCH_ROOT, configured_research_root
from trading_research.safety import assert_research_write_path, research_root, write_json_artifact
from trading_research.strategy_library import LIBRARY_ROOT, StrategyLibrary


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STATIC_SCANNER_IDS = {"LP1", "LP2", "PRB1", "PRB2"}
DEFAULT_ROUTER_IDS = {"REBOUND", "CONFIRMED_BREAKOUT", "FALSE_BREAKOUT_ONE_BAR"}
RUNTIME_SELECTABLE_ROUTER_IDS = {
    "FALSE_BREAKOUT_TWO_BAR",
    "FALSE_BREAKOUT_COMPLEX",
    "FALSE_BREAKOUT_CONTINUATION",
}
ANALYSIS_ONLY_IDS = {"BMSB", "GAUSSIAN"}


def _registry() -> dict:
    return json.loads((LIBRARY_ROOT / "registry.json").read_text(encoding="utf-8"))["strategies"]


def _path(reference: str) -> Path:
    return REPOSITORY_ROOT / reference.split(":", 1)[0]


def test_library_covers_every_verified_runtime_strategy_surface() -> None:
    registry = _registry()
    assert STATIC_SCANNER_IDS <= set(registry)
    assert DEFAULT_ROUTER_IDS <= set(registry)
    assert RUNTIME_SELECTABLE_ROUTER_IDS <= set(registry)
    assert {"INEFFICIENCY_RECLAIM"} <= set(registry)
    assert ANALYSIS_ONLY_IDS <= set(registry)

    assert all(registry[strategy_id]["enabled"] is True for strategy_id in STATIC_SCANNER_IDS | DEFAULT_ROUTER_IDS)
    assert all(registry[strategy_id]["enabled"] is False for strategy_id in RUNTIME_SELECTABLE_ROUTER_IDS | {"INEFFICIENCY_RECLAIM"})
    assert all(registry[strategy_id]["execution_capability"] == "ANALYSIS_ONLY" for strategy_id in ANALYSIS_ONLY_IDS)


def test_every_implementation_confirmed_entry_has_local_markdown_and_authority() -> None:
    registry = _registry()
    library = StrategyLibrary()
    for strategy_id, entry in registry.items():
        if not str(entry["definition_status"]).startswith("implementation_confirmed"):
            continue
        document = LIBRARY_ROOT / entry["documentation"]
        assert document.is_file(), strategy_id
        text = library.get(strategy_id, include_documentation=True)["documentation_markdown"]
        assert "## Code and test authority" in text
        assert entry["authoritative_code"], strategy_id
        assert entry["authoritative_tests"], strategy_id
        assert all(_path(reference).is_file() for reference in entry["authoritative_code"]), strategy_id
        assert all(_path(reference).is_file() for reference in entry["authoritative_tests"]), strategy_id


def test_lp_prb_library_entries_match_the_static_production_scanner_allowlist() -> None:
    source = (REPOSITORY_ROOT / "backtest_engine_v24.4/scanner/registry.py").read_text(encoding="utf-8")
    for strategy_id in STATIC_SCANNER_IDS:
        assert f'("{strategy_id.lower()}", "{strategy_id}")' in source
    registry = _registry()
    assert {strategy_id for strategy_id, entry in registry.items() if entry.get("version") == "market-screener-lp-prb-strategy-2"} == STATIC_SCANNER_IDS


def test_runtime_research_root_is_fixed_and_cli_has_no_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADING_RESEARCH_ROOT", str(tmp_path / "redirect"))
    assert configured_research_root() == DEFAULT_RESEARCH_ROOT
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["trade-review", "--research-root", str(tmp_path)])
    assert "root" not in inspect.signature(write_review_artifact).parameters
    assert "root" not in inspect.signature(research_root).parameters
    assert "root" not in inspect.signature(assert_research_write_path).parameters
    assert "root" not in inspect.signature(write_json_artifact).parameters
