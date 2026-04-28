"""News-driven trade blocking logic."""

from __future__ import annotations

from typing import Dict, Iterable, List

from src.config import SETTINGS
from src.data.news import NewsService


def _matching_headlines(headlines: Iterable[str], keywords: Iterable[str]) -> List[str]:
    matches: List[str] = []
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for headline in headlines:
        normalized = headline.lower()
        if any(keyword in normalized for keyword in lowered_keywords):
            matches.append(headline)
    return matches


class NewsRiskFilter:
    """Evaluate symbol-specific and macro news risk before trading."""

    def __init__(self, news_service: NewsService) -> None:
        self.news_service = news_service

    def get_symbol_risk_context(self, symbol: str) -> Dict[str, object]:
        news_items = self.news_service.fetch_news_by_symbol(symbol)
        headlines = [str(item.get("headline", "")) for item in news_items]
        matches = _matching_headlines(headlines, SETTINGS.news.high_risk_keywords)
        provider_hits = sorted(
            {
                str(item.get("provider_code") or item.get("source") or "")
                for item in news_items
                if item.get("provider_code") or item.get("source")
            }
        )
        source_types = sorted({str(item.get("source_type", "external")) for item in news_items})
        return {
            "symbol": symbol,
            "blocked": bool(matches),
            "headlines": headlines,
            "matched_headlines": matches,
            "provider_hits": provider_hits,
            "source_types": source_types,
        }

    def has_high_risk_news(self, symbol: str) -> bool:
        return bool(self.get_symbol_risk_context(symbol)["blocked"])

    def get_macro_risk_context(self) -> Dict[str, object]:
        macro_items = self.news_service.fetch_macro_events()
        headlines = [str(item.get("headline", "")) for item in macro_items]
        matches = _matching_headlines(headlines, SETTINGS.news.macro_risk_keywords)
        critical_matches = _matching_headlines(headlines, SETTINGS.news.critical_macro_keywords)
        provider_hits = sorted(
            {
                str(item.get("provider_code") or item.get("source") or "")
                for item in macro_items
                if item.get("provider_code") or item.get("source")
            }
        )
        source_types = sorted({str(item.get("source_type", "external")) for item in macro_items})
        return {
            "blocked": bool(critical_matches) or len(matches) >= SETTINGS.news.macro_min_match_count,
            "headlines": headlines,
            "matched_headlines": matches,
            "critical_matches": critical_matches,
            "provider_hits": provider_hits,
            "source_types": source_types,
        }

    def is_macro_risk(self) -> bool:
        return bool(self.get_macro_risk_context()["blocked"])
