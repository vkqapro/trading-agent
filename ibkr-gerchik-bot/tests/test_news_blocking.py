"""Tests for news blocking."""

import unittest

from src.data.news_filter import NewsRiskFilter


class StubNewsService:
    def fetch_news_by_symbol(self, symbol: str, limit: int = 10):  # noqa: ARG002
        return [{"headline": "Apple faces lawsuit before earnings"}]

    def fetch_macro_events(self, limit: int = 10):  # noqa: ARG002
        return [{"headline": "FOMC decision due later today"}]


class NewsBlockingTests(unittest.TestCase):
    def test_symbol_and_macro_block(self) -> None:
        news_filter = NewsRiskFilter(StubNewsService())
        self.assertTrue(news_filter.has_high_risk_news("AAPL"))
        self.assertTrue(news_filter.is_macro_risk())

