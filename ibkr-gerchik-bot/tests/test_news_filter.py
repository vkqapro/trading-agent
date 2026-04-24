"""Tests for news filtering."""

import unittest

from src.data.news_filter import NewsRiskFilter


class StubNewsService:
    def fetch_news_by_symbol(self, symbol: str, limit: int = 10):  # noqa: ARG002
        if symbol == "AAPL":
            return [{"headline": "Apple faces lawsuit ahead of earnings"}]
        return [{"headline": "Calm product launch update"}]

    def fetch_macro_events(self, limit: int = 10):  # noqa: ARG002
        return [{"headline": "FOMC meeting keeps traders cautious"}]


class WeakMacroNewsService:
    def fetch_news_by_symbol(self, symbol: str, limit: int = 10):  # noqa: ARG002
        return []

    def fetch_macro_events(self, limit: int = 10):  # noqa: ARG002
        return [{"headline": "Geopolitical concerns linger in markets"}]


class NewsFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = NewsRiskFilter(StubNewsService())

    def test_detects_symbol_risk(self) -> None:
        self.assertTrue(self.filter.has_high_risk_news("AAPL"))

    def test_allows_symbol_without_risk_keyword(self) -> None:
        self.assertFalse(self.filter.has_high_risk_news("MSFT"))

    def test_detects_macro_risk(self) -> None:
        self.assertTrue(self.filter.is_macro_risk())

    def test_single_weak_macro_match_does_not_block(self) -> None:
        weak_filter = NewsRiskFilter(WeakMacroNewsService())
        self.assertFalse(weak_filter.is_macro_risk())


if __name__ == "__main__":
    unittest.main()
