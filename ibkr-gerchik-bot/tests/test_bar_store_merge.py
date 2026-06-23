from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.data import bar_store


class BarStoreMergeTests(unittest.TestCase):
    def test_save_bars_merges_and_replaces_duplicate_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(bar_store, "BARS_DIR", root),
                patch.object(bar_store, "INDEX_PATH", root / "index.json"),
            ):
                first = pd.DataFrame([
                    {"date": "2026-06-22 09:30:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
                    {"date": "2026-06-22 09:35:00-04:00", "open": 10.5, "high": 12, "low": 10, "close": 11.5, "volume": 200},
                ])
                second = pd.DataFrame([
                    {"date": "2026-06-22 09:35:00-04:00", "open": 10.5, "high": 12.5, "low": 10, "close": 12, "volume": 250},
                    {"date": "2026-06-22 09:40:00-04:00", "open": 12, "high": 13, "low": 11.5, "close": 12.5, "volume": 300},
                ])

                bar_store.save_bars("AAPL", "intraday_5m", first)
                bar_store.save_bars("AAPL", "intraday_5m", second)
                stored = bar_store.load_bars("AAPL", "intraday_5m")

        self.assertEqual(len(stored), 3)
        self.assertEqual(list(stored["close"]), [10.5, 12.0, 12.5])
        self.assertEqual(float(stored.iloc[1]["volume"]), 250.0)


if __name__ == "__main__":
    unittest.main()
