from __future__ import annotations

import tempfile
import threading
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

    def test_concurrent_stale_writer_cannot_remove_newer_bars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(bar_store, "BARS_DIR", root),
                patch.object(bar_store, "INDEX_PATH", root / "index.json"),
            ):
                seed = pd.DataFrame([
                    {"date": "2026-06-22 09:30:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
                ])
                stale = seed.copy()
                newer = pd.DataFrame([
                    {"date": "2026-06-22 09:30:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
                    {"date": "2026-06-22 15:55:00-04:00", "open": 12, "high": 13, "low": 11, "close": 12.5, "volume": 900},
                ])
                bar_store.save_bars("AAPL", "intraday_5m", seed)

                start = threading.Barrier(3)

                def write(frame: pd.DataFrame) -> None:
                    start.wait()
                    bar_store.save_bars("AAPL", "intraday_5m", frame)

                stale_writer = threading.Thread(target=write, args=(stale,))
                fresh_writer = threading.Thread(target=write, args=(newer,))
                stale_writer.start()
                fresh_writer.start()
                start.wait()
                stale_writer.join()
                fresh_writer.join()
                stored = bar_store.load_bars("AAPL", "intraday_5m")

        self.assertEqual(len(stored), 2)
        self.assertEqual(str(stored.iloc[-1]["date"]), "2026-06-22 15:55:00-04:00")

    def test_merge_keeps_ibkr_named_timezone_rows_after_csv_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(bar_store, "BARS_DIR", root),
                patch.object(bar_store, "INDEX_PATH", root / "index.json"),
            ):
                stored_frame = pd.DataFrame([
                    {"date": "2026-06-22 15:55:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
                ])
                ibkr_frame = pd.DataFrame([
                    {
                        "date": pd.Timestamp("2026-06-23 09:30:00", tz="US/Eastern"),
                        "open": 11,
                        "high": 12,
                        "low": 10,
                        "close": 11.5,
                        "volume": 200,
                    },
                ])

                bar_store.save_bars("AAPL", "intraday_5m", stored_frame)
                bar_store.save_bars("AAPL", "intraday_5m", ibkr_frame)
                stored = bar_store.load_bars("AAPL", "intraday_5m")

        self.assertEqual(len(stored), 2)
        self.assertEqual(str(stored.iloc[-1]["date"]), "2026-06-23 09:30:00-04:00")


if __name__ == "__main__":
    unittest.main()
