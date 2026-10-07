from __future__ import annotations

import shutil
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
for path in (str(ENGINE_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from scanner.adapters.lp_prb import evaluate_symbol
from scanner.contracts import ScanStatus
from scanner.data_adapter import LoadedBars, load_closed_daily_bars
from scanner.registry import PRODUCTION_STRATEGY_IDS, list_strategy_specs
from scanner.runner import run_universe_scan
from src.mcp.trading_data_service import DataError
from strategies import market_screener_lp_prb_strategy_2 as original


def constant_frame(count: int = 30) -> pd.DataFrame:
    index = pd.bdate_range("2026-01-01", periods=count)
    return pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1_000_000.0},
        index=index,
    ).rename_axis("Date")


def aapl_history() -> pd.DataFrame:
    path = REPO_ROOT / "memory" / "bars" / "AAPL__daily.csv"
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date").rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )[["Open", "High", "Low", "Close", "Volume"]].rename_axis("Date")


class ScannerTests(unittest.TestCase):
    def test_registry_contains_exactly_four_production_strategies(self) -> None:
        self.assertEqual(PRODUCTION_STRATEGY_IDS, ("lp1", "lp2", "prb1", "prb2"))
        self.assertEqual(tuple(spec.strategy_id for spec in list_strategy_specs()), PRODUCTION_STRATEGY_IDS)

    def test_closed_bar_guard_rejects_unfinished_bar(self) -> None:
        candles = [
            {"timestamp": "2026-01-01T00:00:00-05:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "closed": True},
            {"timestamp": "2026-01-02T00:00:00-05:00", "open": 1.5, "high": 2, "low": 1, "close": 1.8, "volume": 11, "closed": False},
        ]
        with patch("scanner.data_adapter.get_symbol_history", return_value={"symbol": "TEST", "candles": candles}):
            with self.assertRaises(DataError) as caught:
                load_closed_daily_bars("TEST")
        self.assertEqual(caught.exception.code, "INCOMPLETE_BAR_INCLUDED")

    def _assert_parity(self, strategy_id: str, date: str) -> None:
        frame = aapl_history().loc[:date]
        p = replace(
            original.BASELINE, side="Long Only",
            lp1=strategy_id == "lp1", lp2=strategy_id == "lp2",
            prb1=strategy_id == "prb1", prb2=strategy_id == "prb2",
        )
        old = original.prepare(frame, p).iloc[-1]
        new = evaluate_symbol("AAPL", strategy_id, frame, run_id="parity").to_dict()
        expected_status = "ENTRY_SIGNAL" if bool(old.signal) else ("REJECTED" if bool(old.get("candidate", False)) else "NO_SIGNAL")
        self.assertEqual(new["status"], expected_status)
        self.assertEqual(new["signal"]["pattern"], old.pattern or strategy_id.upper())
        self.assertEqual(new["signal"]["triggered"], bool(old.triggered))
        self.assertEqual(new["evidence"]["side"], int(old.side))
        self.assertEqual(new["rejection_reasons"], [item for item in str(old.reject).split(",") if item])
        self.assertEqual(new["schema_version"], "1.1")
        self.assertEqual(new["pattern_definition"]["strategy"], strategy_id.upper())
        self.assertEqual(new["pattern_definition"]["direction"], "LONG")
        self.assertTrue(new["pattern_definition"]["semantic_steps"])
        if bool(old.get("candidate", False)):
            expected_keys = {
                "lp1": ["bar_1", "signal_bar"],
                "lp2": ["bar_1", "bar_2", "signal_bar"],
                "prb1": ["bar_1", "signal_bar"],
                "prb2": ["bar_1", "bar_2", "signal_bar"],
            }[strategy_id]
            self.assertEqual(list(new["pattern_bars"]), expected_keys)
            self.assertEqual(new["signal"]["signal_bar_index"], len(frame) - 1)
            self.assertEqual(new["pattern_bars"]["signal_bar"]["index"], len(frame) - 1)
            self.assertEqual(new["pattern_bars"]["signal_bar"]["date"], pd.Timestamp(frame.index[-1]).date().isoformat())
            self.assertTrue(new["trigger_evidence"]["pattern_confirmation_valid"])
            self.assertEqual(
                sum(item["type"] == "bar_marker" for item in new["chart_annotations"]),
                len(expected_keys),
            )
            self.assertEqual(
                {item["label"] for item in new["chart_annotations"] if item["type"] != "bar_marker"},
                {"Level", "Entry", "Stop", "Target"},
            )
        for new_value, old_value in (
            (new["level"]["price"], old.level),
            (new["trade_plan"]["entry"], old.planned_entry),
            (new["trade_plan"]["stop"], old.stop),
            (new["trade_plan"]["target"], old.target),
            (new["quality"]["score"], old.get("score")),
        ):
            if pd.isna(old_value):
                self.assertIsNone(new_value)
            else:
                self.assertAlmostEqual(float(new_value), float(old_value), places=12)

    def test_lp1_parity(self) -> None:
        self._assert_parity("lp1", "2024-03-04")

    def test_lp2_parity(self) -> None:
        self._assert_parity("lp2", "2023-10-30")

    def test_prb1_parity(self) -> None:
        self._assert_parity("prb1", "2026-09-02")

    def test_prb2_parity(self) -> None:
        self._assert_parity("prb2", "2026-09-03")

    def test_noncandidate_is_no_signal_not_missing(self) -> None:
        result = evaluate_symbol("FLAT", "lp1", constant_frame(), run_id="test")
        self.assertEqual(result.status, ScanStatus.NO_SIGNAL)
        self.assertFalse(result.matched)
        self.assertEqual(result.pattern_bars, {})
        self.assertEqual(result.trigger_evidence, {})
        self.assertEqual(result.rejection_evidence, ())

    def test_known_candidate_rejection_evidence_is_normalized(self) -> None:
        frame = aapl_history().loc[:"2024-03-04"]
        result = evaluate_symbol(
            "AAPL", "lp1", frame, run_id="test", params={"min_score": 100.0},
        )
        self.assertEqual(result.status, ScanStatus.REJECTED)
        self.assertFalse(result.matched)
        self.assertTrue(result.pattern_bars)
        self.assertIn("score_below_minimum", result.rejection_evidence)
        self.assertFalse(result.trigger_evidence["score_threshold_passed"])

    def test_insufficient_data_is_explicit(self) -> None:
        result = evaluate_symbol("SHORT", "lp1", constant_frame(10), run_id="test")
        self.assertEqual(result.status, ScanStatus.INSUFFICIENT_DATA)
        self.assertFalse(result.matched)
        self.assertEqual(result.pattern_definition["strategy"], "LP1")
        self.assertEqual(result.rejection_evidence, ("insufficient_bars",))

    def test_one_bad_symbol_does_not_abort_scan(self) -> None:
        frame = constant_frame()
        loaded = LoadedBars("GOOD", "1D", frame, frame.index[0].isoformat(), frame.index[-1].isoformat(), True, len(frame), "hash")
        def loader(symbol: str, **_: object) -> LoadedBars:
            if symbol == "BAD":
                raise DataError("NO_CANDLE_DATA", "broken fixture")
            return loaded
        with patch("scanner.runner.available_daily_symbols", return_value=["GOOD", "BAD"]), \
             patch("scanner.runner.load_closed_daily_bars", side_effect=loader), \
             patch("scanner.runner.write_run_artifacts", return_value=Path("mock-run")):
            result = run_universe_scan(["GOOD", "BAD"])
        self.assertEqual(result["summary"]["symbols_scanned"], 1)
        self.assertEqual(result["summary"]["symbols_skipped"], 1)
        self.assertIn("GOOD", result["symbols"])

    def test_two_runs_create_distinct_immutable_directories(self) -> None:
        runs_root = ENGINE_ROOT / "tests" / "_artifact_runs"
        created: list[Path] = []
        try:
            with patch("scanner.runner.available_daily_symbols", return_value=[]):
                first = run_universe_scan(["MISSING"], runs_root=runs_root)
                second = run_universe_scan(["MISSING"], runs_root=runs_root)
            created = [Path(first["run_dir"]), Path(second["run_dir"])]
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertTrue(all(path.is_dir() for path in created))
        finally:
            for path in created:
                shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
