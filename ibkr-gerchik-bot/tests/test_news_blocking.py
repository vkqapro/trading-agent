"""Tests for news blocking."""

import unittest

from src.data.news_filter import NewsRiskFilter


class StubNewsService:
    def fetch_news_by_symbol(self, symbol: str, limit: int = 10):  # noqa: ARG002
        return [
            {"headline": "Apple faces lawsuit before earnings", "provider_code": "BRFUPDN", "source_type": "ibkr"},
            {"headline": "Apple faces lawsuit before earnings", "source": "Event Registry", "source_type": "external"},
        ]

    def fetch_macro_events(self, limit: int = 10):  # noqa: ARG002
        return [
            {"headline": "FOMC decision due later today", "provider_code": "BRFG", "source_type": "ibkr"},
            {"headline": "War concerns rise into the close", "source": "Event Registry", "source_type": "external"},
        ]


class NewsBlockingTests(unittest.TestCase):
    def test_symbol_and_macro_block(self) -> None:
        news_filter = NewsRiskFilter(StubNewsService())
        self.assertTrue(news_filter.has_high_risk_news("AAPL"))
        self.assertTrue(news_filter.is_macro_risk())

    def test_context_includes_provider_metadata(self) -> None:
        news_filter = NewsRiskFilter(StubNewsService())
        symbol_context = news_filter.get_symbol_risk_context("AAPL")
        macro_context = news_filter.get_macro_risk_context()

        self.assertEqual(symbol_context["risk_level"], "HIGH")
        self.assertEqual(macro_context["risk_level"], "HIGH")
        self.assertIn("BRFUPDN", symbol_context["provider_hits"])
        self.assertIn("ibkr", symbol_context["source_types"])
        self.assertIn("external", symbol_context["source_types"])
        self.assertIn("BRFG", macro_context["provider_hits"])
