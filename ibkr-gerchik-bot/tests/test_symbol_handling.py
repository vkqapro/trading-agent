from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import SETTINGS, Settings, fx_pair_components
from src.data.news import NewsService
from src.main import _build_research_symbols


class SymbolHandlingTests(unittest.TestCase):
    def test_fx_pair_components_supports_dot_and_compact_formats(self) -> None:
        self.assertEqual(fx_pair_components("EUR.USD"), ("EUR", "USD"))
        self.assertEqual(fx_pair_components("eurusd"), ("EUR", "USD"))
        self.assertIsNone(fx_pair_components("AAPL"))

    def test_settings_classifies_forex_symbols_as_cash(self) -> None:
        self.assertEqual(SETTINGS.symbol_security_type("EUR.USD"), "CASH")
        self.assertEqual(SETTINGS.symbol_security_type("AAPL"), "STK")

    def test_build_research_symbols_maps_cash_positions_to_account_currency_pair(self) -> None:
        symbols = _build_research_symbols(
            [
                {"symbol": "EUR", "sec_type": "CASH"},
                {"symbol": "AAPL", "sec_type": "STK"},
            ]
        )
        self.assertIn("EUR.USD", symbols)
        self.assertIn("AAPL", symbols)

    def test_news_service_skips_fx_symbol_news(self) -> None:
        news_service = NewsService()
        self.assertEqual(news_service.fetch_news_by_symbol("EUR.USD"), [])

    def test_settings_can_load_stock_symbols_from_csv_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "symbols.csv"
            csv_path.write_text("symbol\nAAPL\nMSFT\nAAPL\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "STOCK_SYMBOLS_FILE": str(csv_path),
                    "STOCK_SYMBOLS": "SHOULD,NOT,WIN",
                },
                clear=False,
            ):
                settings = Settings()

            self.assertEqual(settings.stock_symbols, ["AAPL", "MSFT"])

    def test_settings_falls_back_to_inline_symbols_when_csv_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STOCK_SYMBOLS_FILE": "config/does-not-exist.csv",
                "STOCK_SYMBOLS": "AAPL, MSFT",
            },
            clear=False,
        ):
            settings = Settings()

        self.assertEqual(settings.stock_symbols, ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
