"""Central configuration and logging utilities."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


BASE_DIR = Path(__file__).resolve().parents[1]
MEMORY_DIR = BASE_DIR / "memory"
LOG_DIR = MEMORY_DIR / "runtime"

load_dotenv(BASE_DIR / ".env", override=False)


def _csv_env(name: str, default: str) -> List[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class BrokerConfig:
    host: str = os.getenv("IBKR_HOST", "127.0.0.1")
    port: int = int(os.getenv("IBKR_PORT", "7497"))
    client_id: int = int(os.getenv("IBKR_CLIENT_ID", "1"))
    reconnect_retries: int = int(os.getenv("IBKR_RECONNECT_RETRIES", "5"))
    reconnect_delay_seconds: int = int(os.getenv("IBKR_RECONNECT_DELAY_SECONDS", "5"))
    market_data_timeout_seconds: int = int(os.getenv("IBKR_MARKET_DATA_TIMEOUT_SECONDS", "10"))


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS", "0.02"))
    max_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    max_open_risk_pct: float = float(os.getenv("MAX_OPEN_RISK", "0.03"))
    min_reward_risk_ratio: float = float(os.getenv("MIN_REWARD_RISK", "3.0"))
    max_spread_pct: float = float(os.getenv("MAX_SPREAD_PCT", "0.003"))
    max_position_value: float = float(os.getenv("MAX_POSITION_VALUE", "25000"))
    calculated_stop_pct: float = float(os.getenv("CALCULATED_STOP_PCT", "0.0015"))
    max_stop_vs_calculated_multiplier: float = float(os.getenv("MAX_STOP_VS_CALCULATED_MULTIPLIER", "1.2"))
    first_unstable_minutes: int = int(os.getenv("FIRST_UNSTABLE_MINUTES", "5"))


@dataclass(frozen=True)
class StrategyConfig:
    min_avg_volume: int = int(os.getenv("MIN_AVG_VOLUME", "500000"))
    lookback_bars: int = int(os.getenv("LOOKBACK_BARS", "60"))
    level_tolerance_pct: float = float(os.getenv("LEVEL_TOLERANCE_PCT", "0.0025"))
    consolidation_window: int = int(os.getenv("CONSOLIDATION_WINDOW", "20"))
    abnormal_range_multiplier: float = float(os.getenv("ABNORMAL_RANGE_MULTIPLIER", "2.0"))
    compression_range_multiplier: float = float(os.getenv("COMPRESSION_RANGE_MULTIPLIER", "0.6"))
    atr_travel_limit_pct: float = float(os.getenv("ATR_TRAVEL_LIMIT_PCT", "0.8"))
    minimum_technical_atr_pct: float = float(os.getenv("MIN_TECHNICAL_ATR_PCT", "0.01"))
    level_strength_threshold: float = float(os.getenv("LEVEL_STRENGTH_THRESHOLD", "4.0"))


@dataclass(frozen=True)
class TradingHours:
    timezone: str = os.getenv("TRADING_TIMEZONE", "America/New_York")
    market_open_hour: int = int(os.getenv("MARKET_OPEN_HOUR", "9"))
    market_open_minute: int = int(os.getenv("MARKET_OPEN_MINUTE", "30"))
    market_close_hour: int = int(os.getenv("MARKET_CLOSE_HOUR", "16"))
    market_close_minute: int = int(os.getenv("MARKET_CLOSE_MINUTE", "0"))


@dataclass(frozen=True)
class NewsConfig:
    api_key: str = os.getenv("NEWS_API_KEY", "")
    base_url: str = os.getenv("NEWS_API_BASE_URL", "https://eventregistry.org/api/v1/article/getArticles")
    macro_url: str = os.getenv("NEWS_MACRO_URL", "https://eventregistry.org/api/v1/article/getArticles")
    earnings_url: str = os.getenv("NEWS_EARNINGS_URL", "https://eventregistry.org/api/v1/article/getArticles")
    request_timeout_seconds: int = int(os.getenv("NEWS_TIMEOUT_SECONDS", "10"))
    ibkr_news_enabled: bool = os.getenv("IBKR_NEWS_ENABLED", "true").lower() == "true"
    ibkr_symbol_providers: Tuple[str, ...] = tuple(
        item for item in _csv_env("IBKR_NEWS_SYMBOL_PROVIDERS", "BRFUPDN,DJ-N") if item
    )
    ibkr_macro_providers: Tuple[str, ...] = tuple(
        item for item in _csv_env("IBKR_NEWS_MACRO_PROVIDERS", "BRFG,DJ-RTG") if item
    )
    ibkr_headline_limit: int = int(os.getenv("IBKR_NEWS_HEADLINE_LIMIT", "10"))
    ibkr_lookback_hours: int = int(os.getenv("IBKR_NEWS_LOOKBACK_HOURS", "24"))
    high_risk_keywords: Tuple[str, ...] = (
        "earnings",
        "lawsuit",
        "downgrade",
        "investigation",
        "fraud",
        "offering",
        "bankruptcy",
    )
    macro_risk_keywords: Tuple[str, ...] = (
        "cpi",
        "fomc",
        "fed speech",
        "federal reserve",
        "powell",
        "geopolitical",
        "war",
        "sanctions",
    )
    critical_macro_keywords: Tuple[str, ...] = ("cpi", "fomc", "fed speech", "federal reserve", "powell")
    macro_min_match_count: int = int(os.getenv("NEWS_MACRO_MIN_MATCH_COUNT", "2"))
    default_macro_query: str = os.getenv(
        "NEWS_MACRO_QUERY",
        "CPI OR FOMC OR \"Fed speech\" OR \"Federal Reserve\" OR geopolitical OR sanctions",
    )


@dataclass(frozen=True)
class PathsConfig:
    trade_log: Path = MEMORY_DIR / "TRADE_LOG.md"
    research_log: Path = MEMORY_DIR / "RESEARCH_LOG.md"
    levels_log: Path = MEMORY_DIR / "LEVELS_LOG.md"
    weekly_log: Path = MEMORY_DIR / "WEEKLY_LOG.md"
    weekly_review_log: Path = MEMORY_DIR / "WEEKLY_REVIEW.md"
    strategy_doc: Path = MEMORY_DIR / "TRADING_STRATEGY.md"
    reports_dir: Path = MEMORY_DIR / "reports"
    runtime_dir: Path = LOG_DIR
    state_file: Path = LOG_DIR / "state.json"


@dataclass(frozen=True)
class Settings:
    app_name: str = "ibkr-gerchik-bot"
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    dry_run_mode: bool = os.getenv("DRY_RUN_MODE", "true").lower() == "true"
    auto_git_push: bool = os.getenv("AUTO_GIT_PUSH", "false").lower() == "true"
    git_branch: str = os.getenv("WORKFLOW_GIT_BRANCH", "Test")
    account_currency: str = os.getenv("ACCOUNT_CURRENCY", "USD")
    symbols: List[str] = field(default_factory=lambda: _csv_env("BOT_SYMBOLS", "AAPL,MSFT,NVDA,AMD,TSLA,META,AMZN,NFLX"))
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trading_hours: TradingHours = field(default_factory=TradingHours)
    news: NewsConfig = field(default_factory=NewsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    slack_webhook: str = os.getenv("SLACK_WEBHOOK", "")
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    slack_channel: str = os.getenv("SLACK_CHANNEL", "")
    premarket_levels_export_min_strength: float = float(os.getenv("PREMARKET_LEVELS_EXPORT_MIN_STRENGTH", "7.0"))


SETTINGS = Settings()


def ensure_directories() -> None:
    """Create runtime and memory directories expected by the application."""
    SETTINGS.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.paths.reports_dir.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure application logging once."""
    ensure_directories()
    logger = logging.getLogger(SETTINGS.app_name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(SETTINGS.paths.runtime_dir / "application.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


LOGGER = setup_logging()


def append_markdown_log(path: Path, heading: str, fields: Dict[str, object]) -> None:
    """Append a timestamped Markdown section to a log file."""
    ensure_directories()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {heading} ({datetime.now().isoformat(timespec='seconds')})\n")
        for key, value in fields.items():
            handle.write(f"- **{key}**: {value}\n")
