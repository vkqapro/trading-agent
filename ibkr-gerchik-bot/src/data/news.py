"""News API access layer."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence

import requests

from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS


SYMBOL_QUERY_MAP: Dict[str, Dict[str, object]] = {
    "AAPL": {
        "company_name": "Apple Inc.",
        "concept_uri": "http://en.wikipedia.org/wiki/Apple_Inc.",
        "keywords": ["Apple", "Apple Inc.", "iPhone", "Mac"],
    },
    "MSFT": {
        "company_name": "Microsoft",
        "concept_uri": "http://en.wikipedia.org/wiki/Microsoft",
        "keywords": ["Microsoft", "Azure", "Windows"],
    },
    "NVDA": {
        "company_name": "Nvidia",
        "concept_uri": "http://en.wikipedia.org/wiki/Nvidia",
        "keywords": ["Nvidia", "GPU", "AI chips"],
    },
    "AMD": {
        "company_name": "Advanced Micro Devices",
        "concept_uri": "http://en.wikipedia.org/wiki/Advanced_Micro_Devices",
        "keywords": ["AMD", "Ryzen", "EPYC"],
    },
    "TSLA": {
        "company_name": "Tesla, Inc.",
        "concept_uri": "http://en.wikipedia.org/wiki/Tesla,_Inc.",
        "keywords": ["Tesla", "Elon Musk", "EV"],
    },
    "META": {
        "company_name": "Meta Platforms",
        "concept_uri": "http://en.wikipedia.org/wiki/Meta_Platforms",
        "keywords": ["Meta", "Facebook", "Instagram"],
    },
    "AMZN": {
        "company_name": "Amazon",
        "concept_uri": "http://en.wikipedia.org/wiki/Amazon_(company)",
        "keywords": ["Amazon", "AWS", "Prime"],
    },
    "NFLX": {
        "company_name": "Netflix",
        "concept_uri": "http://en.wikipedia.org/wiki/Netflix",
        "keywords": ["Netflix", "streaming"],
    },
}


class NewsService:
    """Fetch symbol, macro, and earnings context from NewsAPI.ai / Event Registry."""

    def __init__(self, session: Optional[requests.Session] = None, broker: Optional[IBKRClient] = None) -> None:
        self.session = session or requests.Session()
        self.config = SETTINGS.news
        self.broker = broker

    def fetch_news_by_symbol(self, symbol: str, limit: int = 10) -> List[Dict[str, object]]:
        if SETTINGS.symbol_security_type(symbol) != "STK":
            return []
        external_items = self._fetch_external_news_by_symbol(symbol, limit=limit)
        ibkr_items = self.fetch_ibkr_news_by_symbol(symbol, limit=limit)
        return self._merge_news_items(external_items, ibkr_items, limit=max(limit, self.config.ibkr_headline_limit))

    def _fetch_external_news_by_symbol(self, symbol: str, limit: int = 10) -> List[Dict[str, object]]:
        symbol_key = symbol.upper()
        symbol_meta = SYMBOL_QUERY_MAP.get(symbol_key, {})
        keyword_terms = [str(item) for item in symbol_meta.get("keywords", [symbol_key, f"{symbol_key} stock"]) if item]
        params = self._article_params(
            keywords=keyword_terms,
            limit=limit,
            sort_by="rel",
            extra={
                "ignoreSourceGroupUri": "paywall/paywalled_sources",
                "lang": "eng",
                "sourceGroupUri": "business/top100",
            },
        )
        concept_uri = symbol_meta.get("concept_uri")
        if concept_uri:
            params["conceptUri"] = concept_uri

        payload = self._request(self.config.base_url, params)
        return self._parse_articles(payload)

    def fetch_macro_events(self, limit: int = 10) -> List[Dict[str, object]]:
        external_items = self._fetch_external_macro_events(limit=limit)
        ibkr_items = self.fetch_ibkr_macro_events(limit=limit)
        return self._merge_news_items(external_items, ibkr_items, limit=max(limit, self.config.ibkr_headline_limit))

    def _fetch_external_macro_events(self, limit: int = 10) -> List[Dict[str, object]]:
        params = self._article_params(
            keywords=["CPI", "FOMC", "Fed speech", "Federal Reserve", "geopolitical", "sanctions"],
            limit=limit,
            sort_by="date",
            extra={"lang": "eng"},
        )
        payload = self._request(self.config.macro_url, params)
        return self._parse_articles(payload)

    def fetch_ibkr_news_providers(self) -> List[Dict[str, str]]:
        if not self.config.ibkr_news_enabled or self.broker is None:
            return []
        return self.broker.get_news_providers()

    def fetch_ibkr_news_by_symbol(self, symbol: str, limit: int = 10) -> List[Dict[str, object]]:
        return self._fetch_ibkr_news(symbol, self.config.ibkr_symbol_providers, limit=limit)

    def fetch_ibkr_macro_events(self, limit: int = 10) -> List[Dict[str, object]]:
        macro_items: List[Dict[str, object]] = []
        seen: set[str] = set()
        for proxy_symbol in ("SPY", "QQQ"):
            for item in self._fetch_ibkr_news(proxy_symbol, self.config.ibkr_macro_providers, limit=limit):
                key = self._headline_key(item)
                if key not in seen:
                    seen.add(key)
                    macro_items.append(item)
        return macro_items

    def fetch_earnings_calendar(self, symbols: Iterable[str], days_ahead: int = 14) -> List[Dict[str, object]]:
        symbol_list = [symbol for symbol in symbols if symbol and SETTINGS.symbol_security_type(symbol) == "STK"]
        earnings_events: List[Dict[str, object]] = []
        for symbol in symbol_list:
            params = self._article_params(
                keywords=[symbol, "earnings"],
                limit=max(3, days_ahead),
                sort_by="date",
                extra={"lang": "eng"},
            )
            payload = self._request(self.config.earnings_url, params)
            for item in self._parse_articles(payload):
                headline = str(item.get("headline", ""))
                if "earnings" in headline.lower():
                    earnings_events.append({"symbol": symbol, "headline": headline, "raw": item})
        return earnings_events

    def _article_params(
        self,
        keywords: List[str],
        limit: int,
        sort_by: str,
        extra: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        params: Dict[str, object] = {
            "resultType": "articles",
            "articlesSortBy": sort_by,
            "keywordOper": "or",
            "apiKey": self.config.api_key,
        }
        for keyword in keywords:
            if keyword:
                params.setdefault("keyword", [])
                params["keyword"].append(keyword)  # type: ignore[index]
        if limit > 0:
            params["articlesCount"] = limit
        if extra:
            params.update(extra)
        return params

    def _fetch_ibkr_news(self, symbol: str, provider_codes: Sequence[str], limit: int = 10) -> List[Dict[str, object]]:
        if not self.config.ibkr_news_enabled or self.broker is None or not provider_codes:
            return []

        items = self.broker.get_historical_news(
            symbol=symbol,
            provider_codes=list(provider_codes),
            total_results=min(limit, self.config.ibkr_headline_limit),
            lookback_hours=self.config.ibkr_lookback_hours,
        )
        normalized: List[Dict[str, object]] = []
        for item in items:
            headline = str(item.get("headline", ""))
            provider_code = str(item.get("provider_code", "")) or str(item.get("source", ""))
            normalized.append(
                {
                    "headline": headline,
                    "published_at": str(item.get("published_at", "")),
                    "source": provider_code,
                    "url": str(item.get("url", "")),
                    "provider_code": provider_code,
                    "source_type": "ibkr",
                    "raw": item,
                }
            )
        return normalized

    def _request(self, url: str, params: Dict[str, object]) -> object:
        if not self.config.api_key:
            LOGGER.info("NEWS_API_KEY is not configured. News requests will return empty results.")
            return {"articles": {"results": []}}

        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.config.request_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            safe_params = {k: v for k, v in params.items() if k != "apiKey"}
            LOGGER.exception("News API request failed for url=%s params=%s", url, safe_params)
            return {"articles": {"results": []}}

    @staticmethod
    def _parse_articles(payload: object) -> List[Dict[str, object]]:
        articles: object = []
        if isinstance(payload, dict):
            raw_articles = payload.get("articles", [])
            if isinstance(raw_articles, dict):
                articles = raw_articles.get("results", [])
            else:
                articles = raw_articles
        elif isinstance(payload, list):
            articles = payload

        normalized: List[Dict[str, object]] = []
        if not isinstance(articles, list):
            return normalized

        for article in articles:
            if not isinstance(article, dict):
                continue
            source = article.get("source", {})
            normalized.append(
                {
                    "headline": article.get("title") or article.get("headline") or "",
                    "published_at": article.get("dateTime") or article.get("publishedAt") or "",
                    "source": source.get("title", "") if isinstance(source, dict) else str(source),
                    "url": article.get("url") or article.get("link") or "",
                    "provider_code": "",
                    "source_type": "external",
                    "raw": article,
                }
            )
        return normalized

    def _merge_news_items(
        self,
        external_items: List[Dict[str, object]],
        ibkr_items: List[Dict[str, object]],
        *,
        limit: int,
    ) -> List[Dict[str, object]]:
        ordered = OrderedDict()
        for item in ibkr_items + external_items:
            key = self._headline_key(item)
            if key and key not in ordered:
                ordered[key] = item
        merged = list(ordered.values())
        return merged[:limit]

    @staticmethod
    def _headline_key(item: Dict[str, object]) -> str:
        headline = str(item.get("headline", "")).strip().lower()
        if not headline:
            return ""
        headline = re.sub(r"[^a-z0-9\s]+", " ", headline)
        return re.sub(r"\s+", " ", headline).strip()
