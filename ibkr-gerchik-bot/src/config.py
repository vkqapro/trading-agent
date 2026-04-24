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
except ImportError:  # pragma: no cover - only used before dependency installation.
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
    client_id: int = int(os.getenv("IBKR_CLIENT_ID", "101"))
    reconnect_retries: int = 5
    reconnect_delay_seconds: int = 5
    market_data_timeout_seconds: int = 10


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01
    max_daily_loss_pct: float = 0.02
    max_positions: int = 5
    max_open_risk_pct: float = 0.03
    min_reward_risk_ratio: float = 2.0
    max_spread_pct: float = 0.003


@dataclass(frozen=True)
class StrategyConfig:
    min_avg_volume: int = 500_000
    lookback_bars: int = 60
    level_tolerance_pct: float = 0.0025
    rejection_wick_ratio: float = 1.2
    consolidation_window: int = 20
    third_touch_min_spacing: int = 3


@dataclass(frozen=True)
class TradingHours:
    timezone: str = "America/New_York"
    premarket: Tuple[str, str] = ("08:00", "09:25")
    market_open: Tuple[str, str] = ("09:30", "10:30")
    intraday: Tuple[str, str] = ("10:30", "15:30")
    end_of_day: Tuple[str, str] = ("15:45", "16:15")


@dataclass(frozen=True)
class NewsConfig:
    api_key: str = os.getenv("NEWS_API_KEY", "")
    base_url: str = os.getenv("NEWS_API_BASE_URL", "https://eventregistry.org/api/v1/article/getArticles")
    macro_url: str = os.getenv("NEWS_MACRO_URL", "https://eventregistry.org/api/v1/article/getArticles")
    earnings_url: str = os.getenv("NEWS_EARNINGS_URL", "https://eventregistry.org/api/v1/article/getArticles")
    request_timeout_seconds: int = int(os.getenv("NEWS_TIMEOUT_SECONDS", "10"))
    high_risk_keywords: Tuple[str, ...] = (
        "earnings",
        "lawsuit",
        "downgrade",
        "investigation",
        "fraud",
        "offering",
        "bankruptcy",
        "guidance cut",
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
    critical_macro_keywords: Tuple[str, ...] = (
        "cpi",
        "fomc",
        "fed speech",
        "federal reserve",
        "powell",
    )
    macro_min_match_count: int = int(os.getenv("NEWS_MACRO_MIN_MATCH_COUNT", "2"))
    default_macro_query: str = os.getenv(
        "NEWS_MACRO_QUERY",
        "CPI OR FOMC OR \"Fed speech\" OR \"Federal Reserve\" OR geopolitical OR sanctions",
    )


@dataclass(frozen=True)
class PathsConfig:
    trade_log: Path = MEMORY_DIR / "TRADE_LOG.md"
    research_log: Path = MEMORY_DIR / "RESEARCH_LOG.md"
    weekly_log: Path = MEMORY_DIR / "WEEKLY_LOG.md"
    runtime_dir: Path = LOG_DIR
    state_file: Path = LOG_DIR / "state.json"


@dataclass(frozen=True)
class Settings:
    app_name: str = "ibkr-gerchik-bot"
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    dry_run_mode: bool = os.getenv("DRY_RUN_MODE", "true").lower() == "true"
    account_currency: str = "USD"
    symbols: List[str] = field(
        default_factory=lambda: _csv_env(
            "BOT_SYMBOLS",
            "AAPL,MSFT,NVDA,AMD,TSLA,META,AMZN,NFLX",
        )
    )
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trading_hours: TradingHours = field(default_factory=TradingHours)
    news: NewsConfig = field(default_factory=NewsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    slack_webhook: str = os.getenv("SLACK_WEBHOOK", "")


SETTINGS = Settings()


def ensure_directories() -> None:
    """Create runtime directories expected by the application."""
    SETTINGS.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure console and file logging once for the application."""
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
