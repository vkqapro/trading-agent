from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from dashboard_react.server import _parse_stock_symbol_upload, _stock_symbols_missing_chart_history


class BulkSymbolUploadTests(TestCase):
    def test_parse_stock_symbol_upload_skips_headers_and_dedupes(self) -> None:
        parsed, invalid = _parse_stock_symbol_upload(
            {"csv_text": "symbol,AAPL,msft\nTSLA,AAPL,BAD-DASH\n"}
        )

        self.assertEqual(parsed, ["AAPL", "MSFT", "TSLA"])
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["value"], "BAD-DASH")

    def test_missing_chart_history_selects_existing_symbols_that_need_fetch(self) -> None:
        def fake_load_bars(symbol: str, timeframe: str) -> pd.DataFrame:
            if symbol == "READY":
                rows = {"daily": 60, "weekly": 12, "intraday_4h": 20}[timeframe]
            else:
                rows = 0
            return pd.DataFrame({"date": range(rows), "close": range(rows)})

        with patch("dashboard_react.server.load_bars", side_effect=fake_load_bars):
            self.assertEqual(_stock_symbols_missing_chart_history(["READY", "AMD"]), ["AMD"])
