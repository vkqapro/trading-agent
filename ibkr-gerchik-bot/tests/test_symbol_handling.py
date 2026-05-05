from __future__ import annotations

import unittest

from src.config import SETTINGS, fx_pair_components
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


if __name__ == "__main__":
    unittest.main()
